from datetime import date
from urllib.parse import urlparse

CANONICAL_SITEMAP_URL = "https://mielenosoitukset.fi/sitemap.xml"
SITEMAP_LINE = f"Sitemap: {CANONICAL_SITEMAP_URL}"

EXISTING_DIRECTIVES = [
    "User-agent: *",
    "Disallow: /admin/",
    "Disallow: /users/auth/login/",
    "Disallow: /users/auth/register/",
    "Disallow: /users/auth/forgot/",
]


def _production_robots(app):
    response = app.test_client().get(
        "/robots.txt", base_url="https://mielenosoitukset.fi"
    )
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    return response.get_data(as_text=True)


def test_robots_txt_advertises_canonical_sitemap(app):
    body = _production_robots(app)

    assert SITEMAP_LINE in body
    assert "sitemap.xml?" not in body


def test_robots_txt_keeps_existing_directives_and_single_sitemap(app):
    body = _production_robots(app)
    lines = [line.strip() for line in body.splitlines() if line.strip()]

    for directive in EXISTING_DIRECTIVES:
        assert directive in lines

    sitemap_lines = [line for line in lines if line.lower().startswith("sitemap:")]
    assert sitemap_lines == [SITEMAP_LINE]

    for line in lines:
        assert line == SITEMAP_LINE or any(
            line.lower().startswith(prefix)
            for prefix in ("user-agent:", "disallow:", "allow:")
        )

    parsed = urlparse(sitemap_lines[0].split(":", 1)[1].strip())
    assert parsed.scheme == "https"
    assert parsed.netloc == "mielenosoitukset.fi"
    assert parsed.path == "/sitemap.xml"
    assert parsed.query == ""
    assert parsed.fragment == ""


def test_robots_txt_blocks_calendar_for_ai_crawlers_not_search_engines(app):
    body = _production_robots(app)
    groups = [g for g in body.split("\n\n") if "User-agent:" in g]

    ai = next(g for g in groups if "User-agent: GPTBot" in g)
    assert "User-agent: meta-externalagent" in ai
    assert "Disallow: /calendar/" in ai
    assert "Disallow: /city/*/calendar/" in ai

    everyone = next(g for g in groups if "User-agent: *" in g)
    assert "/calendar/" not in everyone
    assert "Googlebot" not in body and "bingbot" not in body


def test_robots_txt_lets_ai_crawlers_read_current_year_calendar(app):
    year = date.today().year
    body = _production_robots(app)
    ai = next(g for g in body.split("\n\n") if "User-agent: GPTBot" in g)
    lines = ai.splitlines()

    assert f"Allow: /calendar/{year}/" in lines
    assert f"Allow: /city/*/calendar/{year}/" in lines
    assert lines.index(f"Allow: /calendar/{year}/") < lines.index("Disallow: /calendar/")


def test_calendar_far_years_are_noindex_and_nearby_years_are_not(app):
    year = date.today().year
    client = app.test_client()

    far = client.get(f"/calendar/{year - 5}/")
    assert far.headers.get("X-Robots-Tag") == "noindex, nofollow"
    far_month = client.get(f"/calendar/{year + 5}/3/")
    assert far_month.headers.get("X-Robots-Tag") == "noindex, nofollow"

    for y in (year - 1, year, year + 1):
        assert "X-Robots-Tag" not in client.get(f"/calendar/{y}/").headers
