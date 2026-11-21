"""Accepting an invitation gives the invited role, but only a role set behind the role check."""

import pytest
from bson import ObjectId

from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user
from tests.test_city_scoped_admin import _create_organization_admin


def _member_of(db, org_id, role, permissions):
    user_id = _create_organization_admin(db, org_id, permissions)
    db.memberships.update_one({"user_id": user_id, "organization_id": org_id}, {"$set": {"role": role}})
    return user_id


def _invitee(db, email):
    user_doc = User.create_user(username=f"invitee-{ObjectId()}"[-20:], password="InviteePass1!", email=email, displayname="Invitee")
    user_doc.update({"confirmed": True, "active": True, "role": "user", "global_admin": False, "global_permissions": []})
    return db.users.insert_one(user_doc).inserted_id


@pytest.mark.parametrize("actor_role, actor_perms, role, allowed", [
    ("member", ["INVITE_TO_ORGANIZATION", "VIEW_ORGANIZATION", "LIST_ORGANIZATIONS"], "member", False),
    ("member", ["INVITE_TO_ORGANIZATION", "VIEW_ORGANIZATION", "LIST_ORGANIZATIONS"], "admin", False),
    ("member", ["INVITE_TO_ORGANIZATION", "VIEW_ORGANIZATION", "LIST_ORGANIZATIONS"], "owner", False),
    ("admin", ["INVITE_TO_ORGANIZATION", "EDIT_ORGANIZATION"], "member", True),
    ("admin", ["INVITE_TO_ORGANIZATION", "EDIT_ORGANIZATION"], "admin", True),
    ("admin", ["INVITE_TO_ORGANIZATION", "EDIT_ORGANIZATION"], "owner", False),
    ("owner", ["INVITE_TO_ORGANIZATION", "EDIT_ORGANIZATION", "DELETE_ORGANIZATION"], "owner", True),
])
def test_invite_role_needs_matching_authority(app, db, seeded_data, actor_role, actor_perms, role, allowed):
    org_id = seeded_data["org_id"]
    db.organizations.update_one({"_id": org_id}, {"$set": {"invitations": []}})
    actor = _member_of(db, org_id, actor_role, actor_perms)
    response = _client_for_user(app, actor).post("/admin/organization/api/set_invite_role/", json={
        "organization_id": str(org_id), "email": "new@example.test", "role": role,
    })
    stored = db.organizations.find_one({"_id": org_id}).get("invitations", [])
    if allowed:
        assert response.status_code == 200
        assert stored[0]["role"] == role and stored[0]["role_granted_by"] == actor
    else:
        assert response.status_code == 403
        assert stored == []


@pytest.mark.parametrize("route", ["get", "post"])
def test_accept_gives_checked_role_without_raising_account_role(app, db, seeded_data, route):
    org_id = seeded_data["org_id"]
    email = f"owner-{route}@example.test"
    invitee = _invitee(db, email)
    db.organizations.update_one({"_id": org_id}, {"$set": {"invitations": [
        {"email": email, "role": "owner", "role_granted_by": seeded_data["admin_id"], "invited_at": utcnow()},
    ]}})
    client = _client_for_user(app, invitee)
    if route == "get":
        client.get(f"/users/orgs/accept_invite?organization_id={org_id}")
    else:
        client.post(f"/users/orgs/accept_invite/{org_id}", headers={"Referer": "/"})
    membership = db.memberships.find_one({"user_id": invitee, "organization_id": org_id})
    assert membership["role"] == "owner"
    assert "DELETE_ORGANIZATION" in membership["permissions"]
    assert db.users.find_one({"_id": invitee})["role"] == "user"
    assert db.organizations.find_one({"_id": org_id})["invitations"] == []


@pytest.mark.parametrize("invitation", [
    lambda email: email,
    lambda email: {"email": email, "role": "owner", "invited_at": utcnow()},
])
def test_unchecked_invitation_joins_as_member(app, db, seeded_data, invitation):
    org_id = seeded_data["org_id"]
    email = f"legacy-{ObjectId()}@example.test"
    invitee = _invitee(db, email)
    db.organizations.update_one({"_id": org_id}, {"$set": {"invitations": [invitation(email)]}})
    _client_for_user(app, invitee).get(f"/users/orgs/accept_invite?organization_id={org_id}")
    membership = db.memberships.find_one({"user_id": invitee, "organization_id": org_id})
    assert membership["role"] == "member"
    assert db.users.find_one({"_id": invitee})["role"] == "user"
