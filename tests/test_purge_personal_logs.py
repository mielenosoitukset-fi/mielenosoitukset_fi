"""Retention for personal data in logs (#596): deletes and unsets only what is old enough."""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
MARK = {"seeded_by": "personal-log-test"}
COLLECTIONS = ("login_logs", "malicious_reports", "demo_submission_errors", "demo_reminders", "security_cases")


def _days(n):
    return NOW - timedelta(days=n)


def _cleanup(db):
    for name in COLLECTIONS:
        db[name].delete_many(MARK)


def _seed(db, protected_id):
    _cleanup(db)
    db.login_logs.insert_many([
        {**MARK, "n": "old", "user_id": ObjectId(), "timestamp": _days(366), "ip": "1.2.3.4"},
        {**MARK, "n": "fresh", "user_id": ObjectId(), "timestamp": _days(364), "ip": "1.2.3.4"},
        {**MARK, "n": "protected", "user_id": protected_id, "timestamp": _days(400), "ip": "1.2.3.4"},
    ])
    db.malicious_reports.insert_many([
        {**MARK, "n": "old", "reported_at": _days(366), "ip": "1.2.3.4"},
        {**MARK, "n": "fresh", "reported_at": _days(364), "ip": "1.2.3.4"},
    ])
    db.demo_submission_errors.insert_many([
        {**MARK, "n": "gone", "created_at": _days(91), "form_snapshot": {"email": "a@example.test"}},
        {**MARK, "n": "scrubbed", "created_at": _days(40), "form_snapshot": {"email": "a@example.test"}, "error_code": "x"},
        {**MARK, "n": "fresh", "created_at": _days(10), "form_snapshot": {"email": "a@example.test"}},
    ])
    db.demo_reminders.insert_many([
        {**MARK, "n": "old", "created_at": _days(91), "user_email": "a@example.test", "user_ip": "1.2.3.4", "user_agent": "UA"},
        {**MARK, "n": "fresh", "created_at": _days(89), "user_email": "a@example.test", "user_ip": "1.2.3.4", "user_agent": "UA"},
    ])
    db.security_cases.insert_one({**MARK, "status": "suspended", "target_id": protected_id})


def test_purge_deletes_and_unsets_only_old_data(db):
    from mielenosoitukset_fi.scripts import purge_personal_logs as purge

    protected_id = ObjectId()
    _seed(db, protected_id)
    try:
        dry = purge.purge(db, now=NOW, dry_run=True)
        assert dry["counts"]["login_logs:365"] >= 1
        assert db.login_logs.count_documents(MARK) == 3

        purge.purge(db, now=NOW)
        names = lambda coll: {d["n"] for d in db[coll].find(MARK)}
        assert names("login_logs") == {"fresh", "protected"}
        assert names("malicious_reports") == {"fresh"}
        assert names("demo_submission_errors") == {"scrubbed", "fresh"}
        scrubbed = db.demo_submission_errors.find_one({"n": "scrubbed", **MARK})
        assert "form_snapshot" not in scrubbed and scrubbed["error_code"] == "x"
        assert "form_snapshot" in db.demo_submission_errors.find_one({"n": "fresh", **MARK})

        old = db.demo_reminders.find_one({"n": "old", **MARK})
        assert "user_ip" not in old and "user_agent" not in old and old["user_email"] == "a@example.test"
        assert db.demo_reminders.find_one({"n": "fresh", **MARK})["user_ip"] == "1.2.3.4"
    finally:
        _cleanup(db)


def test_closed_security_case_does_not_protect_login_rows(db):
    from mielenosoitukset_fi.scripts import purge_personal_logs as purge

    user_id = ObjectId()
    _cleanup(db)
    db.login_logs.insert_one({**MARK, "n": "old", "user_id": user_id, "timestamp": _days(400)})
    db.security_cases.insert_one({**MARK, "status": "restored", "target_id": user_id})
    try:
        purge.purge(db, now=NOW)
        assert db.login_logs.count_documents(MARK) == 0
    finally:
        _cleanup(db)


def test_scheduled_run_does_nothing_without_the_flag(db, monkeypatch):
    from mielenosoitukset_fi.scripts import purge_personal_logs as purge

    monkeypatch.delenv("PERSONAL_LOG_RETENTION_ENABLED", raising=False)
    assert purge.main(db=db) == {"enabled": False, "changed": 0}
    monkeypatch.setenv("PERSONAL_LOG_RETENTION_ENABLED", "1")
    assert purge.main(db=db, dry_run=True)["dry_run"] is True


def test_purge_job_is_registered():
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP

    assert "purge_personal_logs" in JOB_DEFINITION_MAP


def test_reports_reminder_cap_and_reset_visits(db):
    from mielenosoitukset_fi.scripts import purge_personal_logs as purge

    for name in ("reports", "demo_reminders", "password_changes"):
        db[name].delete_many(MARK)
    db.reports.insert_many([
        {**MARK, "n": "old", "date": _days(366), "ip": "1.2.3.4", "reporter_email": "a@example.test"},
        {**MARK, "n": "fresh", "date": _days(364), "ip": "1.2.3.4"},
    ])
    db.demo_reminders.insert_many([
        {**MARK, "n": "capped", "created_at": _days(731), "user_email": "a@example.test"},
        {**MARK, "n": "kept", "created_at": _days(729), "user_email": "a@example.test"},
    ])
    db.password_changes.insert_many([
        {**MARK, "n": "old_visit", "datetime": _days(31), "visit": True, "changed": False, "ip": "1.2.3.4"},
        {**MARK, "n": "fresh_visit", "datetime": _days(29), "visit": True, "changed": False},
        {**MARK, "n": "old_change", "datetime": _days(400), "visit": True, "changed": True},
    ])
    try:
        purge.purge(db, now=NOW)
        names = lambda coll: {d["n"] for d in db[coll].find(MARK)}
        assert names("reports") == {"fresh"}
        assert names("demo_reminders") == {"kept"}
        assert names("password_changes") == {"fresh_visit", "old_change"}
    finally:
        for name in ("reports", "demo_reminders", "password_changes"):
            db[name].delete_many(MARK)
