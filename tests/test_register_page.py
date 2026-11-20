"""The React registration page posts to the unchanged Flask handler."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "frontend/src/app/RegisterScreen.tsx"
ROUTE = ROOT / "frontend/src/app/register-route.ts"


def test_form_posts_the_flask_fields_to_the_flask_handler():
    screen = SCREEN.read_text(encoding="utf-8")
    route = ROUTE.read_text(encoding="utf-8")
    assert "export const registerPath = '/users/auth/register';" in route
    assert 'action={registerPath} method="post"' in screen
    for field in ('name="username"', 'name="email"', 'name="password"', 'name="password_confirm"'):
        assert field in screen
    assert "dangerouslySetInnerHTML" not in screen


def test_routing_claims_only_get_and_head_of_the_exact_path():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    rule = 'RewriteRule "^/users/auth/register$" "http://127.0.0.1:8090$0" [P,L]'
    assert rule in routing
    before = routing.split(rule)[0].rstrip().splitlines()[-1]
    assert before == 'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"'
    # The next-steps page reads the Flask session and stays there.
    assert "/users/auth/register/next_steps" not in routing


def test_flask_post_still_validates(client):
    response = client.post("/users/auth/register", data={"username": "ab", "email": "x@example.test", "password": "x"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/users/auth/register")


def test_client_checks_match_flask_password_rules():
    route = ROUTE.read_text(encoding="utf-8")
    helpers = (ROOT / "mielenosoitukset_fi/utils/helpers.py").read_text(encoding="utf-8")
    assert "len(password) < 12" in helpers and "password.length >= 12" in route
    assert "if categories < 3:" in helpers and ".length >= 3" in route
    assert "crypto.getRandomValues" in SCREEN.read_text(encoding="utf-8")
    assert "Math.random" not in route + SCREEN.read_text(encoding="utf-8")


def test_page_shows_flask_flashes():
    screen = SCREEN.read_text(encoding="utf-8")
    assert "useFlashNotices()" in screen and "<FlashNotices notices={notices} />" in screen
