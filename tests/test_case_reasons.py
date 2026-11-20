"""Rejecting a demonstration and closing a case take a structured reason."""
from copy import deepcopy

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils import case_reasons
from tests.conftest import _client_for_user
from tests.test_city_scoped_admin import _create_scoped_admin


def _admin(app, seeded_data):
    return _client_for_user(app, seeded_data["admin_id"])


def _second_case(db, seeded_data, running_num=777001, **changes):
    doc = deepcopy(db.cases.find_one({"_id": seeded_data["case_id"]}))
    doc.update({"_id": ObjectId(), "running_num": running_num, **changes})
    db.cases.insert_one(doc)
    return doc["_id"]


def test_parse_reason_rules(db, seeded_data):
    parse = case_reasons.parse_reason
    with pytest.raises(case_reasons.ReasonError):
        parse({"reason_key": "handled"}, case_reasons.REJECT_KEYS, db, demo_id="x")
    with pytest.raises(case_reasons.ReasonError):
        parse({"reason_key": "other"}, case_reasons.CLOSE_KEYS, db)
    with pytest.raises(case_reasons.ReasonError):
        parse({"reason_key": "spam", "note": "x" * 1001}, case_reasons.CLOSE_KEYS, db)
    meta, _schema = parse({"reason_key": "spam"}, case_reasons.CLOSE_KEYS, db)
    assert meta["reason"] == "Roskaposti" and meta["note"] == ""
    # Old clients send free text only.
    meta, _schema = parse({"reason": "Vanha syy"}, case_reasons.CLOSE_KEYS, db)
    assert meta["reason_key"] == "other" and meta["reason"] == "Muu syy – Vanha syy"


def test_close_as_duplicate_links_original_case(app, db, seeded_data):
    original = _second_case(db, seeded_data)
    case_id = seeded_data["case_id"]
    client = _admin(app, seeded_data)
    response = client.post(f"/admin/case/{case_id}/close/", json={
        "reason_key": "duplicate", "duplicate_of": "#777001", "note": "Sama ilmoitus",
    })
    assert response.status_code == 200, response.get_json()
    doc = db.cases.find_one({"_id": case_id})
    entry = doc["case_history"][-1]
    assert entry["mech_action"] == "close_case"
    assert entry["metadata"]["duplicate_of_case_id"] == str(original)
    assert entry["metadata"]["reason"] == "Kaksoiskappale: #777001 – Sama ilmoitus"
    assert doc["meta"]["closed"] is True and doc["meta"]["closed_reason"].startswith("Kaksoiskappale")
    page = client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert f"/admin/case/{original}/" in page and "Avaa alkuperäinen tapaus" in page

    assert client.post(f"/admin/case/{case_id}/reopen/", json={"reason": "Ei sittenkään"}).status_code == 200
    assert "closed_reason" not in db.cases.find_one({"_id": case_id})["meta"]


def test_duplicate_needs_a_real_other_case(app, db, seeded_data):
    case_id = seeded_data["case_id"]
    running_num = db.cases.find_one({"_id": case_id}).get("running_num")
    client = _admin(app, seeded_data)
    for reference in ("", "#999999999", str(running_num)):
        response = client.post(f"/admin/case/{case_id}/close/", json={"reason_key": "duplicate", "duplicate_of": reference})
        assert response.status_code == 400
    assert not db.cases.find_one({"_id": case_id})["meta"].get("closed")


def test_city_admin_cannot_point_at_a_case_they_cannot_see(app, db, seeded_data):
    db.cases.update_one({"_id": seeded_data["case_id"]}, {"$set": {"demo_id": seeded_data["pending_demo_id"]}})
    foreign_demo = deepcopy(db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]}))
    foreign_demo.update({"_id": ObjectId(), "city": "Turku", "city_key": "turku"})
    db.demonstrations.insert_one(foreign_demo)
    _second_case(db, seeded_data, running_num=777002, demo_id=foreign_demo["_id"])
    actor = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE", "EDIT_CASE"])
    response = _client_for_user(app, actor).post(f"/admin/case/{seeded_data['case_id']}/close/", json={
        "reason_key": "duplicate", "duplicate_of": "777002",
    })
    assert response.status_code == 400
    assert response.get_json()["error"] == "Alkuperäistä tapausta ei löytynyt."


def test_reject_demo_as_duplicate_records_history(app, db, seeded_data):
    case_id = seeded_data["case_id"]
    original = seeded_data["demo_id"]
    client = _admin(app, seeded_data)
    assert client.post(f"/admin/case/{case_id}/demo_action", json={
        "action": "reject", "reason_key": "duplicate", "duplicate_of": str(seeded_data["pending_demo_id"]),
    }).status_code == 400
    response = client.post(f"/admin/case/{case_id}/demo_action", json={
        "action": "reject", "reason_key": "duplicate", "duplicate_of": str(original),
    })
    assert response.status_code == 200, response.get_json()
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["rejected"] is True
    doc = db.cases.find_one({"_id": case_id})
    entry = doc["case_history"][-1]
    assert entry["mech_action"] == "reject_demo" and entry["metadata"]["duplicate_of_demo_id"] == str(original)
    assert doc["action_logs"][-1]["reason"].startswith("Kaksoiskappale: ")
    page = client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert "Avaa alkuperäinen mielenosoitus" in page


def test_reject_needs_a_reason(app, db, seeded_data):
    client = _admin(app, seeded_data)
    response = client.post(f"/admin/case/{seeded_data['case_id']}/demo_action", json={"action": "reject"})
    assert response.status_code == 400
    assert not db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]}).get("rejected")
