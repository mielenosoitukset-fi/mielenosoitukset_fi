"""The React password reset request page posts to the unchanged Flask handler."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_form_posts_email_to_the_flask_handler():
    screen = (ROOT / "frontend/src/app/PasswordResetRequestScreen.tsx").read_text(encoding="utf-8")
    route = (ROOT / "frontend/src/app/password-reset-route.ts").read_text(encoding="utf-8")
    assert "export const passwordResetRequestPath = '/users/auth/password_reset_request';" in route
    assert 'action={passwordResetRequestPath} method="post"' in screen
    assert 'name="email"' in screen and "dangerouslySetInnerHTML" not in screen


def test_routing_claims_only_get_and_head():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    rule = 'RewriteRule "^/users/auth/password_reset_request$" "http://127.0.0.1:8090$0" [P,L]'
    assert rule in routing
    before = routing.split(rule)[0].rstrip().splitlines()[-1]
    assert before == 'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"'


def test_flask_post_keeps_the_same_answer(client):
    response = client.post("/users/auth/password_reset_request", data={"email": "nobody@example.test"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/users/auth/login")
