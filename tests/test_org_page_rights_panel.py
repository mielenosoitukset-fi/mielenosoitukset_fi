"""The organisation page explains roles, hides owner steps from admins and shows the approval right."""

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import org_rights
from mielenosoitukset_fi.utils import org_approval_right as right
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user, _user_doc
from datetime import timedelta


@pytest.fixture
def page(app, db, seeded_data):
    app.config["ORG_APPROVAL_RIGHT_ENABLED"] = True
    org = ObjectId()
    db.organizations.insert_one({"_id": org, "name": "Sivuyhdistys", "description": "", "email": "s@org.test",
                                 "verified": True, "members": []})
    people = {}
    for role in ("owner", "admin", "member"):
        people[role] = ObjectId()
        db.users.insert_one(_user_doc(f"page-{role}", f"page-{role}@example.test", "Passw0rd!x", _id=people[role]))
        db.memberships.insert_one({"user_id": people[role], "organization_id": org, "role": role, "permissions": []})
    from mielenosoitukset_fi.admin import org_admin_grants as oag
    with app.app_context():
        for uid in people.values():
            oag.sync_account(db, uid)

    def get(role):
        return _client_for_user(app, people[role]).get(f"/admin/organization/view/{org}").get_data(as_text=True)

    return {"app": app, "db": db, "org": org, "people": people, "get": get}


def test_role_help_replaces_the_stale_note(page):
    html = page["get"]("admin")
    assert "Roolit ja oikeudet" in html and "org_admin-rooli" in html
    assert "Pääsy ei poistu automaattisesti" not in html


def test_admin_cannot_pick_owner_but_owner_can(page):
    assert 'value="owner"' not in page["get"]("admin").split('id="member-table-body"')[1].split("</tbody>")[0].replace(
        'value="owner" selected', "")
    owner_html = page["get"]("owner")
    assert 'value="owner"' in owner_html


def test_admin_sees_owner_row_locked(page):
    html = page["get"]("admin")
    assert "Vain omistaja muuttaa omistajan roolia" in html


def test_panel_hidden_while_feature_off(page):
    page["app"].config["ORG_APPROVAL_RIGHT_ENABLED"] = False
    assert "approval-right-panel" not in page["get"]("admin")


def test_admin_can_request_member_cannot(page):
    assert "Pyydä hyväksymisoikeutta" in page["get"]("admin")
    member = _client_for_user(page["app"], page["people"]["member"]).get(f"/admin/organization/view/{page['org']}")
    assert member.status_code in (200, 403)
    if member.status_code == 200:
        assert "Pyydä hyväksymisoikeutta" not in member.get_data(as_text=True)


def test_panel_states(page):
    db, org = page["db"], page["org"]
    db.organizations.update_one({"_id": org}, {"$set": {"rights.approve_own_demos": {
        "grant_id": "g" * 20, "status": "live", "valid_until": utcnow() + timedelta(days=30)}}})
    html = page["get"]("owner")
    assert "Voimassa" in html and "Luovu oikeudesta" in html and "Pyydä hyväksymisoikeutta" not in html
    assert "Luovu oikeudesta" not in page["get"]("admin")
    db.organizations.update_one({"_id": org}, {"$set": {"rights.approve_own_demos.status": "frozen"}})
    html = page["get"]("admin")
    assert "Jäädytetty" in html and "Pyydä hyväksymisoikeutta" not in html
    db.organizations.update_one({"_id": org}, {"$set": {"verified": False}})
    assert "Pyydä hyväksymisoikeutta" not in page["get"]("admin")


def test_open_request_and_refusal_reason_shown(page):
    db, org = page["db"], page["org"]
    admin = _client_for_user(page["app"], page["people"]["admin"])
    reply = admin.post("/admin/organization/api/request_approval_right/", json={"organization_id": str(org)})
    assert reply.status_code == 200
    html = page["get"]("admin")
    assert "Pyyntö on käsiteltävänä Hallinnossa" in html and "Pyydä hyväksymisoikeutta" not in html
    db.cases.update_one({"type": right.CASE_TYPE}, {"$set": {"meta.closed": True, "meta.outcome": "refused",
                                                              "meta.closed_reason": "Liian aikaista"}})
    html = page["get"]("admin")
    assert "Viimeisin pyyntö hylättiin:" in html and "Liian aikaista" in html
