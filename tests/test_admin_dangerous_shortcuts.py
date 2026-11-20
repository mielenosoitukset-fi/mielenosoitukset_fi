"""Admin actions with big effects never happen by accident or leak scope."""

from bson import ObjectId


def test_demo_list_has_no_shift_click_instant_delete(admin_client):
    page = admin_client.get("/admin/demo/").get_data(as_text=True)
    assert "fastDeleteMode" not in page
    assert "Pikapoisto" not in page


def test_command_center_cancel_aborts_when_prompt_is_dismissed(admin_client, seeded_data):
    page = admin_client.get(
        f"/admin/demo/command-center/{seeded_data['pending_demo_id']}"
    ).get_data(as_text=True)
    assert "if (reason === null) return;" in page


def test_command_center_hides_cancel_for_cancelled_demo(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    url = f"/admin/demo/command-center/{demo_id}"
    assert 'data-command="cancel"' in admin_client.get(url).get_data(as_text=True)
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"cancelled": True}})
    page = admin_client.get(url).get_data(as_text=True)
    assert 'data-command="cancel"' not in page
    assert 'data-command="duplicate"' in page


def test_sidebar_tasks_are_cached_per_admin(app, db, seeded_data):
    from tests.conftest import _client_for_user
    from tests.test_city_scoped_admin import _create_scoped_admin

    foreign = {
        "_id": ObjectId(), "title": "Turun salainen tehtävä", "city": "Turku",
        "city_key": "turku", "date": "2099-01-01", "approved": False, "rejected": False,
        "hide": False,
    }
    db.demonstrations.insert_one(foreign)
    city_admin = _create_scoped_admin(db, ["helsinki"], ["LIST_DEMOS", "VIEW_DEMO"])

    admin_page = _client_for_user(app, seeded_data["admin_id"]).get("/admin/demo/")
    city_page = _client_for_user(app, city_admin).get("/admin/demo/")

    assert "Turun salainen tehtävä" in admin_page.get_data(as_text=True)
    assert "Turun salainen tehtävä" not in city_page.get_data(as_text=True)


def test_demo_list_has_no_dead_delete_helpers_or_inline_titles(admin_client, db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$set": {"title": "x'); alert(1); ('"}})
    page = admin_client.get("/admin/demo/").get_data(as_text=True)
    assert "updateDeleteButtonTexts" not in page
    assert "openModal(event, this.dataset.title, this.dataset.id)" in page
    assert "openModal(event, '" not in page
