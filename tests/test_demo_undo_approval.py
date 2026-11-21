"""Undoing a mistaken approval within a short window.

Maintainer decision (20 Nov 2026): for about 30 minutes after an approval an
admin can undo it in the case. The demo goes back to pending, the case reopens
and no email is sent. After the window only a take-down is possible.
"""

from datetime import timedelta

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.test_demo_decision_service import _capture_decision_email


def _link_case(db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    db.cases.update_one(
        {"_id": seeded_data["case_id"]},
        {"$set": {"demo_id": demo_id, "case_type": "new_demo", "type": "new_demo"}},
    )
    return demo_id, seeded_data["case_id"]


def _approve(admin_client, demo_id):
    response = admin_client.post(f"/api/admin/demo/{demo_id}/approve", json={})
    assert response.status_code == 200


def _undo(client, case_id):
    return client.post(f"/admin/case/{case_id}/demo_action", json={"action": "undo_approval"})


def test_undo_returns_demo_to_pending_and_reopens_case(admin_client, db, seeded_data, monkeypatch):
    demo_id, case_id = _link_case(db, seeded_data)
    link_id = db.magic_links.insert_one(
        {"demo_id": str(demo_id), "action": "edit", "revoked": False, "used_at": None,
         "created_at": utcnow() - timedelta(minutes=1)}
    ).inserted_id
    queued = _capture_decision_email(monkeypatch)
    _approve(admin_client, demo_id)
    assert db.magic_links.find_one({"_id": link_id})["revoked"] is True
    assert db.cases.find_one({"_id": case_id})["meta"]["closed"] is True
    approval_mails = len(queued)

    response = _undo(admin_client, case_id)

    assert response.status_code == 200, response.get_json()
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["approved"] is False and demo["rejected"] is False
    assert demo["moderation_decision"]["status"] == "pending"
    assert demo["moderation_decision"]["undone_decision_id"]
    case = db.cases.find_one({"_id": case_id})
    assert case["meta"]["closed"] is False
    assert "outcome" not in case["meta"] and "closed_reason" not in case["meta"]
    assert case["action_logs"][-1]["action_type"] == "undo_approval"
    assert db.magic_links.find_one({"_id": link_id})["revoked"] is False
    assert len(queued) == approval_mails  # undo sends nothing
    history = db.demo_history.find_one({"demo_id": demo_id}, sort=[("_id", -1)]) or db.demo_audit_logs.find_one(
        {"action": "undo_approval"}
    )
    assert history is not None

    # Approving again does not email the submitter a second time.
    _approve(admin_client, demo_id)
    assert db.demonstrations.find_one({"_id": demo_id})["approved"] is True
    assert len(queued) == approval_mails


def test_undo_after_window_is_refused(admin_client, db, seeded_data):
    demo_id, case_id = _link_case(db, seeded_data)
    _approve(admin_client, demo_id)
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$set": {"moderation_decision.at": utcnow() - timedelta(minutes=31)}}
    )

    response = _undo(admin_client, case_id)

    assert response.status_code == 409
    assert db.demonstrations.find_one({"_id": demo_id})["approved"] is True


def test_undo_only_applies_to_an_approval(admin_client, db, seeded_data):
    demo_id, case_id = _link_case(db, seeded_data)
    assert _undo(admin_client, case_id).status_code == 409  # pending
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"rejected": True, "moderation_decision": {
        "id": "x", "status": "rejected", "at": utcnow()}}})
    assert _undo(admin_client, case_id).status_code == 409
    assert db.demonstrations.find_one({"_id": demo_id})["rejected"] is True


def test_case_page_shows_undo_button_only_inside_window(admin_client, db, seeded_data):
    demo_id, case_id = _link_case(db, seeded_data)
    url = f"/admin/case/{case_id}/"
    marker = 'onclick="undoApproval()"'
    assert marker not in admin_client.get(url).get_data(as_text=True)

    _approve(admin_client, demo_id)
    body = admin_client.get(url).get_data(as_text=True)
    assert marker in body and "Peru hyväksyntä" in body

    db.demonstrations.update_one(
        {"_id": demo_id}, {"$set": {"moderation_decision.at": utcnow() - timedelta(hours=1)}}
    )
    assert marker not in admin_client.get(url).get_data(as_text=True)


def test_undo_needs_approval_rights(app, admin_client, db, seeded_data):
    from mielenosoitukset_fi.users.models import User
    from tests.conftest import _client_for_user

    demo_id, case_id = _link_case(db, seeded_data)
    _approve(admin_client, demo_id)
    editor_id = ObjectId()
    editor = User.create_user(
        username=f"undo-editor-{str(editor_id)[-6:]}", password="EditorPass1!",
        email=f"undo-editor-{str(editor_id)[-6:]}@example.test", displayname="Editor",
    )
    editor.update({
        "_id": editor_id, "confirmed": True, "active": True, "role": "admin", "global_admin": False,
        "global_permissions": ["LIST_DEMOS", "VIEW_DEMO", "EDIT_DEMO", "VIEW_CASE", "EDIT_CASE"],
    })
    db.users.insert_one(editor)

    response = _undo(_client_for_user(app, editor_id), case_id)

    assert response.status_code in (302, 403, 404)
    assert db.demonstrations.find_one({"_id": demo_id})["approved"] is True
