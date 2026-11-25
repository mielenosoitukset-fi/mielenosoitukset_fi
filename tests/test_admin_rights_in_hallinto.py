"""The site admin shows rights read-only and never writes them."""

import pytest
from bson import ObjectId


@pytest.fixture
def hallinto_lock(app):
    app.config["HALLINTO_URL"] = "https://hallinto.example/"
    yield
    app.config["HALLINTO_URL"] = None


def _target(db):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "rights-target", "email": "rights@example.test",
                         "displayname": "Rights Target", "role": "user", "global_permissions": [],
                         "confirmed": True, "active": True, "password_hash": "x"})
    return user_id


def test_edit_page_shows_rights_read_only_with_hallinto_dialog(admin_client, db, seeded_data, hallinto_lock):
    user_id = _target(db)
    page = admin_client.get(f"/admin/user/edit_user/{user_id}").get_data(as_text=True)
    assert 'name="role"' not in page
    assert 'name="permissions[global][]"' not in page
    assert 'name="admin_scope_cities[]"' not in page
    assert "Oikeudet muutetaan Hallinnossa" in page
    assert 'href="https://hallinto.example/"' in page


@pytest.mark.parametrize("path", ["save_user", "edit_user"])
def test_forms_never_write_rights(admin_client, db, seeded_data, hallinto_lock, path):
    user_id = _target(db)
    response = admin_client.post(f"/admin/user/{path}/{user_id}", data={
        "username": "rights-target", "displayname": "Renamed", "email": "rights@example.test",
        "confirmed": "on", "role": "admin", "permissions[global][]": ["EDIT_USER", "DELETE_DEMO"],
        "admin_scope_cities[]": ["helsinki"], "admin_scope_permissions[city][]": ["VIEW_DEMO"],
    })
    assert response.status_code in (302, 303)
    doc = db.users.find_one({"_id": user_id})
    assert doc["displayname"] == "Renamed"
    assert doc["role"] == "user"
    assert doc.get("global_permissions") == []
    assert db.admin_scope_grants.count_documents({"user_id": {"$in": [user_id, str(user_id)]}}) == 0


def test_create_user_only_makes_plain_accounts(admin_client, db, seeded_data, hallinto_lock):
    admin_client.post("/admin/user/create_user", data={"email": "newadmin@example.test", "role": "admin"})
    assert db.users.find_one({"email": "newadmin@example.test"}) is None
    page = admin_client.get("/admin/user/").get_data(as_text=True)
    assert 'id="create-user-role" name="role" value="user"' in page
