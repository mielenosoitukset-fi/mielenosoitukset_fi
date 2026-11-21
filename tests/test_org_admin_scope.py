"""Organisation admins (role "admin", no global rights) stay inside their own organisation."""

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils.classes.MemberShip import MemberShip
from tests.conftest import _client_for_user


@pytest.fixture
def org_owner_client(app, db, seeded_data):
    """Owner of a separate organisation, promoted the way the invite flow does it."""
    org_id = ObjectId()
    db.organizations.insert_one({"_id": org_id, "name": "Own org", "verified": False, "members": []})
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "own-org-owner", "email": "owner@own-org.test",
                         "confirmed": True, "role": "admin", "global_permissions": [],
                         "password_hash": "x", "active": True})
    db.memberships.insert_one({"user_id": user_id, "organization_id": org_id, "role": "owner",
                               "permissions": MemberShip.DEFAULT_PERMISSIONS["owner"]})
    return _client_for_user(app, user_id)


@pytest.mark.parametrize("path", [
    "/admin/dashboard/login-feed",
    "/admin/dashboard/data",
    "/admin/dashboard/panic/status",
    "/admin/status",
])
def test_org_admin_cannot_open_site_wide_surfaces(org_owner_client, path):
    assert org_owner_client.get(path).status_code == 403


def test_org_admin_cannot_clear_site_cache(org_owner_client):
    assert org_owner_client.post("/admin/dashboard/cache/clear").status_code == 403


def test_org_admin_cannot_view_or_delete_other_organisation(org_owner_client, db, seeded_data):
    other = seeded_data["org_id"]
    assert org_owner_client.get(f"/admin/organization/view/{other}").status_code == 403
    assert org_owner_client.get(f"/admin/organization/confirm_delete/{other}").status_code == 403
    response = org_owner_client.post(f"/admin/organization/delete/{other}", data={"confirm_delete": "1"})
    assert response.status_code == 403
    assert db.organizations.find_one({"_id": other}) is not None


def test_org_admin_cannot_change_other_recurring_demo(org_owner_client, db, seeded_data):
    recu = seeded_data["recu_demo_id"]
    assert org_owner_client.get(f"/admin/recu_demo/edit_recu_demo/{recu}").status_code == 403
    assert org_owner_client.post(f"/admin/recu_demo/{recu}/bulk-cancel-children",
                                 data={"child_scope": "all_future"}).status_code == 403
    response = org_owner_client.post(f"/admin/recu_demo/delete_recu_demo/{recu}", data={"confirm_delete": "1"})
    assert response.status_code == 403
    assert db.recu_demos.find_one({"_id": recu}) is not None


def test_org_admin_cannot_read_other_unapproved_demo(org_owner_client, seeded_data):
    response = org_owner_client.get(f"/api/admin/demonstrations/{seeded_data['pending_demo_id']}")
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/admin/demo/translations", "/admin/ui-translations"])
def test_org_admin_is_not_a_translation_reviewer(org_owner_client, path):
    assert org_owner_client.get(path).status_code == 403


def test_org_owner_keeps_own_organisation_and_recurring_demo(app, db, seeded_data):
    org_id = ObjectId()
    db.organizations.insert_one({"_id": org_id, "name": "Mine", "description": "d", "email": "mine-org@x.test", "verified": False, "members": []})
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "mine-owner", "email": "mine@x.test", "confirmed": True,
                         "role": "admin", "global_permissions": [], "password_hash": "x", "active": True})
    db.memberships.insert_one({"user_id": user_id, "organization_id": org_id, "role": "owner",
                               "permissions": MemberShip.DEFAULT_PERMISSIONS["owner"]})
    recu_id = ObjectId()
    db.recu_demos.insert_one({**db.recu_demos.find_one({"_id": seeded_data["recu_demo_id"]}), "_id": recu_id,
                              "organizers": [{"organization_id": org_id, "name": "Mine"}]})
    client = _client_for_user(app, user_id)
    assert client.get(f"/admin/organization/view/{org_id}").status_code == 200
    assert client.get(f"/admin/recu_demo/edit_recu_demo/{recu_id}").status_code == 200
    response = client.post(f"/admin/recu_demo/delete_recu_demo/{recu_id}", data={"confirm_delete": "1"})
    assert response.status_code == 302
    assert db.recu_demos.find_one({"_id": recu_id}) is None


def test_site_wide_admin_keeps_global_surfaces(app, db, seeded_data):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "site-admin", "email": "site@x.test", "confirmed": True,
                         "role": "admin", "global_permissions": ["VIEW_DEMO", "LIST_DEMOS"],
                         "password_hash": "x", "active": True})
    client = _client_for_user(app, user_id)
    assert client.get("/admin/dashboard/login-feed").status_code == 200
    assert client.get("/admin/status").status_code == 200
    assert client.get(f"/api/admin/demonstrations/{seeded_data['pending_demo_id']}").status_code == 200


def test_org_owner_with_user_role_reaches_admin_panel_for_own_org(app, db, seeded_data):
    org_id = ObjectId()
    db.organizations.insert_one({"_id": org_id, "name": "Plain", "description": "d", "email": "plain-org@x.test",
                                 "verified": False, "members": []})
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": "plain-owner", "email": "plain@x.test", "confirmed": True,
                         "role": "user", "global_permissions": [], "password_hash": "x", "active": True})
    db.memberships.insert_one({"user_id": user_id, "organization_id": org_id, "role": "owner",
                               "permissions": MemberShip.DEFAULT_PERMISSIONS["owner"]})
    client = _client_for_user(app, user_id)
    assert client.get(f"/admin/organization/view/{org_id}").status_code == 200
    assert client.get(f"/admin/organization/view/{seeded_data['org_id']}").status_code == 403
    assert client.get("/admin/status").status_code == 403
