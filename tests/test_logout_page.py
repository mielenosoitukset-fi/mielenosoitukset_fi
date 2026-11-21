"""The React sign-out confirmation: Flask still signs out and answers prefetches."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULE = 'RewriteRule "^/users/auth/logout$" "http://127.0.0.1:8090$0" [P,L]'


def test_routing_claims_only_navigations_of_the_exact_path():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    assert RULE in routing
    conditions = routing.split(RULE)[0].rstrip().splitlines()[-3:]
    assert conditions == [
        'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"',
        'RewriteCond "%{HTTP:Sec-Purpose} %{HTTP:Purpose}" "!(prefetch|prerender)" [NC]',
        'RewriteCond %{HTTP:Sec-Fetch-Mode} "^(navigate)?$" [NC]',
    ]


def test_page_posts_the_session_token_to_flask():
    screen = (ROOT / "frontend/src/app/LogoutScreen.tsx").read_text(encoding="utf-8")
    route = (ROOT / "frontend/src/app/logout-route.ts").read_text(encoding="utf-8")
    render = (ROOT / "frontend/src/app/render.tsx").read_text(encoding="utf-8")
    assert "export const logoutPath = '/users/auth/logout';" in route
    assert 'action={logoutPath} method="post"' in screen
    assert '<input type="hidden" name="logout_token" value={session.logoutToken} />' in screen
    block = render.split("if (route.name === 'logout') {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (!session.authenticated && !session.unverified) return { status: 302, html: '', redirect: config.urls.login," in block
    assert "headers['Cache-Control'] = 'private, no-store';" in block


def test_flask_prefetch_and_post_contract_is_unchanged(client, user_client):
    prefetch = user_client.get("/users/auth/logout", headers={"Sec-Purpose": "prefetch"})
    assert prefetch.status_code == 204
    # A POST without the token never signs anyone out.
    refused = user_client.post("/users/auth/logout", data={})
    assert refused.status_code == 302 and refused.headers["Location"].endswith("/users/auth/logout")
    assert client.get("/users/auth/logout").status_code == 302
