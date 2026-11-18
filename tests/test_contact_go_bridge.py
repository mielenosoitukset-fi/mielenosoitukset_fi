"""Read-only contact token check on /api/session for the Go handler (docs/go-contact.md)."""

from pathlib import Path

import pytest

TOKEN_HEADER = "X-Contact-Form-Token"


@pytest.fixture
def bridge_on(app):
    app.config["AUTH_BRIDGE_SCOPES"] = True
    app.config["CONTACT_MIN_FORM_SECONDS"] = 0
    yield
    app.config["AUTH_BRIDGE_SCOPES"] = False


def _issue(client):
    return client.get("/api/session?contact_form=1").get_json()["contact_form"]["csrf_token"]


def test_flag_off_ignores_the_header(client):
    token = _issue(client)
    assert "contact_token" not in client.get("/api/session", headers={TOKEN_HEADER: token}).get_json()


def test_without_the_header_nothing_is_added(client, bridge_on):
    assert "contact_token" not in client.get("/api/session").get_json()


def test_outstanding_token_is_valid_and_not_consumed(client, bridge_on):
    token = _issue(client)
    for _ in range(3):  # reading never consumes
        answer = client.get("/api/session", headers={TOKEN_HEADER: token}).get_json()["contact_token"]
        assert answer["valid"] is True
    assert answer["enabled"] is True and answer["max_age_seconds"] == 7200
    assert answer["limits"] == {"name_max": 120, "email_max": 254, "message_min": 10, "message_max": 5000}
    with client.session_transaction() as session:
        assert [entry["token"] for entry in session["contact_form_tokens"]] == [token]


def test_unknown_empty_or_foreign_tokens_are_invalid(client, app, bridge_on):
    token = _issue(client)
    for supplied in ("", "nope", token + "x", token[:-1]):
        assert client.get("/api/session", headers={TOKEN_HEADER: supplied}).get_json()["contact_token"]["valid"] is False
    # A visitor without that session cannot use the token.
    other = app.test_client()
    assert other.get("/api/session", headers={TOKEN_HEADER: token}).get_json()["contact_token"]["valid"] is False


def test_age_window_follows_flask_settings(client, app, bridge_on):
    token = _issue(client)
    app.config["CONTACT_MIN_FORM_SECONDS"] = 3600
    assert client.get("/api/session", headers={TOKEN_HEADER: token}).get_json()["contact_token"]["valid"] is False
    app.config["CONTACT_MIN_FORM_SECONDS"] = 0
    app.config["CONTACT_FORM_TOKEN_MAX_AGE_SECONDS"] = -1
    try:
        assert client.get("/api/session", headers={TOKEN_HEADER: token}).get_json()["contact_token"]["valid"] is False
    finally:
        app.config.pop("CONTACT_FORM_TOKEN_MAX_AGE_SECONDS")


def test_disabled_form_is_reported(client, app, bridge_on):
    app.config["CONTACT_FORM_ENABLED"] = False
    try:
        assert client.get("/api/session", headers={TOKEN_HEADER: "x"}).get_json()["contact_token"]["enabled"] is False
    finally:
        app.config["CONTACT_FORM_ENABLED"] = True


def test_response_stays_private_and_token_stays_out_of_logs_and_urls(client, bridge_on):
    response = client.get("/api/session", headers={TOKEN_HEADER: _issue(client)})
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Vary"] == "Cookie"


def test_go_mail_fixtures_match_the_flask_template():
    """The Go handler renders the support mail itself; keep it in step with the Jinja template."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("fixtures", root / "scripts/generate_contact_mail_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import os
    previous = os.getcwd()
    os.chdir(root)
    try:
        for name, context in module.CASES.items():
            expected = (root / f"go-api/testdata/contact_mail_{name}.html").read_bytes().decode("utf-8")
            assert module.render(context) == expected, "run scripts/generate_contact_mail_fixtures.py and update go-api/contact.go"
    finally:
        os.chdir(previous)
