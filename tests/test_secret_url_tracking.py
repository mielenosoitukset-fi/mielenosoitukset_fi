from pathlib import Path

from flask import Flask

from mielenosoitukset_fi.utils.secret_urls import url_carries_secret

BASE_TEMPLATE = Path(__file__).resolve().parents[1] / "mielenosoitukset_fi" / "templates" / "base.html"


def _app():
    app = Flask(__name__)

    @app.route("/users/auth/confirm_email/<token>")
    def confirm(token):
        return ""

    @app.route("/demonstration/<demo_id>")
    def detail(demo_id):
        return ""

    return app


def _check(path):
    app = _app()
    # Pushing the context matches the URL, which fills request.view_args.
    with app.test_request_context(path):
        return url_carries_secret()


def test_token_in_path_is_secret():
    assert _check("/users/auth/confirm_email/abc123") is True


def test_token_in_query_is_secret():
    assert _check("/demonstration/1?token=abc") is True
    assert _check("/demonstration/1?invite=abc") is True


def test_plain_pages_are_not_secret():
    assert _check("/demonstration/1") is False
    assert _check("/demonstration/1?page=2") is False


def test_no_request_is_not_secret():
    assert url_carries_secret() is False


def test_base_template_skips_matomo_and_referrer_on_secret_pages():
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    assert "{% if config.get('MATOMO_ENABLED', true) and not url_carries_secret() %}" in source
    assert '{% if url_carries_secret() %}<meta name="referrer" content="no-referrer" />{% endif %}' in source


def test_matomo_loads_only_through_consent_script():
    """Verify the pages never load matomo.js directly; consent.js does it after Accept."""
    base = Path("mielenosoitukset_fi/templates/base.html").read_text(encoding="utf-8")
    client = Path("frontend/src/app/client.tsx").read_text(encoding="utf-8")
    consent = Path("mielenosoitukset_fi/static/js/consent.js").read_text(encoding="utf-8")

    assert "js/consent.js" in base and 'data-matomo="1"' in base
    assert "script src=\"{{ url_for('static', filename='js/matomo.js') }}\"" not in base
    assert "script.src = '/static/js/matomo.js'" not in client
    assert "/static/js/consent.js" in client
    # Nothing loads before a stored "granted" or a click on the accept button.
    assert "state === 'granted'" in consent
    assert 'data-choice="granted"' in consent and 'data-choice="denied"' in consent
