"""User form shows Finnish permission names and city checkboxes; list tells admin scopes apart (audit #17)."""

from datetime import datetime

from bson import ObjectId


def test_city_permissions_use_descriptions_not_codes(admin_client, seeded_data):
    page = admin_client.get(f"/admin/user/edit_user/{seeded_data['user_id']}").get_data(as_text=True)
    assert '<label for="perm_city_EDIT_DEMO">EDIT_DEMO</label>' not in page
    assert 'for="perm_city_EDIT_DEMO">' in page


def test_user_list_separates_city_only_admins(admin_client, db, seeded_data):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "aaa-city-only", "email": "cityonly@example.test",
                         "role": "admin", "global_permissions": [], "confirmed": True})
    db.admin_scope_grants.insert_one({"user_id": user_id, "scope_type": "city", "scope_keys": ["helsinki"],
                                      "permissions": ["VIEW_DEMO"], "granted_at": datetime.utcnow()})
    page = admin_client.get("/admin/user/?search=aaa-city-only").get_data(as_text=True)
    assert "Admin, vain paikkakunnat" in page
    assert "Admin, koko sivusto" not in page
