"""Organization and demonstration change history pages, built from audit_events."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin.audit_history import _field_changes, display_value, helsinki_time
from mielenosoitukset_fi.utils.audit_trail import REDACTED


def test_field_changes_flatten_nested_sets_and_unsets():
    changes = _field_changes({
        "op": "update",
        "change": [{"$set": {"name": "Uusi", "social": {"x": "@a"}}, "$unset": {"logo": ""}, "$push": {"tags": "t"}}],
    })
    assert changes["name"] == {"kind": "set", "value": "Uusi"}
    assert changes["social.x"] == {"kind": "set", "value": "@a"}
    assert changes["logo"]["kind"] == "unset"
    assert changes["tags"] == {"kind": "push", "value": "t"}


def test_display_hides_redacted_values_and_uses_helsinki_time():
    assert display_value(REDACTED) == "(piilotettu)"
    assert display_value(True) == "kyllä"
    assert helsinki_time(datetime(2026, 11, 20, 10, 0, 0)) == "20.11.2026 12:00:00"


def _event(org_id, at, op, change, actor, via=None):
    return {
        "v": 1, "at": at, "db": "x", "collection": "organizations", "op": op,
        "doc_ids": [org_id], "change": change, "actor": actor,
        "via": via or {"kind": "http", "service": "flask", "method": "POST", "rule": "/admin/organization/edit/<org_id>"},
        "outcome": {"ok": True, "n": 1},
    }


@pytest.mark.integration
def test_organization_history_shows_creator_and_each_change(admin_client, db, seeded_data):
    org_id = ObjectId(seeded_data["org_id"])
    admin_id = str(seeded_data["admin_id"])
    start = datetime.now(timezone.utc) - timedelta(days=1)
    db.audit_events.insert_many([
        _event(org_id, start, "insert", [{"_id": org_id, "name": "Vanha nimi", "email": "info@example.org"}],
               {"kind": "user", "user_id": admin_id}),
        _event(org_id, start + timedelta(hours=1), "update", [{"$set": {"name": "Uusi nimi"}}],
               {"kind": "job", "name": "merge_duplicates"}, via={"kind": "job", "name": "merge_duplicates"}),
    ])

    response = admin_client.get(f"/admin/organization/history/{org_id}")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Muutoshistoria" in page
    assert "Luotu" in page and "Muokattu" in page
    assert "Vanha nimi" in page and "Uusi nimi" in page
    assert "Taustatyö: merge_duplicates" in page
    assert "@admin" in page or "admin" in page

    view = admin_client.get(f"/admin/organization/view/{org_id}").get_data(as_text=True)
    assert f"/admin/organization/history/{org_id}" in view


@pytest.mark.integration
def test_real_write_appears_in_history(admin_client, db, seeded_data):
    org_id = ObjectId(seeded_data["org_id"])
    db.organizations.update_one({"_id": org_id}, {"$set": {"description": "Kirjattu kuvaus"}})
    page = admin_client.get(f"/admin/organization/history/{org_id}").get_data(as_text=True)
    assert "Kirjattu kuvaus" in page
    assert "Skripti" in page  # written outside a request: named by the process


@pytest.mark.integration
def test_history_requires_organization_edit_rights(user_client, seeded_data):
    response = user_client.get(f"/admin/organization/history/{seeded_data['org_id']}")
    assert response.status_code in (302, 403)


@pytest.mark.integration
def test_demo_history_lists_every_recorded_change(admin_client, db, seeded_data):
    demo_id = ObjectId(seeded_data["demo_id"])
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"title": "Audit-otsikko"}})
    page = admin_client.get(f"/admin/demo/edit_history/{demo_id}").get_data(as_text=True)
    assert "Kaikki kirjatut muutokset" in page
    assert "Audit-otsikko" in page


def _legacy_logs(db, org_id, now):
    other = str(ObjectId())
    db.admin_logs.delete_many({})
    db.admin_logs.insert_many([
        {"event": "organization_created", "timestamp": now - timedelta(days=30),
         "actor": {"id": "1", "username": "olivia", "email": "secret@example.test"}, "details": {"org_id": str(org_id)}},
        {"event": "organization_verified_changed", "timestamp": now - timedelta(days=5),
         "actor": {"id": "1", "username": "vanha_admin", "email": "secret@example.test"},
         "details": {"organization_id": str(org_id), "previous": False, "new": True}},
        {"event": "organization_update", "timestamp": now - timedelta(days=4),
         "actor": {"id": "1", "username": "vanha_admin"}, "details": {"org_id": str(org_id)}},
        {"event": "organization_update", "timestamp": now - timedelta(days=4),
         "actor": {"id": "1", "username": "toinen"}, "details": {"org_id": other}},
        {"event": "organization_view", "timestamp": now - timedelta(days=4),
         "actor": {"id": "1", "username": "katsoja"}, "details": {"org_id": str(org_id)}},
        {"event": "organization_update", "timestamp": now + timedelta(days=1),
         "actor": {"id": "1", "username": "tuleva"}, "details": {"org_id": str(org_id)}},
    ])


@pytest.mark.integration
def test_backfill_copies_old_org_events_once_and_history_shows_them(admin_client, db, seeded_data, monkeypatch):
    from mielenosoitukset_fi.scripts import backfill_org_history as backfill

    monkeypatch.delenv("ORG_HISTORY_BACKFILL_DISABLED", raising=False)
    org_id = ObjectId(seeded_data["org_id"])
    now = datetime.now(timezone.utc)
    db.audit_events.delete_many({})
    db.maintenance_markers.delete_many({"_id": backfill.MARKER_ID})
    db.audit_events.insert_one(_event(org_id, now - timedelta(hours=1), "update", [{"$set": {"name": "Nyt"}}],
                                      {"kind": "user", "user_id": str(seeded_data["admin_id"])}))
    _legacy_logs(db, org_id, now)

    assert backfill.main(db=db, dry_run=True)["events_seen"] == 4
    assert db.audit_events.count_documents({"legacy": True}) == 0

    result = backfill.main(db=db)["events"]
    assert result["written"] == 4 and result["no_org_id"] == 0
    assert db.audit_events.count_documents({"legacy": True, "collection": "organizations", "doc_ids": org_id}) == 3
    assert backfill.main(db=db) == {"already_done": True}
    db.maintenance_markers.delete_many({"_id": backfill.MARKER_ID})
    assert backfill.main(db=db)["events"]["already_there"] == 4  # idempotent
    assert db.audit_events.count_documents({"legacy": True, "collection": "organizations"}) == 4

    stored = db.audit_events.find_one({"source.id": str(db.admin_logs.find_one({"event": "organization_verified_changed"})["_id"])})
    assert "secret@example.test" not in str(stored) and "vanha_admin" not in str(stored)

    page = admin_client.get(f"/admin/organization/history/{org_id}").get_data(as_text=True)
    assert "Nyt" in page and "Vanha ylläpitoloki" in page and "verified" in page
    assert "toinen" not in page and "katsoja" not in page and "tuleva" not in page
    assert "secret@example.test" not in page
    assert "Muutoshistoria kirjataan" not in page  # creation is known now
    from mielenosoitukset_fi.admin.audit_history import first_event_at
    assert db.audit_events.find_one({"legacy": {"$ne": True}})["at"] == first_event_at(db)
    assert "Organisaatio luotu" in page


@pytest.mark.integration
def test_history_shows_estimated_creation_time_when_no_creator_is_recorded(admin_client, db, seeded_data):
    org_id = ObjectId(seeded_data["org_id"])
    db.audit_events.delete_many({})
    page = admin_client.get(f"/admin/organization/history/{org_id}").get_data(as_text=True)
    assert "Tekijä ei tiedossa" in page and "Luontiaika arvioitu" in page
    assert helsinki_time(org_id.generation_time, seconds=True) in page

    db.audit_events.insert_one(_event(org_id, datetime.now(timezone.utc), "insert", [{"_id": org_id, "name": "X"}],
                                      {"kind": "user", "user_id": str(seeded_data["admin_id"])}))
    page = admin_client.get(f"/admin/organization/history/{org_id}").get_data(as_text=True)
    assert "Luontiaika arvioitu" not in page

def test_backfill_adds_sent_field_names_and_collapses_double_logged_edits(db, seeded_data, monkeypatch):
    from mielenosoitukset_fi.scripts import backfill_org_history as backfill

    monkeypatch.delenv("ORG_HISTORY_BACKFILL_DISABLED", raising=False)
    monkeypatch.setattr(backfill, "FIELDS_FIRST_DAY", datetime(2026, 11, 1))
    monkeypatch.setattr(backfill, "FIELDS_MARKER_ID", backfill.FIELDS_MARKER_ID)
    monkeypatch.setattr(backfill, "PAUSE", 0, raising=False)
    org_id = ObjectId(seeded_data["org_id"])
    actor = str(seeded_data["admin_id"])
    at = datetime(2026, 11, 10, 12, 0, 0)
    db.audit_events.delete_many({})
    db.admin_logs.delete_many({})
    db.maintenance_markers.delete_many({})
    db.audit_events.insert_one(_event(org_id, datetime.now(timezone.utc), "update", [{"$set": {"x": 1}}],
                                      {"kind": "user", "user_id": actor}))
    path = f"/admin/organization/edit/{org_id}"
    form_row = {"actor": {"user_id": actor}, "request": {"method": "POST", "path": path,
                "form_keys": ["name", "website", "csrf_token", "password"]}}
    db.admin_logs.insert_many([
        {"event": "organization_update", "timestamp": at + timedelta(seconds=2), "actor": {"id": actor},
         "details": {"org_id": str(org_id)}},
        {**form_row, "timestamp": at},
        {**form_row, "timestamp": at + timedelta(seconds=1)},  # logged twice
        {**form_row, "timestamp": at + timedelta(days=2), "request": {**form_row["request"], "form_keys": ["email"]}},
        {**form_row, "timestamp": at + timedelta(days=3), "request": {"method": "POST", "path": "/admin/demo/edit/x",
                                                                      "form_keys": ["name"]}},
    ])
    result = backfill.main(db=db)
    assert result["fields"]["matched_events"] == 2 and result["fields"]["new_rows"] == 1
    legacy = list(db.audit_events.find({"legacy": True, "collection": "organizations"}))
    edit = [e for e in legacy if e.get("reason") == "Organisaation tietoja muokattu"]
    assert sorted(tuple(e["legacy_fields"]) for e in edit) == [("email",), ("name", "website")]
    assert "csrf_token" not in str(legacy) and "password" not in str(legacy)
    assert backfill.main(db=db) == {"already_done": True}


@pytest.mark.integration
def test_backfill_gives_admin_form_creations_their_creator(db, monkeypatch):
    from mielenosoitukset_fi.scripts import backfill_org_history as backfill

    monkeypatch.delenv("ORG_HISTORY_BACKFILL_DISABLED", raising=False)
    monkeypatch.setattr(backfill, "FIELDS_FIRST_DAY", datetime(2025, 3, 1))
    monkeypatch.setattr(backfill, "PAUSE", 0)
    when = datetime(2025, 3, 5, 9, 30, 0)
    org_id = ObjectId.from_datetime(when + timedelta(seconds=2))
    db.organizations.insert_one({"_id": org_id, "name": "Vanha järjestö"})
    for coll in ("audit_events", "admin_logs", "maintenance_markers"):
        db[coll].delete_many({})
    db.audit_events.insert_one(_event(ObjectId(), datetime.now(timezone.utc), "update", [{"$set": {"x": 1}}],
                                      {"kind": "system"}))
    db.admin_logs.insert_many([
        {"timestamp": when, "user": {"_id": "u-creator", "username": "x"},
         "request": {"method": "POST", "path": "/admin/organization/create"}},
        {"timestamp": when, "user": {"_id": "u-creator"},  # a second org created far from this request
         "request": {"method": "POST", "path": "/admin/organization/create"}},
    ])
    try:
        result = backfill.main(db=db)["fields"]
        assert result["new_rows"] == 1 and result["create_rows"] == 2 and result["create_unmatched"] == 0
        row = db.audit_events.find_one({"legacy": True, "doc_ids": org_id})
        assert row["op"] == "insert" and row["actor"] == {"kind": "user", "user_id": "u-creator"}
        assert row["at"].replace(tzinfo=None) == when
    finally:
        db.organizations.delete_one({"_id": org_id})
