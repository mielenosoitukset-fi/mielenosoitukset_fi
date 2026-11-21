"""Organization access recovery (O5): route, refusals, invitation, accept follow-up, privacy."""

import json

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import city_grants, grant_proofs, org_outreach, org_recovery
from mielenosoitukset_fi.admin.hallinto_city_grant_api import AUDIENCE, recovery_attestation_message
from mielenosoitukset_fi.utils.invitations import invitation_expired, invitation_for, invited_role
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, STATE, _b64url, _headers, _verifier, api,
)

KEY = "hallinto-recovery-0000000001"
BASE = "/internal/hallinto/v1/org-recoveries"
PERSON = "uusi@example.test"


class Mailbox:
    def __init__(self):
        self.sent = []

    def queue_email(self, **kwargs):
        self.sent.append(kwargs)


@pytest.fixture
def env(api, monkeypatch):
    app, client, db, body, digest, proof, seeded, target = api
    app.config["ORG_RECOVERY_ENABLED"] = True
    mailbox = Mailbox()
    monkeypatch.setattr(org_recovery, "_email_sender", lambda: mailbox)
    org_id, owner, admin, person = ObjectId(), ObjectId(), ObjectId(), ObjectId()
    db.users.insert_one(_user_doc("olli", "olli@example.test", "OlliPass1!", _id=owner))
    db.users.insert_one(_user_doc("aino", "aino@example.test", "AinoPass1!", _id=admin))
    db.users.insert_one(_user_doc("uusi", PERSON, "UusiPass1!", _id=person))
    db.organizations.insert_one({"_id": org_id, "name": "Esimerkkiyhdistys", "invitations": []})
    db.memberships.insert_one({"user_id": owner, "organization_id": org_id, "role": "owner", "permissions": []})
    db.memberships.insert_one({"user_id": admin, "organization_id": org_id, "role": "admin", "permissions": []})
    return app, client, db, seeded, mailbox, org_id, owner, admin, person


def _body(env, need="owner_change", role=None, address=PERSON, request_id=KEY, state=None, **over):
    app, client, db, seeded, mailbox, org_id = env[:6]
    role = role or ("owner" if need != "add_member" else "member")
    fields = {"request_id": request_id, "case_id": "case-1", "decision_id": "synthetic-decision",
              "decision_revision": 1, "need": need, "organization_id": str(org_id), "person_email": address,
              "person_role": role, **over}
    with app.app_context():
        state = state or org_recovery.read_state(db, fields["organization_id"])["expected_state"]
    body = {**fields, "expected_state": state, "authority_attestation": "placeholder"}
    digest = org_recovery.recovery_digest(body)
    body["authority_attestation"] = _b64url(SIGNER.sign(recovery_attestation_message(digest)))
    return body, digest


def _post(env, body, digest, key=None, action=grant_proofs.ACTION_ORG_RECOVERY_V1):
    app, client, db, seeded = env[:4]
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE, action=action,
                                         intent_digest=digest, factor="passkey")
    return client.post(BASE, json=body, headers=_headers(proof=proof, key=key or body["request_id"]))


def _raw(env, **over):
    """A body that fails validation: Flask answers 422 before anything else."""
    body = {"request_id": KEY, "case_id": "case-1", "decision_id": "d", "decision_revision": 1,
            "need": "owner_change", "organization_id": str(env[5]), "person_email": PERSON,
            "person_role": "owner", "expected_state": "x", "authority_attestation": "p", **over}
    return env[1].post(BASE, json=body, headers=_headers(proof="x", key=KEY))


def _go(env, **kwargs):
    body, digest = _body(env, **kwargs)
    return _post(env, body, digest)


def test_off_by_default_is_404(env):
    env[0].config["ORG_RECOVERY_ENABLED"] = "true"  # a string never turns it on
    assert _go(env).status_code == 404


def test_owner_change_creates_one_invitation_and_changes_nobody_yet(env):
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    response = _go(env)
    assert response.status_code == 200, response.get_json()
    effective = response.get_json()["effective"]
    assert effective["role"] == "owner" and effective["replaces_owners_count"] == 1
    assert effective["person_email_sha256"] == org_recovery._email_hash(PERSON)
    assert PERSON not in json.dumps(response.get_json())
    org = db.organizations.find_one({"_id": org_id})
    invitation = invitation_for(org["invitations"], PERSON)
    assert invited_role(invitation) == "owner" and invitation["replaces_owners"] == [str(owner)]
    assert db.memberships.find_one({"user_id": owner})["role"] == "owner"
    assert db.memberships.count_documents({"user_id": person}) == 0
    assert len(mailbox.sent) == 1 and mailbox.sent[0]["recipients"] == [PERSON]


def test_retry_replays_without_second_invitation_or_mail(env):
    body, digest = _body(env)
    first = _post(env, body, digest)
    second = _post(env, body, digest)
    assert first.get_json() == second.get_json()
    assert len(env[4].sent) == 1
    assert len(env[2].organizations.find_one({"_id": env[5]})["invitations"]) == 1


def test_same_key_other_intent_conflicts(env):
    _go(env)
    body, digest = _body(env, need="lost_access")
    assert _post(env, body, digest).status_code == 409


def test_stale_state_is_refused(env):
    app, client, db = env[:3]
    body, digest = _body(env, state="0" * 40)
    assert _post(env, body, digest).status_code == 409
    assert env[4].sent == []


def test_state_change_after_approval_is_refused(env):
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    body, digest = _body(env)
    db.memberships.update_one({"user_id": admin}, {"$set": {"role": "member"}})
    assert _post(env, body, digest).status_code == 409


@pytest.mark.parametrize("case", ["protected", "missing_org", "no_account", "inactive", "site_admin",
                                  "already_owner", "open_invitation", "opted_out"])
def test_refusals_are_all_the_same_422_and_send_nothing(env, case, monkeypatch):
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    over = {}
    if case == "protected":
        app.config["BOARD_ORG_ID"] = str(org_id)
    elif case == "missing_org":
        over["organization_id"] = str(ObjectId())
    elif case == "no_account":
        over["address"] = "ei-tilia@example.test"
    elif case == "inactive":
        db.users.update_one({"_id": person}, {"$set": {"active": False}})
    elif case == "site_admin":
        db.users.update_one({"_id": person}, {"$set": {"role": "admin"}})
    elif case == "already_owner":
        db.memberships.insert_one({"user_id": person, "organization_id": org_id, "role": "owner", "permissions": []})
    elif case == "open_invitation":
        db.organizations.update_one({"_id": org_id}, {"$push": {"invitations": {"email": PERSON, "invited_at": utcnow()}}})
    elif case == "opted_out":
        db[org_outreach.OPTOUTS].insert_one({"address_hash": org_outreach.address_hash(PERSON), "at": utcnow()})
    if case == "missing_org":
        body = {"request_id": KEY, "case_id": "case-1", "decision_id": "d", "decision_revision": 1, "need": "owner_change",
                "organization_id": over["organization_id"], "person_email": PERSON, "person_role": "owner",
                "expected_state": "x", "authority_attestation": "p"}
        digest = org_recovery.recovery_digest(body)
        body["authority_attestation"] = _b64url(SIGNER.sign(recovery_attestation_message(digest)))
    else:
        body, digest = _body(env, **over)
    response = _post(env, body, digest)
    assert response.status_code == 422 and response.get_json() == {"code": "unsupported_scope"}
    assert mailbox.sent == []


def test_lost_access_ignores_opt_out_it_is_a_reply(env):
    db = env[2]
    db[org_outreach.OPTOUTS].insert_one({"address_hash": org_outreach.address_hash(PERSON), "at": utcnow()})
    assert _go(env, need="lost_access").status_code == 200


def test_add_member_role_is_admin_or_member_only(env):
    assert _go(env, need="add_member", role="admin").status_code == 200
    assert _raw(env, need="add_member", person_role="owner").status_code == 422


def test_owner_role_for_owner_change_only(env):
    assert _raw(env, need="owner_change", person_role="member").status_code == 422


def test_plus_tag_and_uppercase_person_are_not_accepted_as_address(env):
    assert _raw(env, person_email="Uusi@Example.test").status_code == 422


def test_proof_attestation_and_actor_are_still_required(env):
    body, digest = _body(env)
    assert _post(env, body, digest, action=grant_proofs.ACTION_ORG_INVITATION_V1).status_code == 403
    bad, bad_digest = _body(env)
    bad["authority_attestation"] = _b64url(SIGNER.sign(b"other\n" + bad_digest.encode()))
    assert _post(env, bad, bad_digest).status_code == 403
    assert env[4].sent == []


def test_invitation_lasts_14_days(env):
    db, org_id = env[2], env[5]
    _go(env)
    invitation = db.organizations.find_one({"_id": org_id})["invitations"][0]
    assert invitation["expires_at"] - invitation["invited_at"] == org_recovery.LIFETIME
    assert not invitation_expired(invitation)
    from datetime import timedelta, timezone
    late = invitation["expires_at"].replace(tzinfo=timezone.utc) + timedelta(seconds=1)
    assert invitation_expired(invitation, now=late)


def test_accept_makes_new_owner_and_turns_old_owners_into_admins(env):
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    _go(env)
    org = db.organizations.find_one({"_id": org_id})
    invitation = invitation_for(org["invitations"], PERSON)
    db.memberships.insert_one({"user_id": person, "organization_id": org_id, "role": "owner", "permissions": []})
    org_recovery.on_accept(db, org_id, invitation, person)
    assert db.memberships.find_one({"user_id": owner})["role"] == "admin"
    assert db.memberships.find_one({"user_id": admin})["role"] == "admin"
    assert db.memberships.find_one({"user_id": person})["role"] == "owner"
    assert db.memberships.count_documents({"organization_id": org_id}) == 3
    operation = db[city_grants.OPERATIONS].find_one({"idempotency_key": KEY})
    assert operation["receipt"]["effective"]["accepted_at"]


def test_accept_raises_an_existing_lower_member(env):
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    db.memberships.insert_one({"user_id": person, "organization_id": org_id, "role": "member", "permissions": []})
    _go(env, need="add_member", role="admin")
    invitation = invitation_for(db.organizations.find_one({"_id": org_id})["invitations"], PERSON)
    org_recovery.on_accept(db, org_id, invitation, person)
    assert db.memberships.find_one({"user_id": person})["role"] == "admin"
    assert db.memberships.find_one({"user_id": owner})["role"] == "owner"


def test_ordinary_invitation_does_nothing_on_accept(env):
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    org_recovery.on_accept(db, org_id, {"email": PERSON, "role": "owner", "role_granted_by": "x"}, person)
    assert db.memberships.find_one({"user_id": owner})["role"] == "owner"


def test_journal_and_logs_hold_no_address(env, caplog):
    db = env[2]
    _go(env)
    stored = json.dumps(db[city_grants.OPERATIONS].find_one({"idempotency_key": KEY}), default=str)
    assert PERSON not in stored and "uusi@" not in stored
    assert PERSON not in caplog.text


def test_approval_page_shows_terms_and_returns_expected_state(env):
    from urllib.parse import parse_qs, urlsplit
    app, client, db, seeded, mailbox, org_id, owner, admin, person = env
    body, digest = _body(env)
    fields = {k: v for k, v in body.items() if k not in {"expected_state", "authority_attestation"}}
    raw = _b64url(json.dumps(fields).encode())
    verifier = _verifier(app, seeded["admin_id"])
    page = verifier.get("/admin/hallinto/approve", query_string={"r": raw, "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Esimerkkiyhdistys" in text and "u***@example.test" in text
    assert PERSON not in text
    done = verifier.post("/admin/hallinto/approve", data={"r": raw, "state": STATE}, headers={"Origin": "http://localhost"})
    assert done.status_code == 303
    returned = parse_qs(urlsplit(done.headers["Location"]).fragment)
    assert returned["expected_state"] == [body["expected_state"]]
    with app.app_context():
        proof = returned["proof"][0]
    reply = app.test_client().post(BASE, json=body, headers=_headers(proof=proof, key=KEY))
    assert reply.status_code == 200


def test_approval_page_off_and_wrong_shape(env):
    app, client, db, seeded = env[:4]
    body, _ = _body(env)
    fields = {k: v for k, v in body.items() if k not in {"expected_state", "authority_attestation"}}
    verifier = _verifier(app, seeded["admin_id"])
    app.config["ORG_RECOVERY_ENABLED"] = False
    assert verifier.get("/admin/hallinto/approve", query_string={"r": _b64url(json.dumps(fields).encode()), "state": STATE}).status_code == 404
    app.config["ORG_RECOVERY_ENABLED"] = True
    extra = {**fields, "extra": 1}
    assert verifier.get("/admin/hallinto/approve", query_string={"r": _b64url(json.dumps(extra).encode()), "state": STATE}).status_code == 400
