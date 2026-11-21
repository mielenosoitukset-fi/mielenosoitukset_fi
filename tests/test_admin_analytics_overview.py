"""Analytics live in one hub; the old pages redirect there."""

import re


def test_old_most_viewed_list_redirects_to_the_hub(admin_client):
    response = admin_client.get("/admin/demo_analytics?page=2")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/stats?include_past=1")


def test_old_per_demo_page_redirects_to_the_one_demo_page(admin_client, seeded_data):
    demo_id = seeded_data["demo_id"]
    response = admin_client.get(f"/admin/per_demo_analytics/{demo_id}")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/analytics/demonstration/{demo_id}")
    assert admin_client.get("/admin/per_demo_analytics/not-an-id").status_code == 404


def test_hub_tabs_on_every_analytics_page(admin_client, seeded_data):
    demo_id = seeded_data["demo_id"]
    for url, current in (
        ("/admin/stats", "Mielenosoitusten katselut"),
        ("/admin/analytics/", "Koko palvelu"),
        ("/admin/analytics/overall_24h", "Viimeiset 24 h"),
    ):
        page = admin_client.get(url).get_data(as_text=True)
        nav = re.search(r"data-analytics-tabs>(.*?)</nav>", page, re.S)
        assert nav, url
        assert re.findall(r'aria-current="page"><i[^>]*></i>([^<]+)</a>', nav.group(1)) == [current], url

    stats = admin_client.get("/admin/stats?include_past=1").get_data(as_text=True)
    assert "/admin/per_demo_analytics/" not in stats
    command_center = admin_client.get(f"/admin/demo/command-center/{demo_id}").get_data(as_text=True)
    assert f"/admin/analytics/demonstration/{demo_id}" in command_center
    demo_page = admin_client.get(f"/admin/analytics/demonstration/{demo_id}").get_data(as_text=True)
    assert "Perinteinen katseluanalytiikka" not in demo_page
    assert f'/admin/demo/command-center/{demo_id}' in demo_page


def test_sidebar_has_one_analytics_entry(admin_client):
    page = admin_client.get("/admin/stats").get_data(as_text=True)
    links = re.findall(r'class="nav-link admin-sidebar-link[^"]*" href="([^"]+)"', page)
    assert "/admin/stats" in links
    assert "/admin/analytics/" not in links
