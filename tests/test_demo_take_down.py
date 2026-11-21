"""Taking down an approved demonstration without deleting it.

Maintainer decisions (20 Nov 2026): reason required, who/when recorded,
reversible (republish), public URL shows a short notice with the reason (not a
404), out of every public list, submitter emailed with the reason, rights
behind one permission function.
"""

from bson import ObjectId

from tests.test_city_scoped_admin import _create_scoped_admin
from tests.conftest import _client_for_user
from tests.test_demo_decision_service import _capture_decision_email


def _approve(admin_client, demo_id):
    response = admin_client.post(f"/api/admin/demo/{demo_id}/approve", json={})
    assert response.status_code == 200


def _take_down(client, demo_id, reason="Ilmoitus sisälsi henkilötietoja."):
    # Urgent: hidden at once; the second-admin flow is in test_demo_take_down_review.
    return client.post(f"/api/admin/demo/{demo_id}/take_down", json={"reason": reason, "urgent": True})


def test_take_down_hides_records_and_emails_reason(admin_client, client, db, seeded_data, monkeypatch):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    queued = _capture_decision_email(monkeypatch)

    response = _take_down(admin_client, demo_id)

    assert response.status_code == 200, response.get_json()
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["approved"] is False and demo["rejected"] is True and demo["taken_down"] is True
    assert demo["taken_down_reason"] == "Ilmoitus sisälsi henkilötietoja."
    assert demo["taken_down_by"] and demo["taken_down_at"]
    assert demo["moderation_decision"]["status"] == "taken_down"
    assert [mail["template_name"] for mail in queued] == ["demo_submitter_taken_down.html"]
    assert queued[0]["context"]["reason"] == "Ilmoitus sisälsi henkilötietoja."
    case = db.cases.find_one({"demo_id": {"$in": [demo_id, str(demo_id)]}})
    assert case["meta"]["outcome"] == "taken_down"
    assert "Syy: Ilmoitus sisälsi henkilötietoja." in case["action_logs"][-1]["note"]

    public = client.get("/demonstration/pending-demonstration")
    assert public.status_code == 410
    body = public.get_data(as_text=True)
    assert "Mielenosoitus on poistettu sivustolta" in body
    assert "Ilmoitus sisälsi henkilötietoja." in body
    assert public.headers.get("X-Robots-Tag") == "noindex"


def test_take_down_needs_reason_json_and_a_public_demo(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    assert _take_down(admin_client, demo_id).status_code == 409  # still pending
    _approve(admin_client, demo_id)
    assert _take_down(admin_client, demo_id, reason="   ").status_code == 400
    form = admin_client.post(f"/api/admin/demo/{demo_id}/take_down", data={"reason": "x"})
    assert form.status_code == 415
    assert db.demonstrations.find_one({"_id": demo_id})["approved"] is True


def test_city_admin_cannot_take_down_or_republish(app, admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    city_admin = _client_for_user(
        app, _create_scoped_admin(db, ["helsinki"], ["LIST_DEMOS", "VIEW_DEMO", "ACCEPT_DEMO"])
    )
    assert _take_down(city_admin, demo_id).status_code == 403
    assert _take_down(admin_client, demo_id).status_code == 200

    response = city_admin.post(f"/api/admin/demo/{demo_id}/approve", json={})
    assert response.status_code == 403
    assert db.demonstrations.find_one({"_id": demo_id})["taken_down"] is True


def test_republish_restores_the_demo(admin_client, client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    _take_down(admin_client, demo_id)

    _approve(admin_client, demo_id)

    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["approved"] is True and demo["rejected"] is False and demo["taken_down"] is False
    assert client.get("/demonstration/pending-demonstration").status_code != 410


def test_hidden_or_unknown_demo_gets_no_notice(admin_client, client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    _take_down(admin_client, demo_id)
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"hide": True}})
    assert client.get("/demonstration/pending-demonstration").status_code != 410
    assert client.get(f"/demonstration/{ObjectId()}").status_code != 410


def test_case_page_action_takes_down_with_reason(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    case = db.cases.find_one({"type": "new_demo", "demo_id": demo_id})
    url = f"/admin/case/{case['_id']}/demo_action"

    assert admin_client.post(url, json={"action": "take_down"}).status_code == 400
    response = admin_client.post(url, json={"action": "take_down", "reason": "Vaarallinen kokoontumispaikka.", "urgent": True})

    assert response.status_code == 200, response.get_json()
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["taken_down"] is True
    assert demo["moderation_decision"]["source"] == "case_take_down"


def test_case_page_offers_the_right_button_per_state(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    case = db.cases.find_one({"type": "new_demo", "demo_id": demo_id})
    page = lambda: admin_client.get(f"/admin/case/{case['_id']}/").get_data(as_text=True)

    pending = page()
    assert "Hylkää mielenosoitus" in pending and "takeDownDemo()\"" not in pending

    _approve(admin_client, demo_id)
    approved = page()
    assert 'onclick="takeDownDemo()"' in approved
    assert 'onclick="rejectDemo()"' not in approved and 'onclick="approveDemo()"' not in approved

    _take_down(admin_client, demo_id)
    taken_down = page()
    assert "Julkaise uudelleen" in taken_down
    assert "Ilmoitus sisälsi henkilötietoja." in taken_down
    assert 'onclick="takeDownDemo()"' not in taken_down


def test_bulk_approve_skips_taken_down_without_rights(app, admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    _take_down(admin_client, demo_id)
    city_admin = _client_for_user(
        app, _create_scoped_admin(db, ["helsinki"], ["LIST_DEMOS", "VIEW_DEMO", "ACCEPT_DEMO"])
    )
    with city_admin.session_transaction() as session:
        token = session.get("demo_edit_link_csrf_token") or "x"
        session["demo_edit_link_csrf_token"] = token
    response = city_admin.post(
        "/api/admin/demo/bulk_decide",
        json={"demo_ids": [str(demo_id)], "decision": "approved"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert response.get_json()["results"][0]["status"] == "forbidden"
    assert db.demonstrations.find_one({"_id": demo_id})["taken_down"] is True
