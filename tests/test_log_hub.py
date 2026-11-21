"""One log hub: same breadcrumb, tabs and way back on every log page."""

import re


def _current_tab(page):
    match = re.search(r"data-log-hub-tabs>(.*?)</nav>", page, re.S)
    assert match, "log hub tabs missing"
    current = re.findall(r'aria-current="page"><i[^>]*></i>([^<]+)</a>', match.group(1))
    return current


def test_hub_pages_share_tabs_breadcrumb_and_back_link(admin_client, app):
    with app.test_request_context():
        from flask import url_for

        urls = {
            url_for("admin.logs"): "Hallintatoiminnot",
            url_for("admin_demo.audit_timeline"): "Mielenosoitushistoria",
            url_for("admin_demo.view_super_audit_logs"): "Tekninen audit-loki",
        }
    for url, title in urls.items():
        page = admin_client.get(url).get_data(as_text=True)
        assert _current_tab(page) == [title], url
        assert 'href="/admin/logs">Lokit ja tapahtumat</a>' in page or url == "/admin/logs"
        assert "Hyväksyntöjen tapahtumaloki" in page


def test_demo_history_pages_link_each_other_and_the_demo(admin_client, seeded_data):
    demo_id = seeded_data["demo_id"]
    for url, current in (
        (f"/admin/demo/edit_history/{demo_id}", "Muokkaushistoria"),
        (f"/admin/demo/{demo_id}/audit_log", "Audit-loki"),
    ):
        response = admin_client.get(url)
        assert response.status_code == 200, url
        page = response.get_data(as_text=True)
        nav = re.search(r"data-demo-history-tabs>(.*?)</nav>", page, re.S).group(1)
        assert f"/admin/demo/command-center/{demo_id}" in nav
        assert re.findall(r'aria-current="page"><i[^>]*></i>([^<]+)</a>', nav) == [current]
