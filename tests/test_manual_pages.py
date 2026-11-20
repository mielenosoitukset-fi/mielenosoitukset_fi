import json
import re

import pytest

from mielenosoitukset_fi.admin import manual_registry


MANUAL_PAGES = [slug for slug in manual_registry.PAGES if slug != "index"]


def _search_index(body):
    match = re.search(
        r'<script type="application/json" id="manual-search-data">(.*?)</script>',
        body,
        re.S,
    )
    assert match, "search index missing"
    return json.loads(match.group(1))


@pytest.mark.parametrize("page", MANUAL_PAGES)
def test_manual_pages_render(app, admin_client, page):
    resp = admin_client.get(f"/admin/manual/{page}")
    assert resp.status_code == 200, f"{page} returned {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert "Käyttöohje" in body
    # Every searchable topic points at an element that exists on the page.
    for anchor, _title, _keywords in manual_registry.PAGES[page]["topics"]:
        assert f'id="{anchor}"' in body, f"{page}#{anchor} missing"


def test_manual_index_render(app, admin_client):
    resp = admin_client.get("/admin/manual/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for anchor, _title, _keywords in manual_registry.PAGES["index"]["topics"]:
        assert f'id="{anchor}"' in body
    entries = _search_index(body)
    urls = {entry["url"] for entry in entries}
    assert "/admin/manual/cases#kaksoiskappale" in urls
    assert "/admin/manual/account#passkey" in urls


def test_manual_index_path_redirects(app, admin_client):
    resp = admin_client.get("/admin/manual/index")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/manual/")


@pytest.mark.parametrize(
    "page",
    ["nope", "_macros", "users", "../manual_base", "..%2Fmanual_base", "users/../faq"],
)
def test_manual_serves_only_listed_pages(app, admin_client, page):
    resp = admin_client.get(f"/admin/manual/{page}")
    if page == "users":
        assert resp.status_code == 200
    else:
        assert resp.status_code == 404


def test_manual_requires_login(app, client):
    resp = client.get("/admin/manual/dashboard")
    assert resp.status_code in (302, 401, 403)


def test_manual_hides_shortcuts_without_access(app, user_client):
    resp = user_client.get("/admin/manual/cases")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The guide is readable, but there is no shortcut into the case list.
    assert 'href="/admin/case/"' not in body


def test_manual_tasks_link_to_existing_topics():
    for page, anchor, _title, access in manual_registry.TASKS:
        assert page in manual_registry.PAGES
        anchors = {topic[0] for topic in manual_registry.PAGES[page]["topics"]}
        assert anchor in anchors, f"{page}#{anchor}"
        assert access in manual_registry.ACCESS_CHECKS


def test_manual_help_links_point_at_topics():
    for endpoint, (page, anchor) in manual_registry.HELP_LINKS.items():
        assert page in manual_registry.PAGES, endpoint
        if anchor:
            anchors = {topic[0] for topic in manual_registry.PAGES[page]["topics"]}
            assert anchor in anchors, f"{endpoint} -> {page}#{anchor}"


def test_manual_help_links_name_real_endpoints(app):
    endpoints = set(app.view_functions)
    blueprints = {name.split(".", 1)[0] + "." for name in endpoints}
    for key in manual_registry.HELP_LINKS:
        if key.endswith("."):
            assert key in blueprints, key
        else:
            assert key in endpoints, key


def test_admin_sidebar_links_to_contextual_help(app, admin_client):
    resp = admin_client.get("/admin/case/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/admin/manual/cases#lista"' in body
    assert "Ohje tähän näkymään" in body
