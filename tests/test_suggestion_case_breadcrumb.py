"""The case crumb names the case number and links to it (Olivia, 20 Nov 2026)."""


def test_suggestion_review_breadcrumb_names_and_links_the_case(admin_client, db, seeded_data):
    suggestion_id = seeded_data["suggestion_id"]
    db.cases.delete_one({"_id": suggestion_id})
    db.cases.insert_one({"_id": suggestion_id, "type": "demo_suggestion", "running_num": 104242, "meta": {}})

    html = admin_client.get(f"/admin/demo/suggestions/{suggestion_id}").get_data(as_text=True)

    assert "Tapaus #104242" in html
    assert f'href="/admin/case/{suggestion_id}/"' in html
    assert 'href="/admin/case/"' in html
    start = html.index('class="admin-breadcrumbs"')
    nav = html[start:html.index("</nav>", start)]
    assert "Tapaukset" in nav and "Tarkastelu" in nav
    assert "Mielenosoitukset" not in nav
