"""One row pattern for the admin demo list (pending, approved, rejected)."""

import re

from bson import ObjectId


def _row(page, demo_id):
    match = re.search(rf'<tr id="demo-{demo_id}".*?</tr>', page, re.S)
    assert match, f"row for {demo_id} missing"
    return match.group(0)


def test_every_row_links_title_to_command_center(admin_client, seeded_data):
    page = admin_client.get("/admin/demo/?show_past=all").get_data(as_text=True)
    for key in ("demo_id", "pending_demo_id", "child_demo_id"):
        demo_id = seeded_data[key]
        row = _row(page, demo_id)
        assert f'class="admin-entity-title-link" href="/admin/demo/command-center/{demo_id}"' in row
        # Same three quick buttons and menu on every row; no duplicate "Katsele".
        assert "command-center-btn" in row
        assert "Katsele" not in row
        assert 'class="dropdown-item disabled"' not in row


def test_row_menu_offers_only_actions_that_apply(admin_client, seeded_data):
    page = admin_client.get("/admin/demo/?show_past=all").get_data(as_text=True)

    pending = _row(page, seeded_data["pending_demo_id"])
    assert "Esikatsele (ei julkinen)" in pending
    assert "Avaa julkinen sivu" not in pending
    assert "Suosittele" not in pending
    assert "Luo esikatselukuva" not in pending
    assert "openFreezeModal" not in pending

    approved = _row(page, seeded_data["demo_id"])
    assert "Avaa julkinen sivu" in approved
    assert "Suosittele" in approved or "Poista suositus" in approved
    assert "Käsittele" not in approved
    assert "openFreezeModal" not in approved  # not part of a series

    child = _row(page, seeded_data["child_demo_id"])
    assert "openFreezeModal" in child
    assert "Jäädytä tämä toistuva tapahtuma" in child


def test_rejected_rows_use_the_same_pattern(admin_client, db, seeded_data):
    rejected_id = ObjectId()
    base = db.demonstrations.find_one({"_id": seeded_data["demo_id"]})
    base.update({"_id": rejected_id, "title": "Hylätty testi", "approved": False, "rejected": True, "slug": None})
    db.demonstrations.insert_one(base)

    page = admin_client.get("/admin/demo/?approved=rejected&show_past=all").get_data(as_text=True)
    row = _row(page, rejected_id)
    assert 'class="admin-entity-title-link"' in row
    assert "Hylätty" in row
    assert "Esikatsele (ei julkinen)" in row
    assert "Muokkaushistoria" in row
    assert "data-demo-edit-link-trigger" not in row
    assert "Muokkaa tietoja" not in row
    assert "Käsittele" not in row
    assert " disabled" in row.split("</td>", 1)[0]  # cannot be bulk-selected


def test_org_and_series_titles_link_to_their_admin_page(admin_client, db, seeded_data):
    page = admin_client.get("/admin/organization/").get_data(as_text=True)
    assert re.search(r'class="admin-entity-title-link" href="/admin/organization/view/[0-9a-f]{24}"', page)

    page = admin_client.get("/admin/recu_demo/").get_data(as_text=True)
    assert 'class="admin-entity-title-link"' in page
    assert 'data-tooltip="Ei käyttöoikeutta"' not in page


def test_org_list_drops_unsafe_website_links(admin_client, db, seeded_data):
    db.organizations.update_many({}, {"$set": {"website": "javascript:alert(1)"}})
    page = admin_client.get("/admin/organization/").get_data(as_text=True)
    assert "javascript:alert(1)" not in page
