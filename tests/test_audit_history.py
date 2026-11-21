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
