"""Admin account creation emails a one-hour set-password link, never a password."""


def test_create_user_sends_link_not_password(admin_client, db, monkeypatch):
    sent = []
    import importlib
    module = importlib.import_module("mielenosoitukset_fi.admin.admin_user_bp")
    monkeypatch.setattr(module.email_sender, "queue_email", lambda **kw: sent.append(kw))
    response = admin_client.post("/admin/user/create_user", json={
        "username": "link-user", "email": "link-user@example.test",
    })
    assert response.status_code == 302
    saved = db.users.find_one({"username": "link-user"})
    assert saved["role"] == "user" and saved["confirmed"] is False
    assert len(sent) == 1
    mail = sent[0]
    assert mail["template_name"] == "hallinto_account_invite.html"
    assert "/password_reset/" in mail["context"]["reset_url"]
    assert "password" not in mail["context"]
