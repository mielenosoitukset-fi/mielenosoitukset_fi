"""Undo a demo cancellation from the admin (audit item: restore cancelled demo)."""

from bson import ObjectId

from mielenosoitukset_fi.emailer.EmailSender import EmailSender


def _cancel(admin_client, demo_id):
    response = admin_client.post(f"/api/admin/demo/{demo_id}/cancel", json={"reason": "Sade"})
    assert response.status_code == 200 and response.get_json()["success"]


def test_restore_requires_reason_and_keeps_history(admin_client, db, seeded_data, monkeypatch):
    sent = []
    monkeypatch.setattr(EmailSender, "queue_email", lambda self, **kw: sent.append(kw))
    demo_id = seeded_data["demo_id"]
    db.demo_reminders.insert_one({"_id": ObjectId(), "demonstration_id": demo_id, "user_email": "r@example.test"})
    _cancel(admin_client, demo_id)
    case_id = db.demonstrations.find_one({"_id": demo_id})["cancellation_case_id"]
    sent.clear()

    missing = admin_client.post(f"/api/admin/demo/{demo_id}/restore", json={"reason": " "})
    assert missing.status_code == 400
    assert db.demonstrations.find_one({"_id": demo_id})["cancelled"] is True

    ok = admin_client.post(f"/api/admin/demo/{demo_id}/restore", json={"reason": "Peruttu vahingossa"})
    assert ok.status_code == 200 and ok.get_json()["success"]

    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["cancelled"] is False
    assert "cancelled_at" not in demo and "cancellation_reason" not in demo
    assert demo["restore_reason"] == "Peruttu vahingossa"
    history = demo["cancellation_history"][-1]
    assert history["cancellation_reason"] == "Sade"
    assert history["restore_reason"] == "Peruttu vahingossa"
    assert history["restored_by"]["source"] == "admin_api"

    case = db.cases.find_one({"_id": case_id})
    assert case["case_history"][-1]["action"] == "restored"

    audit = db.demo_audit_logs.find_one({"demo_id": str(demo_id), "action": "restore_demo"})
    assert audit and audit["details"]["reason"] == "Peruttu vahingossa"

    assert [m["template_name"] for m in sent] == ["demo_restored_notification.html"]
    assert sent[0]["recipients"] == ["r@example.test"]

    again = admin_client.post(f"/api/admin/demo/{demo_id}/restore", json={"reason": "uudestaan"})
    assert again.status_code == 409


def test_series_break_cancellation_is_restored_from_the_series(admin_client, db, seeded_data):
    child_id = seeded_data["child_demo_id"]
    db.demonstrations.update_one(
        {"_id": child_id},
        {"$set": {"cancelled": True, "cancelled_by": {"source": "recurring_break", "user_id": None}}},
    )
    response = admin_client.post(f"/api/admin/demo/{child_id}/restore", json={"reason": "Halutaan"})
    assert response.status_code == 409
    assert "taukopäivä" in response.get_json()["error"]
    assert db.demonstrations.find_one({"_id": child_id})["cancelled"] is True


def test_restore_needs_edit_permission(user_client, db, seeded_data):
    demo_id = seeded_data["demo_id"]
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"cancelled": True}})
    response = user_client.post(f"/api/admin/demo/{demo_id}/restore", json={"reason": "Halutaan"})
    assert response.status_code in (302, 401, 403)
    assert db.demonstrations.find_one({"_id": demo_id})["cancelled"] is True


def test_restored_email_template_renders():
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("mielenosoitukset_fi/templates/emails"), autoescape=True)
    html = env.get_template("demo_restored_notification.html").render(
        title="<b>x</b>", date="2026-12-01", city="Helsinki", detail_link="https://example.test/d"
    )
    assert "ei olekaan peruttu" in html
    assert "<b>x</b>" not in html


def test_command_center_offers_restore_only_for_cancelled(admin_client, db, seeded_data):
    demo_id = seeded_data["demo_id"]
    page = admin_client.get(f"/admin/demo/command-center/{demo_id}").get_data(as_text=True)
    assert 'data-command="restore"' not in page
    assert 'data-command="cancel"' in page

    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"cancelled": True}})
    page = admin_client.get(f"/admin/demo/command-center/{demo_id}").get_data(as_text=True)
    assert 'data-command="restore"' in page
    assert 'data-command="cancel"' not in page
    assert f"/api/admin/demo/{demo_id}/restore" in page

    db.demonstrations.update_one(
        {"_id": demo_id}, {"$set": {"cancelled_by": {"source": "recurring_break", "user_id": None}}}
    )
    page = admin_client.get(f"/admin/demo/command-center/{demo_id}").get_data(as_text=True)
    assert 'data-command="restore"' not in page
    assert "taukopäivänä" in page
