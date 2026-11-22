"""Organisation admins read their own organisation's cases and add notes, nothing more."""

from copy import deepcopy

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils.classes.MemberShip import MemberShip
from tests.conftest import _client_for_user


def _account(db, name):
    user_id = ObjectId()
    db.users.insert_one({"_id": user_id, "username": name, "email": f"{name}@org.test",
                         "confirmed": True, "role": "user", "global_permissions": [],
                         "password_hash": "x", "active": True})
    return user_id


@pytest.fixture
def org_cases(app, db, seeded_data):
    org_id = ObjectId()
    db.organizations.insert_one({"_id": org_id, "name": "Scope org", "description": "", "email": "s@org.test",
                                 "verified": True, "members": []})
    people = {}
    for role in ("admin", "member"):
        people[role] = _account(db, f"scope-{role}")
        db.memberships.insert_one({"user_id": people[role], "organization_id": org_id, "role": role,
                                   "permissions": MemberShip.DEFAULT_PERMISSIONS[role]})

    base_demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    own_demo = deepcopy(base_demo)
    own_demo.update({"_id": ObjectId(), "title": "Own org demo",
                     "organizers": [{"name": "Scope org", "organization_id": org_id}]})
    db.demonstrations.insert_one(own_demo)

    base_case = db.cases.find_one({"_id": seeded_data["case_id"]})
    own_case = deepcopy(base_case)
    own_case.update({
        "_id": ObjectId(), "demo_id": own_demo["_id"], "organization_id": None, "running_num": 777001,
        "submitter": {"submitter_email": "stranger@example.test", "submitter_name": "Stranger Person"},
        "action_logs": [{"timestamp": base_case["created_at"], "user": "moderator-mia",
                         "action_type": "note", "reason": "Moderator secret note"}],
        "case_history": [{"timestamp": base_case["created_at"], "action": "created",
                          "user": "moderator-mia", "metadata": {}}],
    })
    db.cases.insert_one(own_case)
    db.submitters.insert_one({"demonstration_id": own_demo["_id"], "submitter_email": "stranger@example.test",
                              "submitter_name": "Stranger Person"})
    return {
        "org_id": org_id, "own_case": own_case["_id"], "foreign_case": seeded_data["case_id"],
        "admin": _client_for_user(app, people["admin"]), "member": _client_for_user(app, people["member"]),
    }


def test_org_admin_lists_only_own_cases(org_cases):
    page = org_cases["admin"].get("/admin/case/?tila=all")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Own org demo" in html
    assert str(org_cases["own_case"]) in html
    assert "100001" not in html
    assert "stranger@example.test" not in html


def test_org_admin_opens_own_case_without_other_peoples_details(org_cases):
    page = org_cases["admin"].get(f"/admin/case/{org_cases['own_case']}/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Own org demo" in html
    assert "stranger@example.test" not in html
    assert "Stranger Person" not in html
    assert "Moderator secret note" not in html
    assert "moderator-mia" not in html


def test_org_admin_cannot_open_or_change_other_cases(org_cases):
    client = org_cases["admin"]
    foreign = org_cases["foreign_case"]
    assert client.get(f"/admin/case/{foreign}/").status_code == 403
    assert client.post(f"/admin/case/{foreign}/add_action/",
                       data={"action_type": "note", "reason": "x"}).status_code == 403


@pytest.mark.parametrize("route", ["close/", "reopen/", "link_demo/", "link_org/", "approve_cancellation",
                                   "decline_cancellation", "deescalate", "unlink_demo/", "update_demo/"])
def test_org_admin_cannot_use_moderator_routes_on_own_case(org_cases, route):
    response = org_cases["admin"].post(f"/admin/case/{org_cases['own_case']}/{route}", data={})
    assert response.status_code == 403


def test_org_admin_note_is_marked_and_shown_to_org(org_cases, db):
    client = org_cases["admin"]
    response = client.post(f"/admin/case/{org_cases['own_case']}/add_action/",
                           data={"action_type": "note", "reason": "Tama on meidan mielenosoitus"})
    assert response.status_code == 302
    note = db.cases.find_one({"_id": org_cases["own_case"]})["action_logs"][-1]
    assert note["org_note"] is True
    assert note["organization_ids"] == [str(org_cases["org_id"])]
    html = client.get(f"/admin/case/{org_cases['own_case']}/").get_data(as_text=True)
    assert "Tama on meidan mielenosoitus" in html


def test_plain_member_sees_no_cases(org_cases):
    assert org_cases["member"].get("/admin/case/").status_code == 403
    assert org_cases["member"].get(f"/admin/case/{org_cases['own_case']}/").status_code == 403


def test_org_admin_without_approval_right_cannot_decide(org_cases, db):
    response = org_cases["admin"].post(f"/admin/case/{org_cases['own_case']}/demo_action", json={"action": "accept"})
    assert response.status_code == 403
    demo_id = db.cases.find_one({"_id": org_cases["own_case"]})["demo_id"]
    assert not db.demonstrations.find_one({"_id": demo_id}).get("approved")


def test_org_admins_are_case_notice_recipients(org_cases, db):
    from mielenosoitukset_fi.utils.case_notifications import case_recipients

    names = {doc["username"] for doc, _user in case_recipients(db, org_cases["own_case"])}
    assert "scope-admin" in names
    assert "scope-member" not in names
    foreign = {doc["username"] for doc, _user in case_recipients(db, org_cases["foreign_case"])}
    assert "scope-admin" not in foreign
