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


def _role_badge(page: str, username: str) -> str:
    row = page[page.index(f"@{username}"):]
    cell = row[row.index('class="user-role'):]
    return cell[: cell.index("</span>")]


def test_user_list_does_not_call_org_admins_site_wide(admin_client, db, seeded_data):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "aaa-org-only", "email": "orgonly@example.test",
                         "role": "admin", "global_permissions": [], "confirmed": True})
    db.memberships.insert_one({"user_id": user_id, "organization_id": ObjectId(), "role": "admin",
                               "permissions": []})
    page = admin_client.get("/admin/user/?search=aaa-org-only").get_data(as_text=True)
    assert "Admin, vain organisaatiot" in _role_badge(page, "aaa-org-only")
    assert "Admin, koko sivusto" not in page


def test_user_list_admin_role_badges(admin_client, db, seeded_data):
    users = {
        "aaa-site-admin": {"role": "admin", "global_permissions": ["VIEW_DEMO"]},
        "aaa-no-rights": {"role": "admin", "global_permissions": []},
        "aaa-flag-super": {"role": "user", "global_admin": True},
        "aaa-role-super": {"role": "superuser"},
        "aaa-both": {"role": "admin", "global_permissions": []},
    }
    for username, fields in users.items():
        user_id = ObjectId()
        db.users.insert_one({"_id": user_id, "username": username, "email": f"{username}@example.test",
                             "confirmed": True, **fields})
        if username == "aaa-both":
            db.memberships.insert_one({"user_id": user_id, "organization_id": ObjectId(), "role": "owner"})
            db.admin_scope_grants.insert_one({"user_id": user_id, "scope_type": "city", "scope_keys": ["helsinki"],
                                              "permissions": ["VIEW_DEMO"], "granted_at": datetime.utcnow()})
    page = admin_client.get("/admin/user/?search=aaa-&per_page=50").get_data(as_text=True)
    assert "Admin, koko sivusto" in _role_badge(page, "aaa-site-admin")
    assert "Admin-rooli, ei oikeuksia" in _role_badge(page, "aaa-no-rights")
    assert "Superkäyttäjä" in _role_badge(page, "aaa-flag-super")
    assert "Superkäyttäjä" in _role_badge(page, "aaa-role-super")
    assert "Admin, paikkakunnat ja organisaatiot" in _role_badge(page, "aaa-both")
