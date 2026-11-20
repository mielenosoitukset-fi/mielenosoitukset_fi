"""The React login page: form targets, helpers, routing and Flask's MFA hint."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { safeRedirect, loginFormAction, loginNext, pendingMfa, loginLegacyLocation, classifyMfaCheck, decodeRequestOptions }
  from './frontend/src/app/login-route.ts';
const decoded = decodeRequestOptions({ challenge: 'AQID', allowCredentials: [{ id: 'BAU', type: 'public-key' }], rpId: 'x' });
console.log(JSON.stringify({
  safe: ['/a?b=1#c', '//evil.example', 'https://evil.example', '/\\\\evil', 'javascript:alert(1)', '/a\\nb', 7, ''].map(safeRedirect),
  actions: [loginFormAction(''), loginFormAction('?next=/x%3Fy%3D1&popup=1&mfa=1&other=2'), loginFormAction('?popup=0&next=')],
  next: [loginNext(''), loginNext('?next=/profile')],
  mfa: [pendingMfa('?mfa=1'), pendingMfa('?mfa=0'), pendingMfa('')],
  legacy: [loginLegacyLocation(''), loginLegacyLocation('?next=/x&mfa=1&legacy=1')],
  checks: [classifyMfaCheck(200, { enabled: true, valid: true }), classifyMfaCheck(200, { enabled: false, valid: true }),
    classifyMfaCheck(200, { enabled: false, valid: false }), classifyMfaCheck(429, { throttled: true }),
    classifyMfaCheck(200, { unverified: true }), classifyMfaCheck(500, null), classifyMfaCheck(500, { valid: false })],
  challenge: Array.from(new Uint8Array(decoded.challenge)),
  credential: Array.from(new Uint8Array(decoded.allowCredentials[0].id)),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_login_route_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["safe"] == ["/a?b=1#c", "/", "/", "/", "/", "/", "/", "/"]
    assert out["actions"] == ["/users/auth/login?next=%2F", "/users/auth/login?next=%2Fx%3Fy%3D1&popup=1",
                              "/users/auth/login?next=%2F"]
    assert out["next"] == ["/", "/profile"]
    assert out["mfa"] == [True, False, False]
    assert out["legacy"] == ["/users/auth/login?legacy=1", "/users/auth/login?next=%2Fx&legacy=1"]
    assert out["checks"] == ["mfa", "valid", "invalid", "throttled", "unverified", "error", "error"]
    assert out["challenge"] == [1, 2, 3] and out["credential"] == [4, 5]


def test_forms_post_to_flask_and_render_no_raw_html():
    screen = (ROOT / "frontend/src/app/LoginScreen.tsx").read_text(encoding="utf-8")
    assert screen.count("action={action} method=\"post\"") == 2
    assert 'name="username"' in screen and 'name="password"' in screen and 'name="2fa_code"' in screen
    assert 'action={loginApi.resendConfirmation} method="post"' in screen and 'name="email_or_username"' in screen
    assert "dangerouslySetInnerHTML" not in screen


def test_routing_claims_only_get_and_head_without_legacy():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    rule = 'RewriteRule "^/users/auth/login$" "http://127.0.0.1:8090$0" [P,L]'
    assert rule in routing
    before = routing.split(rule)[0].rstrip().splitlines()[-2:]
    assert before == ['RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"', 'RewriteCond %{QUERY_STRING} "!(^|&)legacy=1(&|$)"']


def test_flask_login_get_still_serves_the_legacy_page(client):
    response = client.get("/users/auth/login", query_string={"legacy": "1", "next": "/users/profile/"})
    assert response.status_code == 200
    assert 'id="login-form"' in response.get_data(as_text=True)


def test_signed_in_legacy_get_redirects_to_next(client, seeded_data):
    client.post("/users/auth/login", data={"username": seeded_data["user_username"], "password": "UserPass1!"})
    response = client.get("/users/auth/login", query_string={"legacy": "1", "next": "/users/profile/"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/users/profile/"


def test_flask_marks_the_mfa_step_in_its_redirects(app, db):
    from urllib.parse import parse_qs, urlsplit
    from mielenosoitukset_fi.users.models import UserMFA
    from tests.test_mfa_settings import TEST_PASSWORD, _create_mfa_user, _invalid_totp_code

    user_id = _create_mfa_user(db)
    secret = UserMFA(user_id).add_device()
    db.users.update_one({"_id": user_id}, {"$set": {"mfa_enabled": True}})
    username = db.users.find_one({"_id": user_id})["username"]
    client = app.test_client()

    # Without JavaScript: the password POST asks for the code step.
    response = client.post("/users/auth/login", query_string={"next": "/users/profile/"},
                           data={"username": username, "password": TEST_PASSWORD})
    location = urlsplit(response.headers["Location"])
    assert location.path == "/users/auth/login"
    assert parse_qs(location.query) == {"next": ["/users/profile/"], "mfa": ["1"]}

    # A wrong code keeps the step; the hint never signs anyone in.
    response = client.post("/users/auth/login", query_string={"next": "/users/profile/"},
                           data={"2fa_code": _invalid_totp_code(secret)})
    assert parse_qs(urlsplit(response.headers["Location"]).query)["mfa"] == ["1"]
    with client.session_transaction() as login_session:
        assert "_user_id" not in login_session

    # mfa=1 on a fresh client without a verified first factor asks for the password again.
    fresh = app.test_client()
    response = fresh.post("/users/auth/login", query_string={"next": "/", "mfa": "1"}, data={"2fa_code": "123456"})
    assert "mfa=1" not in response.headers["Location"]
    with fresh.session_transaction() as login_session:
        assert "_user_id" not in login_session


def test_login_required_notice_is_finnish_and_shown_once(client):
    # Several protected requests while signed out each queue Flask-Login's notice.
    for _ in range(4):
        response = client.get("/users/auth/ui/tokens")
        assert response.status_code == 302
    messages = client.get("/get_flash_messages").get_json()["messages"]
    assert messages == [{"category": "info", "message": "Kirjaudu sisään nähdäksesi tämän sivun."}]
    assert client.get("/get_flash_messages").get_json()["messages"] == []


def test_react_flash_reader_drops_duplicates():
    source = (ROOT / "frontend/src/app/flash-notices.tsx").read_text(encoding="utf-8")
    assert "seen.has(key)" in source
