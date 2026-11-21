"""Copying a demo opens a filled-in create form; nothing is saved until "Luo"."""

from bson import ObjectId


def test_copy_link_opens_prefilled_create_form_without_writing(admin_client, db, seeded_data):
    demo_id = seeded_data["demo_id"]
    source = db.demonstrations.find_one({"_id": demo_id})
    before = db.demonstrations.count_documents({})

    response = admin_client.get(f"/admin/demo/create_demo?pohja={demo_id}")
    assert response.status_code == 200
    page = response.get_data(as_text=True)

    assert db.demonstrations.count_documents({}) == before
    assert f'data-copy-source="{demo_id}"' in page
    assert f'value="{source["title"]}"' in page
    assert f'value="{source["address"]}"' in page
    assert 'id="date" name="date" class="form-control" value=""' in page
    assert 'action="/admin/demo/create_demo"' in page
    # Edit-only panels stay hidden: this is a new demo.
    assert "Luo muokkauslinkki" not in page
    assert "Kopioi uuden pohjaksi" not in page


def test_copy_ignores_unknown_or_forbidden_sources(admin_client, user_client, seeded_data):
    page = admin_client.get(f"/admin/demo/create_demo?pohja={ObjectId()}").get_data(as_text=True)
    assert "data-copy-source" not in page
    page = admin_client.get("/admin/demo/create_demo?pohja=<script>").get_data(as_text=True)
    assert "data-copy-source" not in page and "<script>\"" not in page

    response = user_client.get(f"/admin/demo/create_demo?pohja={seeded_data['demo_id']}")
    assert response.status_code in (302, 401, 403)


def test_old_duplicate_endpoint_is_gone_and_buttons_link_to_form(admin_client, seeded_data):
    demo_id = seeded_data["demo_id"]
    assert admin_client.post(f"/admin/demo/duplicate/{demo_id}").status_code in (404, 405)

    copy_href = f'href="/admin/demo/create_demo?pohja={demo_id}"'
    command_center = admin_client.get(f"/admin/demo/command-center/{demo_id}").get_data(as_text=True)
    assert f'<a class="action-btn btn btn-outline-secondary" {copy_href}>' in command_center
    assert 'data-command="duplicate"' not in command_center

    edit_page = admin_client.get(f"/admin/demo/edit_demo/{demo_id}").get_data(as_text=True)
    assert copy_href in edit_page
    assert "duplicate-demo-btn" not in edit_page
