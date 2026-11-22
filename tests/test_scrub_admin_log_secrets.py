"""The one-time admin log cleanup removes only credential fields from old rows."""

from datetime import datetime

OLD = datetime(2026, 11, 19, 12, 0)
NEW = datetime(2026, 11, 21, 12, 0)
SEED = {"seeded_by": "admin-log-scrub-test"}


def _cleanup(db):
    db.admin_logs.delete_many({"$or": [SEED, {"event": "admin_log_secret_scrub"}]})
    db.maintenance_markers.delete_many({})


def _seed(db):
    _cleanup(db)
    db.admin_logs.insert_many([
        {**SEED, "n": "old", "timestamp": OLD,
         "request": {"method": "GET", "path": "/admin/dashboard", "remote_addr": "1.2.3.4",
                     "environ": {"HTTP_COOKIE": "session=secret", "HTTP_USER_AGENT": "UA/1"},
                     "headers": "Cookie: session=secret"},
         "user": {"_id": "u1", "username": "admin", "password_hash": "pbkdf2:secret"}},
        {**SEED, "n": "old-new-shape", "timestamp": OLD,
         "request": {"method": "POST", "path": "/admin/x", "user_agent": "UA/2"},
         "user": {"username": "admin"}},
        {**SEED, "n": "new", "timestamp": NEW,
         "request": {"method": "GET", "path": "/admin/y", "headers": "kept"}},
    ])


def test_scrub_removes_credentials_keeps_rows_and_runs_once(db, monkeypatch):
    from mielenosoitukset_fi.scripts import scrub_admin_log_secrets as scrub

    monkeypatch.delenv("ADMIN_LOG_SCRUB_DISABLED", raising=False)
    _seed(db)
    try:
        assert scrub.main(db=db, dry_run=True) == {"dry_run": True, "matching_rows": 1}
        assert "environ" in db.admin_logs.find_one({**SEED, "n": "old"})["request"]

        result = scrub.main(db=db)
        assert result == {"dry_run": False, "matching_rows": 1, "changed": 1, "remaining": 0}

        old = db.admin_logs.find_one({**SEED, "n": "old"})
        assert old["request"] == {"method": "GET", "path": "/admin/dashboard",
                                  "remote_addr": "1.2.3.4", "user_agent": "UA/1"}
        assert old["user"] == {"_id": "u1", "username": "admin"}
        assert db.admin_logs.find_one({**SEED, "n": "old-new-shape"})["request"]["user_agent"] == "UA/2"
        assert db.admin_logs.find_one({**SEED, "n": "new"})["request"]["headers"] == "kept"
        assert db.admin_logs.count_documents(SEED) == 3

        audit = db.admin_logs.find_one({"event": "admin_log_secret_scrub"})
        assert audit["details"]["changed"] == 1
        assert db.maintenance_markers.find_one({"_id": scrub.MARKER_ID})["remaining"] == 0
        assert scrub.main(db=db) == {"already_done": True}
    finally:
        _cleanup(db)


def test_scrub_can_be_switched_off(db, monkeypatch):
    from mielenosoitukset_fi.scripts import scrub_admin_log_secrets as scrub

    monkeypatch.setenv("ADMIN_LOG_SCRUB_DISABLED", "1")
    assert scrub.main(db=db) == {"disabled": True}


def test_scrub_job_is_registered():
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP

    assert "scrub_admin_log_secrets" in JOB_DEFINITION_MAP


def test_form_check_counts_stored_forms_without_values(db):
    from datetime import datetime
    from mielenosoitukset_fi.scripts import diagnose_admin_log_forms as check

    db.maintenance_markers.delete_many({"_id": check.MARKER_ID})
    db.admin_logs.delete_many({"seeded_by": "form-check-test"})
    day = datetime(2026, 3, 15, 10, 0)
    base = {"seeded_by": "form-check-test", "timestamp": day}
    db.admin_logs.insert_many([
        {**base, "request": {"method": "POST", "path": "/admin/organization/edit/abc", "form": {"name": "Salainen nimi"}}},
        {**base, "request": {"method": "POST", "path": "/admin/demo/edit/abc"}},
        {**base, "request": {"method": "GET", "path": "/admin/"}},
    ])
    result = check.main(db=db)
    march = [s for s in result["samples"] if s["day"] == "2026-03-15"][0]
    assert march["post_with_form"] == 1 and march["org_edit_with_form"] == 1
    assert march["org_form_field_names"] == ["name"]
    assert "Salainen nimi" not in str(result)
    assert check.main(db=db) == {"already_done": True}
    db.maintenance_markers.delete_many({"_id": check.MARKER_ID})
    db.admin_logs.delete_many({"$or": [{"seeded_by": "form-check-test"}, {"event": "admin_log_form_check"}]})
