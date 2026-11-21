"""The React password reset page: Flask still checks the link and owns the change."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = "/users/auth/api/password_reset/status"


def test_status_accepts_a_valid_link_and_logs_the_visit(client, db, seeded_data):
    token = seeded_data["reset_token"]
    before = db.password_changes.count_documents({})
    response = client.post(STATUS, json={"token": token})
    assert response.status_code == 200 and response.get_json() == {"valid": True}
    assert response.headers["Cache-Control"] == "no-store"
    row = db.password_changes.find_one(sort=[("_id", -1)])
    assert db.password_changes.count_documents({}) == before + 1
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest() and row["visit"] is True


def test_status_refuses_garbage_and_flashes_the_reason(client, db):
    response = client.post(STATUS, json={"token": "garbage"})
    assert response.status_code == 200 and response.get_json() == {"valid": False}
    flashes = client.get("/get_flash_messages").get_json()["messages"]
    assert any("virheellinen tai vanhentunut" in item["message"] for item in flashes)


def test_status_refuses_a_spent_link(client, db, seeded_data):
    token = seeded_data["reset_token"]
    password = "Zq7-correct-horse-battery-staple"
    changed = client.post(f"/users/auth/password_reset/{token}", data={"password": password, "password_confirm": password})
    assert changed.status_code == 302 and changed.headers["Location"].endswith("/users/auth/login")
    assert client.post(STATUS, json={"token": token}).get_json() == {"valid": False}


def test_status_needs_a_json_token_and_same_site(client):
    assert client.get(STATUS).status_code == 405
    assert client.post(STATUS, data={"token": "x"}).status_code == 400
    assert client.post(STATUS, json={"token": "x" * 2049}).status_code == 400
    cross = client.post(STATUS, json={"token": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403


def test_routing_claims_only_get_and_head_of_the_token_path():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    rule = 'RewriteRule "^/users/auth/password_reset/[A-Za-z0-9_.-]{1,2048}$" "http://127.0.0.1:8090$0" [P,L]'
    assert rule in routing
    before = routing.split(rule)[0].rstrip().splitlines()[-1]
    assert before == 'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"'


def test_page_posts_to_the_flask_handler_and_keeps_the_token_private():
    screen = (ROOT / "frontend/src/app/PasswordResetScreen.tsx").read_text(encoding="utf-8")
    render = (ROOT / "frontend/src/app/render.tsx").read_text(encoding="utf-8")
    assert "const action = `/users/auth/password_reset/${token}`;" in screen
    assert 'action={action} method="post"' in screen and 'name="password_confirm"' in screen
    block = render.split("if (route.name === 'password-reset') {", 1)[1].split("\n  }\n", 1)[0]
    assert "headers['Referrer-Policy'] = 'no-referrer';" in block
    assert "headers['Cache-Control'] = 'private, no-store';" in block


def test_token_pages_never_load_the_third_party_tracker():
    client_source = (ROOT / "frontend/src/app/client.tsx").read_text(encoding="utf-8")
    secret_start = client_source.index("const secretPath = ")
    secret_line = client_source[secret_start:client_source.index(";", secret_start)]
    for name in ("password-reset", "cancel-link", "volunteer-confirm", "email-confirm", "org-invite"):
        assert f"name === '{name}'" in secret_line
    assert "session.matomoEnabled && !secretPath" in client_source
