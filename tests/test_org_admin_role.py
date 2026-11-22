"""org_admin follows owner/admin membership; Hallinto can only end it."""

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import grant_proofs, org_admin_grants as oag
from mielenosoitukset_fi.admin.hallinto_city_grant_api import AUDIENCE, org_admin_end_attestation_message
from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils import org_admin_role as role
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import SIGNER, _b64url, _headers, api  # noqa: F401

END_KEY = "hallinto-orgadmin-end-000001"


def _person(db, name, **updates):
    uid = ObjectId()
    db.users.insert_one(_user_doc(name, f"{name}@example.test", "Passw0rd!x", _id=uid, **updates))
    return uid


def _org(db, name="Sync org"):
    org_id = ObjectId()
    db.organizations.insert_one({"_id": org_id, "name": name, "description": "", "email": "o@org.test",
                                 "verified": False, "members": []})
    return org_id


def _member(db, org_id, user_id, role_name):
    db.memberships.insert_one({"user_id": user_id, "organization_id": org_id, "role": role_name,
                               "permissions": []})


def _grants(db, user_id, **extra):
    return list(db.admin_scope_grants.find({"user_id": user_id, "template": role.TEMPLATE, **extra}))


def test_owner_and_admin_get_role_and_grant_member_does_not(db, app):
    org = _org(db)
    owner, admin, member = (_person(db, f"sync-{n}") for n in ("owner", "admin", "member"))
    _member(db, org, owner, "owner")
    _member(db, org, admin, "admin")
    _member(db, org, member, "member")
    with app.app_context():
        totals = oag.sync_all(db)
    assert totals["added"] >= 2 and totals["failed"] == 0  # other tests may leave owners behind
    for uid in (owner, admin):
        assert db.users.find_one({"_id": uid})["role"] == "org_admin"
        grant = _grants(db, uid)[0]
        assert grant["scope_type"] == "organization" and grant["scope_keys"] == [str(org)]
        assert grant["permissions"] == role.PERMISSIONS and grant["revoked_at"] is None
    assert db.users.find_one({"_id": member})["role"] == "user"
    assert _grants(db, member) == []


def test_sync_is_idempotent(db, app):
    org = _org(db)
    admin = _person(db, "sync-twice")
    _member(db, org, admin, "admin")
    with app.app_context():
        oag.sync_account(db, admin)
        again = oag.sync_account(db, admin)
    assert again["added"] == [] and again["ended"] == []
    assert len(_grants(db, admin)) == 1


def test_losing_admin_ends_grant_and_role_when_none_left(db, app):
    org_a, org_b = _org(db, "A"), _org(db, "B")
    admin = _person(db, "sync-lose")
    _member(db, org_a, admin, "admin")
    _member(db, org_b, admin, "owner")
    with app.app_context():
        oag.sync_account(db, admin)
        db.memberships.update_one({"user_id": admin, "organization_id": org_a}, {"$set": {"role": "member"}})
        oag.sync_account(db, admin)
        assert db.users.find_one({"_id": admin})["role"] == "org_admin"  # B is left
        live = [g["scope_keys"] for g in _grants(db, admin, revoked_at=None)]
        assert live == [[str(org_b)]]
        db.memberships.delete_one({"user_id": admin, "organization_id": org_b})
        oag.sync_account(db, admin)
    assert db.users.find_one({"_id": admin})["role"] == "user"
    assert _grants(db, admin, revoked_at=None) == []
    assert {g["revoked_reason"] for g in _grants(db, admin)} == {"membership_ended"}


def test_other_site_role_is_kept(db, app):
    org = _org(db)
    translator = _person(db, "sync-translator", role="translator")
    _member(db, org, translator, "admin")
    with app.app_context():
        oag.sync_account(db, translator)
    assert db.users.find_one({"_id": translator})["role"] == "translator"
    assert len(_grants(db, translator, revoked_at=None)) == 1


def test_system_team_organisation_gets_no_grant(db, app, monkeypatch):
    org = _org(db, "Board")
    person = _person(db, "sync-board")
    _member(db, org, person, "admin")
    monkeypatch.setattr("mielenosoitukset_fi.utils.system_teams.protected_organization", lambda oid: str(oid) == str(org))
    with app.app_context():
        oag.sync_account(db, person)
    assert _grants(db, person) == []


def test_grant_raises_membership_view(db, app):
    org = _org(db)
    admin = _person(db, "sync-view")
    _member(db, org, admin, "admin")
    with app.app_context():
        oag.sync_account(db, admin)
        user = User.from_db(db.users.find_one({"_id": admin}))
        membership = user.membership_for(org)
        assert set(role.PERMISSIONS) <= set(membership.permissions)
        assert user.has_permission("EDIT_ORGANIZATION", org)
        assert not user.has_permission("EDIT_ORGANIZATION", ObjectId())
        with pytest.raises(ValueError):
            membership.save()  # the raised view is never stored


def test_membership_save_syncs_when_enabled(db, app):
    from mielenosoitukset_fi.utils.classes.MemberShip import MemberShip

    org = _org(db)
    person = _person(db, "sync-hook")
    app.config["ORG_ADMIN_ROLE_ENABLED"] = True
    with app.app_context():
        MemberShip(user_id=person, organization_id=org, role="admin").save()
        assert db.users.find_one({"_id": person})["role"] == "org_admin"
        MemberShip(user_id=person, organization_id=org, role="member").save()
    assert db.users.find_one({"_id": person})["role"] == "user"


def test_membership_save_does_not_sync_when_flag_off(db, app):
    from mielenosoitukset_fi.utils.classes.MemberShip import MemberShip

    org = _org(db)
    person = _person(db, "sync-off")
    app.config["ORG_ADMIN_ROLE_ENABLED"] = False
    with app.app_context():
        MemberShip(user_id=person, organization_id=org, role="admin").save()
    assert _grants(db, person) == []


# --- Hallinto: end, freeze and reads ---------------------------------------------------------

def _end_body(account, org, kind="revoke", end_id=END_KEY, signer=SIGNER, **over):
    body = {"end_id": end_id, "kind": kind, "account_id": str(account), "organization_id": str(org),
            "template": role.TEMPLATE, "decision_id": "synthetic-orgadmin", "decision_revision": 1,
            "authority_attestation": "placeholder", **over}
    digest = oag.end_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(org_admin_end_attestation_message(digest)))
    return body, digest


def _post_end(env, body, digest, key=END_KEY):
    app, client, db, _b, _d, _p, seeded, _t = env
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_ORG_ADMIN_END_V1, intent_digest=digest,
                                         factor="passkey", subject=body["account_id"])
    return client.post("/internal/hallinto/v1/org-admin-ends", json=body, headers=_headers(proof=proof, key=key))


@pytest.fixture
def orgadmin(api):
    app, client, db, _b, _d, _p, seeded, _t = api
    app.config["ORG_ADMIN_HALLINTO_ENABLED"] = True
    app.config["ORG_ADMIN_ROLE_ENABLED"] = True
    org = _org(db)
    person = _person(db, "hallinto-orgadmin")
    _member(db, org, person, "admin")
    with app.app_context():
        oag.sync_account(db, person)
    return api, org, person


def test_end_routes_are_absent_unless_enabled(api):
    app, client, db, *_ = api
    body, digest = _end_body(ObjectId(), ObjectId())
    assert _post_end(api, body, digest).status_code == 404
    assert client.get(f"/internal/hallinto/v1/org-admins/{ObjectId()}", headers=_headers()).status_code == 404


def test_revoke_ends_grant_blocks_regrant_until_membership_changes(orgadmin):
    env, org, person = orgadmin
    app, client, db = env[:3]
    body, digest = _end_body(person, org)
    response = _post_end(env, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["effective"]["organizations"] == []
    assert receipt["effective"]["role"] == "user"
    assert db.users.find_one({"_id": person})["role"] == "user"
    with app.app_context():
        oag.sync_account(db, person)  # still admin of the organisation: no new grant
    assert _grants(db, person, revoked_at=None) == []
    db.memberships.update_one({"user_id": person, "organization_id": org}, {"$set": {"role": "member"}})
    with app.app_context():
        oag.sync_account(db, person)  # block lifted
        db.memberships.update_one({"user_id": person, "organization_id": org}, {"$set": {"role": "admin"}})
        oag.sync_account(db, person)
    assert len(_grants(db, person, revoked_at=None)) == 1


def test_end_replays_and_rejects_other_intent(orgadmin):
    env, org, person = orgadmin
    body, digest = _end_body(person, org, kind="freeze")
    first = _post_end(env, body, digest)
    assert first.status_code == 200
    assert _post_end(env, body, digest).get_json()["operation_id"] == first.get_json()["operation_id"]
    other, other_digest = _end_body(person, org, kind="revoke")
    assert _post_end(env, other, other_digest).status_code == 409


def test_end_needs_org_admin_signature_context(orgadmin):
    from mielenosoitukset_fi.admin.hallinto_city_grant_api import bundle_end_attestation_message

    env, org, person = orgadmin
    body, digest = _end_body(person, org)
    body["authority_attestation"] = _b64url(SIGNER.sign(bundle_end_attestation_message(digest)))
    assert _post_end(env, body, digest).status_code == 403
    assert len(_grants(env[2], person, revoked_at=None)) == 1


def test_end_for_unknown_grant_is_unsupported(orgadmin):
    env, _org_id, person = orgadmin
    body, digest = _end_body(person, ObjectId())
    assert _post_end(env, body, digest).status_code == 422


def test_reads(orgadmin):
    env, org, person = orgadmin
    client = env[1]
    state = client.get(f"/internal/hallinto/v1/org-admins/{person}", headers=_headers()).get_json()
    assert state["role"] == "org_admin" and state["grants"][0]["status"] == "live"
    listing = client.get(f"/internal/hallinto/v1/organizations/{org}/org-admins", headers=_headers()).get_json()
    assert [row["account_id"] for row in listing["org_admins"]] == [str(person)]
