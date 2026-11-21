"""Each case type has a way to resolve it from the case page (audit #7)."""

from copy import deepcopy

from bson import ObjectId


def _case(db, seeded_data, **fields):
    doc = deepcopy(db.cases.find_one({"_id": seeded_data["case_id"]}))
    doc.update({"_id": ObjectId(), "running_num": 650000 + db.cases.count_documents({}), **fields})
    db.cases.insert_one(doc)
    return doc["_id"]


def test_org_suggestion_case_shows_changes_and_review_link(admin_client, db, seeded_data):
    sid = seeded_data["org_suggestion_id"]
    case_id = _case(db, seeded_data, type="organization_edit_suggestion", demo_id=None,
                    organization_id=seeded_data["org_id"], suggestion={}, meta={"suggestion_id": str(sid)})
    page = admin_client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert "Updated Test Organization" in page
    assert f"/admin/organization/{seeded_data['org_id']}/suggestion/{sid}" in page
    assert "Käsittele ehdotus" in page


def test_legacy_org_suggestion_case_finds_suggestion(admin_client, db, seeded_data):
    sid = seeded_data["org_suggestion_id"]
    legacy = db.org_edit_suggestions.find_one({"_id": sid})
    case_id = _case(db, seeded_data, type="organization_edit_suggestion", demo_id=None, suggestion=legacy, meta={})
    page = admin_client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert "Updated Test Organization" in page
    assert "Käsittele ehdotus" in page


def test_rejecting_org_suggestion_closes_case_without_changing_org(admin_client, db, seeded_data):
    sid, org_id = seeded_data["org_suggestion_id"], seeded_data["org_id"]
    before = db.organizations.find_one({"_id": org_id})
    case_id = _case(db, seeded_data, type="organization_edit_suggestion", demo_id=None,
                    organization_id=org_id, suggestion={}, meta={"suggestion_id": str(sid)})
    response = admin_client.post(f"/admin/organization/{org_id}/suggestion/{sid}/reject", data={"note": "ei"})
    assert response.status_code == 302
    assert db.org_edit_suggestions.find_one({"_id": sid})["status"]["state"] == "rejected"
    assert db.organizations.find_one({"_id": org_id})["name"] == before["name"]
    case = db.cases.find_one({"_id": case_id})
    assert case["meta"]["closed"] is True
    assert case["case_history"][-1]["mech_action"] == "org_suggestion_decided"


def test_applying_org_suggestion_closes_case_and_drops_unsafe_links(admin_client, db, seeded_data):
    sid, org_id = seeded_data["org_suggestion_id"], seeded_data["org_id"]
    db.org_edit_suggestions.update_one({"_id": sid}, {"$set": {"fields.social_media_links": {
        "bluesky": "https://bsky.app/profile/x", "evil": "javascript:alert(1)"}}})
    case_id = _case(db, seeded_data, type="organization_edit_suggestion", demo_id=None,
                    organization_id=org_id, suggestion={}, meta={"suggestion_id": str(sid)})
    review = admin_client.get(f"/admin/organization/{org_id}/suggestion/{sid}").get_data(as_text=True)
    assert 'href="javascript:' not in review
    admin_client.post(f"/admin/organization/{org_id}/suggestion/{sid}/apply",
                      data={"apply_fields": ["name", "social_media_links"]})
    org = db.organizations.find_one({"_id": org_id})
    assert org["name"] == "Updated Test Organization"
    assert org["social_media_links"] == {"bluesky": "https://bsky.app/profile/x"}
    assert org.get("updated_at")
    assert db.cases.find_one({"_id": case_id})["meta"]["closed"] is True


def test_apply_refuses_suggestion_of_another_org(admin_client, db, seeded_data):
    other = ObjectId()
    db.organizations.insert_one({"_id": other, "name": "Other org"})
    sid = seeded_data["org_suggestion_id"]
    response = admin_client.post(f"/admin/organization/{other}/suggestion/{sid}/apply", data={"apply_fields": ["name"]})
    assert response.status_code == 404
    assert db.organizations.find_one({"_id": other})["name"] == "Other org"


def test_cancellation_request_can_be_declined(admin_client, db, seeded_data):
    case_id = _case(db, seeded_data, type="demo_cancellation_request", demo_id=seeded_data["demo_id"],
                    meta={"reason": "Sairastuminen"})
    page = admin_client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert "Hylkää peruutuspyyntö" in page
    assert admin_client.post(f"/admin/case/{case_id}/decline_cancellation", data={"reason": ""}).status_code == 302
    assert not db.cases.find_one({"_id": case_id})["meta"].get("closed")
    admin_client.post(f"/admin/case/{case_id}/decline_cancellation", data={"reason": "Pyyntö ei tullut järjestäjältä"})
    case = db.cases.find_one({"_id": case_id})
    assert case["meta"]["closed"] is True
    assert case["meta"]["cancellation_status"] == "declined"
    assert case["case_history"][-1]["metadata"]["reason"] == "Pyyntö ei tullut järjestäjältä"
    assert not db.demonstrations.find_one({"_id": seeded_data["demo_id"]}).get("cancelled")


def test_error_report_links_to_the_demo(admin_client, db, seeded_data):
    case_id = _case(db, seeded_data, type="demo_error_report", demo_id=seeded_data["demo_id"],
                    error_report={"error": "Väärä osoite"})
    page = admin_client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert "Korjaa mielenosoitusta" in page
    assert f"/admin/demo/edit_demo/{seeded_data['demo_id']}" in page


def test_pending_demo_case_links_to_preview_not_public_page(admin_client, db, seeded_data):
    page = admin_client.get(f"/admin/case/{seeded_data['case_id']}/").get_data(as_text=True)
    assert f"/admin/demo/{seeded_data['pending_demo_id']}/preview" in page
    assert "Avaa julkinen sivu" not in page


def test_public_org_suggestion_opens_linked_case_without_ip(client, db, seeded_data):
    org_id = seeded_data["org_id"]
    response = client.post(f"/organization/{org_id}/save_suggestion",
                           data={"name": "Uusi nimi", "email": "anon@example.test"})
    assert response.status_code in (200, 302)
    suggestion = db.org_edit_suggestions.find_one({"fields.name": "Uusi nimi"})
    case = db.cases.find_one({"type": "organization_edit_suggestion", "meta.suggestion_id": str(suggestion["_id"])})
    assert case["organization_id"] == org_id
    assert case["submitter"] == {"submitter_email": "anon@example.test"}
    assert "ip" not in str(case.get("suggestion") or {})
    assert (case.get("meta") or {}).get("urgency") is None
