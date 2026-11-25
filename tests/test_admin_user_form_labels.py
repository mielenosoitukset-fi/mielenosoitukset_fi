"""User form shows Finnish permission names and city checkboxes; list tells admin scopes apart (audit #17)."""

from datetime import datetime

from bson import ObjectId


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
    assert "Organisaatioadmin" in _role_badge(page, "aaa-org-only")
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


def test_user_list_shows_org_admin_from_membership(admin_client, db, seeded_data):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "aaa-plain-owner", "email": "plainowner@example.test",
                         "role": "user", "global_permissions": [], "confirmed": True})
    db.memberships.insert_one({"user_id": user_id, "organization_id": ObjectId(), "role": "owner"})
    page = admin_client.get("/admin/user/?search=aaa-plain-owner").get_data(as_text=True)
    assert "Organisaatioadmin" in _role_badge(page, "aaa-plain-owner")
    assert db.users.find_one({"_id": user_id})["role"] == "user"


def test_user_list_governance_account_needs_national_team_for_superuser_badge(admin_client, db, seeded_data):
    db.users.insert_one({"_id": ObjectId(), "username": "aaa-gov-flag", "email": "govflag@example.test",
                         "role": "admin", "global_admin": True, "governance_managed": True,
                         "global_permissions": [], "confirmed": True})
    db.users.insert_one({"_id": ObjectId(), "username": "aaa-gov-role", "email": "govrole@example.test",
                         "role": "superuser", "governance_managed": True, "global_permissions": [], "confirmed": True})
    page = admin_client.get("/admin/user/?search=aaa-gov-").get_data(as_text=True)
    assert "Superkäyttäjä" not in _role_badge(page, "aaa-gov-flag")
    assert "Superkäyttäjä" not in _role_badge(page, "aaa-gov-role")


def test_user_list_api_only_admin_is_not_site_wide(admin_client, db, seeded_data):
    db.users.insert_one({"_id": ObjectId(), "username": "aaa-api-only", "email": "apionly@example.test",
                         "role": "admin", "global_permissions": ["API_READ"], "confirmed": True})
    page = admin_client.get("/admin/user/?search=aaa-api-only").get_data(as_text=True)
    assert "Admin, koko sivusto" not in _role_badge(page, "aaa-api-only")
