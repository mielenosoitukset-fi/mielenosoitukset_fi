"""Inactive accounts (#1174): warn at 24 months, final warning, delete; exemptions and logins cancel."""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

NOW = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
MARK = {"seeded_by": "inactive-account-test"}
COLLECTIONS = ("users", "memberships", "cases", "security_cases", "email_queue")


def _days(n):
    return NOW - timedelta(days=n)


def _cleanup(db):
    for name in COLLECTIONS:
        db[name].delete_many(MARK)
    db.users.delete_many({"username": {"$regex": "^inactive-test-"}})


def _user(db, name, **fields):
    doc = {"username": f"inactive-test-{name}", "email": f"{name}@example.test", "role": "user",
           "last_login": _days(800), **MARK, **fields}
    doc["_id"] = ObjectId()
    db.users.insert_one(doc)
    return doc


def _run(db, now=NOW, dry_run=False):
    from mielenosoitukset_fi.utils import inactive_accounts

    return inactive_accounts.run(db, now=now, dry_run=dry_run, secret="test-secret")


def _mails(db):
    return sorted(m["_id"].split(":")[-1] for m in db.email_queue.find({"_id": {"$regex": "^inactive-account:"}}))


def test_timeline_first_final_then_delete(db):
    _cleanup(db)
    user = _user(db, "timeline")
    try:
        dry = _run(db, dry_run=True)
        assert dry["counts"]["first_warning"] >= 1 and not db.email_queue.count_documents({"_id": {"$regex": f"inactive-account:{user['_id']}"}})

        assert _run(db)["counts"]["first_warning"] >= 1
        assert db.users.find_one({"_id": user["_id"]})["inactivity_notice"]["first_at"]
        assert db.email_queue.count_documents({"_id": {"$regex": f"inactive-account:{user['_id']}:.*:first"}}) == 1

        _run(db, now=NOW + timedelta(days=22))
        assert "final_at" not in db.users.find_one({"_id": user["_id"]})["inactivity_notice"]
        _run(db, now=NOW + timedelta(days=23))
        assert db.email_queue.count_documents({"_id": {"$regex": f"inactive-account:{user['_id']}:.*:final"}}) == 1

        _run(db, now=NOW + timedelta(days=29))
        assert db.users.find_one({"_id": user["_id"]})
        _run(db, now=NOW + timedelta(days=31))
        assert db.users.find_one({"_id": user["_id"]}) is None
    finally:
        _cleanup(db)


def test_login_after_warning_cancels(db):
    _cleanup(db)
    user = _user(db, "returns")
    try:
        _run(db)
        db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": NOW + timedelta(days=5)}})
        _run(db, now=NOW + timedelta(days=40))
        doc = db.users.find_one({"_id": user["_id"]})
        assert doc is not None and "inactivity_notice" not in doc
    finally:
        _cleanup(db)


def test_recent_and_exempt_accounts_are_left_alone(db):
    _cleanup(db)
    recent = _user(db, "recent", last_login=_days(10))
    admin = _user(db, "admin", role="admin")
    extra = _user(db, "extra", global_permissions=["VIEW_DEMO"])
    no_mail = _user(db, "nomail", email="")
    owner = _user(db, "owner")
    db.memberships.insert_one({**MARK, "user_id": owner["_id"], "role": "owner", "organization_id": ObjectId()})
    case_user = _user(db, "case")
    db.cases.insert_one({**MARK, "submitter_id": case_user["_id"], "type": "new_demo", "meta": {}})
    closed_user = _user(db, "closedcase")
    db.cases.insert_one({**MARK, "submitter_id": closed_user["_id"], "type": "new_demo", "meta": {"closed": True}})
    secure = _user(db, "secure")
    db.security_cases.insert_one({**MARK, "target_id": secure["_id"], "status": "suspended"})
    try:
        _run(db)
        for user in (recent, admin, extra, no_mail, owner, case_user, secure):
            assert "inactivity_notice" not in db.users.find_one({"_id": user["_id"]})
        assert "inactivity_notice" in db.users.find_one({"_id": closed_user["_id"]})
        _run(db, now=NOW + timedelta(days=200))
        assert all(db.users.find_one({"_id": u["_id"]}) for u in (recent, admin, extra, no_mail, owner, case_user, secure))
    finally:
        _cleanup(db)


def test_never_logged_in_counts_from_creation(db):
    _cleanup(db)
    old = ObjectId.from_datetime(_days(800))
    db.users.insert_one({"_id": old, "username": "inactive-test-never", "email": "never@example.test", "role": "user", **MARK})
    try:
        assert _run(db)["counts"]["first_warning"] >= 1
        assert "inactivity_notice" in db.users.find_one({"_id": old})
    finally:
        _cleanup(db)


def test_job_does_nothing_without_flag(db, monkeypatch):
    from mielenosoitukset_fi.scripts import purge_inactive_accounts as job

    monkeypatch.delenv("INACTIVE_ACCOUNT_DELETION_ENABLED", raising=False)
    assert job.main(db=db) == {"enabled": False, "changed": 0}
