"""Saves that change nothing must not create demo versions (or audit events)."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from mielenosoitukset_fi.demonstrations.audit import actor_label, is_noop_change


def test_noop_change_ignores_only_bookkeeping_timestamps():
    old = {"_id": 1, "title": "A", "last_modified": datetime(2026, 1, 1)}
    assert is_noop_change(old, {**old, "last_modified": datetime(2026, 11, 20)})
    assert is_noop_change(old, {**old, "updated_at": datetime(2026, 11, 20)})
    assert not is_noop_change(old, {**old, "title": "B"})
    assert not is_noop_change(None, old)
    assert not is_noop_change(old, None)


def test_versions_without_a_user_name_the_job():
    assert actor_label({"user_id": None, "username": "[JOB] hide_past"}) == "[JOB] hide_past"
    assert actor_label({"user_id": "abc", "username": "x"}) == "abc"
    assert actor_label({}) == "unknown"


def test_job_recorder_skips_timestamp_only_saves(monkeypatch):
    from mielenosoitukset_fi.background_jobs import audit as job_audit

    calls = []
    monkeypatch.setattr(job_audit, "record_demo_change", lambda **kw: calls.append(kw))
    recorder = job_audit.DemoChangeRecorder("hide_past", ObjectId())
    before = {"_id": ObjectId(), "title": "A", "in_past": True, "last_modified": datetime(2026, 1, 1)}
    recorder.record(before, {**before, "last_modified": datetime(2026, 11, 20)}, "update_one")
    assert calls == []
    recorder.record(before, {**before, "in_past": False}, "update_one")
    assert len(calls) == 1 and calls[0]["actor"]["username"] == "[JOB] hide_past"


@pytest.mark.integration
def test_hide_past_leaves_already_marked_demos_alone(db, seeded_data, monkeypatch):
    from mielenosoitukset_fi.scripts import in_past

    # The script loads its own DatabaseManager through importlib; point it at
    # the test database so the test does not depend on that module's state.
    monkeypatch.setattr(in_past, "db", db)

    stamp = datetime(2025, 1, 1)
    past = {
        "title": "Mennyt", "date": "2025-01-01", "approved": True, "in_past": True,
        "last_modified": stamp, "aliases": [],
    }
    unmarked = {**past, "title": "Merkitsemätön", "in_past": False}
    past_id = db.demonstrations.insert_one(past).inserted_id
    unmarked_id = db.demonstrations.insert_one(unmarked).inserted_id
    try:
        in_past.hide_past()
        assert db.demonstrations.find_one({"_id": past_id})["last_modified"] == stamp
        marked = db.demonstrations.find_one({"_id": unmarked_id})
        assert marked["in_past"] is True and marked["last_modified"] != stamp
    finally:
        db.demonstrations.delete_many({"_id": {"$in": [past_id, unmarked_id]}})


@pytest.mark.integration
def test_history_page_hides_empty_versions_and_names_the_job(admin_client, db, seeded_data):
    demo_id = str(seeded_data["demo_id"])
    base = {"_id": ObjectId(demo_id), "title": "Otsikko", "last_modified": datetime(2026, 1, 1)}
    now = datetime.now(timezone.utc)
    job_actor = {"user_id": None, "username": "[JOB] hide_past", "source": "background_job"}
    db.demo_edit_history.insert_many([
        {"demo_id": demo_id, "edited_by": "unknown", "edited_at": now - timedelta(days=2),
         "old_demo": base, "new_demo": {**base, "last_modified": datetime(2026, 2, 1)}, "actor": job_actor},
        {"demo_id": demo_id, "edited_by": "unknown", "edited_at": now - timedelta(days=1),
         "old_demo": base, "new_demo": {**base, "title": "Uusi otsikko"}, "actor": job_actor},
    ])
    page = admin_client.get(f"/admin/demo/edit_history/{demo_id}").get_data(as_text=True)
    assert page.count("Taustatyö: hide_past") == 1
    assert "Aiempi versio" in page or "Nykyinen versio" in page
