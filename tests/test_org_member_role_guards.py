"""Organisation admins manage members and admins; only owners touch owners.

An organisation never loses its last owner through the member routes, and a role
change takes the old role's rights away.
"""

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils.classes.MemberShip import MemberShip
from tests.conftest import _client_for_user

CHANGE = "/admin/organization/api/change_access_level/"
DELETE = "/admin/organization/api/delete_membership/"


def _user(db, name):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": name, "email": f"{name}@org.test",
                         "confirmed": True, "role": "user", "global_permissions": [],
                         "password_hash": "x", "active": True})
    return user_id


def _member(db, org_id, user_id, role):
    return db.memberships.insert_one({
        "user_id": user_id, "organization_id": org_id, "role": role,
        "permissions": MemberShip.DEFAULT_PERMISSIONS[role],
    }).inserted_id


@pytest.fixture
def org(app, db, seeded_data):
    org_id = ObjectId()
    db.organizations.insert_one({"_id": org_id, "name": "Guard org", "description": "", "email": "guard@org.test",
                              "verified": False, "members": []})
    people = {name: _user(db, f"guard-{name}") for name in ("owner", "admin", "member")}
    memberships = {name: _member(db, org_id, uid, name) for name, uid in people.items()}
    return {"id": org_id, "users": people, "memberships": memberships,
            "client": lambda name: _client_for_user(app, people[name])}


def _role(db, org, name):
    return db.memberships.find_one({"_id": org["memberships"][name]})["role"]


def _change(org, actor, target, role):
    return org["client"](actor).post(CHANGE, json={
        "organization_id": str(org["id"]), "user_id": str(org["users"][target]), "role": role,
    })


def test_admin_cannot_make_self_owner(org, db):
    assert _change(org, "admin", "admin", "owner").status_code == 403
    assert _role(db, org, "admin") == "admin"


def test_admin_cannot_make_member_owner(org, db):
    assert _change(org, "admin", "member", "owner").status_code == 403
    assert _role(db, org, "member") == "member"


def test_admin_cannot_demote_owner(org, db):
    assert _change(org, "admin", "owner", "member").status_code == 403
    assert _role(db, org, "owner") == "owner"


def test_admin_cannot_remove_owner(org, db):
    response = org["client"]("admin").post(DELETE, json={"membership_id": str(org["memberships"]["owner"])})
    assert response.status_code == 403
    assert db.memberships.find_one({"_id": org["memberships"]["owner"]}) is not None


def test_admin_can_promote_member_to_admin_and_remove_member(org, db):
    assert _change(org, "admin", "member", "admin").status_code == 200
    assert _role(db, org, "member") == "admin"
    response = org["client"]("admin").post(DELETE, json={"membership_id": str(org["memberships"]["member"])})
    assert response.status_code == 200


def test_last_owner_cannot_step_down_or_be_removed(org, db):
    assert _change(org, "owner", "owner", "admin").status_code == 403
    response = org["client"]("owner").post(DELETE, json={"membership_id": str(org["memberships"]["owner"])})
    assert response.status_code == 403
    assert _role(db, org, "owner") == "owner"


def test_owner_can_hand_over_then_step_down(org, db):
    assert _change(org, "owner", "admin", "owner").status_code == 200
    assert _change(org, "owner", "owner", "member").status_code == 200
    assert _role(db, org, "owner") == "member"


def test_demotion_drops_old_role_rights(org, db):
    assert _change(org, "owner", "admin", "owner").status_code == 200
    assert _change(org, "owner", "owner", "member").status_code == 200
    stored = db.memberships.find_one({"_id": org["memberships"]["owner"]})["permissions"]
    assert "DELETE_ORGANIZATION" not in stored
    assert "EDIT_ORGANIZATION" not in stored
    assert set(stored) == set(MemberShip.DEFAULT_PERMISSIONS["member"])


def test_role_change_keeps_explicit_extras(org, db):
    db.memberships.update_one({"_id": org["memberships"]["member"]},
                              {"$addToSet": {"permissions": "ACCEPT_DEMO"}})
    assert _change(org, "admin", "member", "admin").status_code == 200
    assert _change(org, "admin", "member", "member").status_code == 200
    stored = db.memberships.find_one({"_id": org["memberships"]["member"]})["permissions"]
    assert "ACCEPT_DEMO" in stored  # no role grants it, so a role change leaves it
    assert "EDIT_ORGANIZATION" not in stored
