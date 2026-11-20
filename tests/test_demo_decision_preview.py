"""The approve/reject confirm shows the decision's real side effects first."""

from importlib import import_module

from bson import ObjectId


def _capture_decision_email(monkeypatch):
    queued = []
    for module in {
        import_module("mielenosoitukset_fi.admin.admin_demo_bp"),
        import_module("admin.admin_demo_bp"),
    }:
        monkeypatch.setattr(
            module.email_sender, "queue_email", lambda **kwargs: queued.append(kwargs)
        )
    return queued


def _add_edit_link(db, demo_id):
    reference = db.magic_links.find_one({"demo_id": str(demo_id)})
    db.magic_links.insert_one(
        {
            "_id": ObjectId(),
            "token_hash": "preview-test-edit-link",
            "demo_id": str(demo_id),
            "action": "edit",
            "created_at": reference["created_at"],
            "expires_at": reference["expires_at"],
            "used_at": None,
            "revoked": False,
        }
    )


def test_preview_lists_effects_without_changing_anything(
    admin_client, db, seeded_data, monkeypatch
):
    queued = _capture_decision_email(monkeypatch)
    demo_id = seeded_data["pending_demo_id"]
    _add_edit_link(db, demo_id)
    before = db.demonstrations.find_one({"_id": demo_id})
    live_links = db.magic_links.count_documents(
        {"demo_id": str(demo_id), "revoked": {"$ne": True}, "used_at": None}
    )

    response = admin_client.get(
        f"/api/admin/demo/{demo_id}/decision_preview?decision=approved"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["status_changes"] is True
    assert data["emails_submitter"] is True
    assert data["open_cases"] >= 1
    assert data["links_revoked"] == live_links
    assert data["edit_links_revoked"] == 1
    assert db.demonstrations.find_one({"_id": demo_id}) == before
    assert db.magic_links.count_documents(
        {"demo_id": str(demo_id), "revoked": {"$ne": True}, "used_at": None}
    ) == live_links
    assert queued == []


def test_preview_matches_what_the_decision_then_does(
    admin_client, db, seeded_data, monkeypatch
):
    queued = _capture_decision_email(monkeypatch)
    demo_id = seeded_data["pending_demo_id"]
    preview = admin_client.get(
        f"/api/admin/demo/{demo_id}/decision_preview?decision=rejected"
    ).get_json()

    admin_client.post(f"/api/admin/demo/{demo_id}/deny")

    assert len(queued) == int(preview["emails_submitter"])
    assert db.cases.count_documents(
        {"demo_id": {"$in": [demo_id, str(demo_id)]}, "meta.closed": {"$ne": True}}
    ) == 0
    assert db.magic_links.count_documents(
        {"demo_id": str(demo_id), "revoked": True}
    ) == preview["links_revoked"]

    again = admin_client.get(
        f"/api/admin/demo/{demo_id}/decision_preview?decision=rejected"
    ).get_json()
    assert again["status_changes"] is False
    assert again["emails_submitter"] is False
    assert again["open_cases"] == 0
    assert again["links_revoked"] == 0


def test_preview_rejects_unknown_decision_and_missing_demo(admin_client):
    bad = admin_client.get(
        f"/api/admin/demo/{ObjectId()}/decision_preview?decision=maybe"
    )
    missing = admin_client.get(
        f"/api/admin/demo/{ObjectId()}/decision_preview?decision=approved"
    )
    assert bad.status_code == 400
    assert missing.status_code in {403, 404}


def test_list_and_command_center_link_to_the_case_instead_of_deciding(admin_client, db, seeded_data):
    """Maintainer decision (20 Nov 2026): new submissions are decided only in the case."""
    demo_id = seeded_data["pending_demo_id"]
    case = db.cases.find_one({"type": "new_demo", "demo_id": demo_id})
    assert case is not None
    case_id = case["_id"]
    dashboard = admin_client.get("/admin/demo/").get_data(as_text=True)
    command_center = admin_client.get(
        f"/admin/demo/command-center/{demo_id}"
    ).get_data(as_text=True)

    for html in (dashboard, command_center):
        assert "MosoDemoDecision.confirm" not in html
        assert "data-demo-decision-modal" not in html
        assert f"/admin/case/{case_id}" in html
    assert 'data-command="approve"' not in command_center
    assert 'data-command="reject"' not in command_center
