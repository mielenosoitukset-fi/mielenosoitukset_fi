"""Required password and name changes: React pages, Flask keeps every decision."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_password_form_is_only_for_flagged_accounts(user_client, db, seeded_data):
    # Changing the password here needs no current password, so an unflagged session is sent away.
    response = user_client.get("/users/auth/forced_pwd_reset/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/users/auth/settings")
    before = db.users.find_one({"_id": seeded_data["user_id"]}).get("password_hash")
    posted = user_client.post("/users/auth/forced_pwd_reset/", data={
        "new_password": "Zq7-correct-horse-battery", "confirm_password": "Zq7-correct-horse-battery"})
    assert posted.status_code == 302 and posted.headers["Location"].endswith("/users/auth/settings")
    after = db.users.find_one({"_id": seeded_data["user_id"]})
    assert after.get("password_hash") == before


def test_flagged_password_account_is_kept_on_the_step(user_client, db, seeded_data):
    db.users.update_one({"_id": seeded_data["user_id"]}, {"$set": {"forced_pwd_reset": True}})
    page = user_client.get("/", follow_redirects=False)
    assert page.status_code == 302 and page.headers["Location"].endswith("/users/auth/forced_pwd_reset/")
    api = user_client.get("/api/notifications/")
    assert api.status_code == 428 and api.get_json()["redirect"] == "/users/auth/forced_pwd_reset/"
    assert user_client.get("/users/auth/forced_pwd_reset/").status_code == 200


def test_session_summary_and_flow_helpers_stay_reachable(user_client, db, seeded_data):
    db.users.update_one({"_id": seeded_data["user_id"]}, {"$set": {"forced_identity_change": True}})
    summary = user_client.get("/api/session")
    assert summary.status_code == 200
    assert summary.get_json()["forced"] == {"password": False, "identity": True}
    assert user_client.get("/get_flash_messages").status_code == 200
    assert user_client.get("/users/auth/api/username_free?username=new-name").status_code == 200


def test_session_summary_reports_no_steps_for_a_normal_account(user_client):
    assert user_client.get("/api/session").get_json()["forced"] == {"password": False, "identity": False}


def test_routing_claims_only_get_and_head_of_both_pages():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    for path in ("forced_pwd_reset", "forced_identity_change"):
        rule = f'RewriteRule "^/users/auth/{path}/$" "http://127.0.0.1:8090$0" [P,L]'
        assert rule in routing
        assert routing.split(rule)[0].rstrip().splitlines()[-1] == 'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"'


def test_pages_post_the_flask_field_names():
    password = (ROOT / "frontend/src/app/ForcedPasswordScreen.tsx").read_text(encoding="utf-8")
    identity = (ROOT / "frontend/src/app/ForcedIdentityScreen.tsx").read_text(encoding="utf-8")
    assert 'action={forcedPasswordPath} method="post"' in password
    assert 'name="new_password"' in password and 'name="confirm_password"' in password
    assert 'action={forcedIdentityPath} method="post"' in identity
    assert 'name="username"' in identity and 'name="displayname"' in identity
    render = (ROOT / "frontend/src/app/render.tsx").read_text(encoding="utf-8")
    assert "redirect: session.forced.password ? forcedPasswordPath : forcedIdentityPath" in render
