"""The second one-time cleanup removes cookies and super-audit credentials, keeps rows."""

from datetime import datetime

OLD = datetime(2026, 11, 19, 12, 0)
NEW = datetime(2026, 11, 21, 12, 0)
SEED = {"seeded_by": "audit-scrub-2-test"}


def _cleanup(db):
    db.admin_logs.delete_many({"$or": [SEED, {"event": "admin_log_secret_scrub_2"}]})
    db.super_audit_logs.delete_many(SEED)
    db.maintenance_markers.delete_many({})


def test_second_scrub_removes_cookies_and_super_audit_credentials(db, monkeypatch):
    from mielenosoitukset_fi.scripts import scrub_audit_log_secrets as scrub

    monkeypatch.delenv("ADMIN_LOG_SCRUB_DISABLED", raising=False)
    monkeypatch.setattr(scrub, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(scrub, "TARGETS", tuple(
        (c, datetime(2026, 11, 18), f) for c, _, f in scrub.TARGETS))
    _cleanup(db)
    db.admin_logs.insert_many([
        {**SEED, "n": "old", "timestamp": OLD, "request": {"path": "/admin/x", "cookies": {"session": "secret"}}},
        {**SEED, "n": "new", "timestamp": NEW, "request": {"cookies": {"keep": "1"}}},
    ])
    db.super_audit_logs.insert_many([
        {**SEED, "n": "old", "timestamp": OLD, "event": "admin:x",
         "payload": {"request": {"path": "/admin/x", "environ": {"HTTP_COOKIE": "session=secret", "HTTP_USER_AGENT": "UA/1"},
                                 "headers": "Cookie: session=secret", "cookies": {"session": "secret"}},
                     "user": {"username": "admin", "password_hash": "pbkdf2:secret"}}},
        {**SEED, "n": "new", "timestamp": NEW, "payload": {"request": {"headers": "kept"}}},
    ])
    try:
        assert scrub.main(db=db, dry_run=True)["rows_changed"] == {"admin_logs": 0, "super_audit_logs": 0}
        result = scrub.main(db=db)
        assert result["rows_changed"] == {"admin_logs": 1, "super_audit_logs": 1}

        assert db.admin_logs.find_one({**SEED, "n": "old"})["request"] == {"path": "/admin/x"}
        assert db.admin_logs.find_one({**SEED, "n": "new"})["request"]["cookies"] == {"keep": "1"}
        old = db.super_audit_logs.find_one({**SEED, "n": "old"})
        assert old["payload"] == {"request": {"path": "/admin/x", "user_agent": "UA/1"}, "user": {"username": "admin"}}
        assert db.super_audit_logs.find_one({**SEED, "n": "new"})["payload"]["request"]["headers"] == "kept"
        assert db.super_audit_logs.count_documents(SEED) == 2
        assert db.admin_logs.find_one({"event": "admin_log_secret_scrub_2"})
        assert scrub.main(db=db) == {"already_done": True}
    finally:
        _cleanup(db)


def test_second_scrub_is_registered_and_switchable(db, monkeypatch):
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP
    from mielenosoitukset_fi.scripts import scrub_audit_log_secrets as scrub

    assert "scrub_audit_log_secrets" in JOB_DEFINITION_MAP
    monkeypatch.setenv("ADMIN_LOG_SCRUB_DISABLED", "1")
    assert scrub.main(db=db) == {"disabled": True}
