"""The 90-day IP retention job unsets IP and User-Agent and never deletes suggestions."""

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=91)
FRESH = NOW - timedelta(days=89)


def _seed(db):
    for name in ("demo_suggestions", "org_edit_suggestions", "cases"):
        db[name].delete_many({"seeded_by": "ip-purge-test"})
    marker = {"seeded_by": "ip-purge-test"}
    db.demo_suggestions.insert_many([
        {**marker, "n": "old", "created_at": OLD, "ip": "1.2.3.4", "user_agent": "UA", "reporter_email": "a@example.test"},
        {**marker, "n": "fresh", "created_at": FRESH, "ip": "1.2.3.4", "user_agent": "UA"},
    ])
    db.org_edit_suggestions.insert_many([
        {**marker, "n": "old", "created_at": OLD, "meta": {"ip": "1.2.3.4", "user_agent": "UA", "submitter_email": "a@example.test"}},
        {**marker, "n": "fresh", "created_at": FRESH, "meta": {"ip": "1.2.3.4", "user_agent": "UA"}},
    ])
    db.cases.insert_many([
        {**marker, "n": "old", "suggestion": {"created_at": OLD, "meta": {"ip": "1.2.3.4", "user_agent": "UA"}, "fields": {"name": "x"}}},
        {**marker, "n": "fresh", "suggestion": {"created_at": FRESH, "meta": {"ip": "1.2.3.4", "user_agent": "UA"}}},
        {**marker, "n": "other", "created_at": OLD, "error_report": {"ip": "9.9.9.9"}},
    ])


def _cleanup(db):
    for name in ("demo_suggestions", "org_edit_suggestions", "cases"):
        db[name].delete_many({"seeded_by": "ip-purge-test"})


def test_purge_unsets_only_old_ips_and_keeps_documents(db):
    from mielenosoitukset_fi.scripts import purge_suggestion_ips as purge

    _seed(db)
    try:
        dry = purge.purge(db, now=NOW, dry_run=True)
        assert dry["counts"]["demo_suggestions"] >= 1
        assert db.demo_suggestions.find_one({"n": "old", "seeded_by": "ip-purge-test"})["ip"] == "1.2.3.4"

        purge.purge(db, now=NOW)
        marker = {"seeded_by": "ip-purge-test"}
        old = db.demo_suggestions.find_one({"n": "old", **marker})
        assert "ip" not in old and "user_agent" not in old and old["reporter_email"] == "a@example.test"
        assert db.demo_suggestions.find_one({"n": "fresh", **marker})["ip"] == "1.2.3.4"

        old = db.org_edit_suggestions.find_one({"n": "old", **marker})
        assert "ip" not in old["meta"] and "user_agent" not in old["meta"] and old["meta"]["submitter_email"] == "a@example.test"
        assert db.org_edit_suggestions.find_one({"n": "fresh", **marker})["meta"]["ip"] == "1.2.3.4"

        old = db.cases.find_one({"n": "old", **marker})
        assert old["suggestion"]["meta"] == {} and old["suggestion"]["fields"] == {"name": "x"}
        assert db.cases.find_one({"n": "fresh", **marker})["suggestion"]["meta"]["ip"] == "1.2.3.4"
        assert db.cases.find_one({"n": "other", **marker})["error_report"]["ip"] == "9.9.9.9"

        assert db.demo_suggestions.count_documents(marker) == 2
        assert db.org_edit_suggestions.count_documents(marker) == 2
        assert db.cases.count_documents(marker) == 3
    finally:
        _cleanup(db)


def test_scheduled_run_does_nothing_without_the_flag(db, monkeypatch):
    from mielenosoitukset_fi.scripts import purge_suggestion_ips as purge

    monkeypatch.delenv("SUGGESTION_IP_PURGE_ENABLED", raising=False)
    assert purge.main(db=db) == {"enabled": False, "changed": 0}
    monkeypatch.setenv("SUGGESTION_IP_PURGE_ENABLED", "1")
    assert purge.main(db=db, dry_run=True)["dry_run"] is True


def test_purge_job_is_registered():
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP

    assert "purge_suggestion_ips" in JOB_DEFINITION_MAP
