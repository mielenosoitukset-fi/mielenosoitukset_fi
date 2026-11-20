"""Admins see self-service account deletion requests as cases (docs/account-erasure.md)."""

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow


def _case(db, user_id, reason=None):
    meta = {}
    if reason:
        meta["reason"] = reason
    case_id = ObjectId()
    db.cases.insert_one({
        "_id": case_id, "type": "account_deletion_request", "submitter_id": user_id, "demo_id": None,
        "organization_id": None, "submitter": None, "error_report": {}, "suggestion": {}, "meta": meta,
        "action_logs": [], "case_history": [], "running_num": 999001, "created_at": utcnow(), "updated_at": utcnow(),
    })
    return case_id


def test_case_page_links_the_requester_and_shows_the_reason(admin_client, db, seeded_data):
    case_id = _case(db, seeded_data["user_id"], "En käytä palvelua enää")

    page = admin_client.get(f"/admin/case/{case_id}/")

    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Tilin poistopyyntö" in body
    assert "En käytä palvelua enää" in body
    assert f"/admin/user/edit_user/{seeded_data['user_id']}" in body or str(seeded_data["user_id"]) in body
    assert db.users.find_one({"_id": seeded_data["user_id"]})["username"] in body
    assert "Ei tiedossa" not in body


def test_case_list_shows_the_request(admin_client, db, seeded_data):
    _case(db, seeded_data["user_id"])

    page = admin_client.get("/admin/case/")

    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Tilin poistopyyntö" in body
    assert db.users.find_one({"_id": seeded_data["user_id"]})["username"] in body
