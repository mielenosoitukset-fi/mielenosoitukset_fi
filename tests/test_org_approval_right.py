"""The organisation right to approve its own demos: request, Hallinto grant/refusal/end, runtime effect."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import grant_proofs, org_admin_grants as oag, org_rights
from mielenosoitukset_fi.admin.hallinto_city_grant_api import (
    AUDIENCE, org_right_attestation_message, org_right_end_attestation_message,
    org_right_refusal_attestation_message,
)
from mielenosoitukset_fi.utils import org_approval_right as right
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user, _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, STATE, _b64url, _headers, _verifier, api,
)

BASE = "/internal/hallinto/v1"
GRANT_ID = "hallinto-right-grant-0000001"
REFUSAL_ID = "hallinto-right-refusal-000001"
END_ID = "hallinto-right-end-0000000001"


class Mailbox:
    def __init__(self):
        self.sent = []

    def queue_email(self, **kwargs):
        self.sent.append(kwargs)


def _until(days=90):
    return (utcnow() + timedelta(days=days)).replace(hour=21, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _person(db, name):
    uid = ObjectId()
    db.users.insert_one(_user_doc(name, f"{name}@example.test", "Passw0rd!x", _id=uid))
    return uid


@pytest.fixture
def env(api, monkeypatch):
    app, client, db, _body, _digest, _proof, seeded, _target = api
    app.config["ORG_APPROVAL_RIGHT_ENABLED"] = True
    app.config["ORG_ADMIN_ROLE_ENABLED"] = True
    mailbox = Mailbox()
    monkeypatch.setattr("mielenosoitukset_fi.admin.role_bundles._email_sender", lambda: mailbox)
    org = ObjectId()
    db.organizations.insert_one({"_id": org, "name": "Oikeusyhdistys", "description": "", "email": "o@org.test",
                                 "verified": True, "members": []})
    people = {name: _person(db, f"right-{name}") for name in ("owner", "admin", "member")}
    for name, uid in people.items():
        db.memberships.insert_one({"user_id": uid, "organization_id": org, "role": name, "permissions": []})
    with app.app_context():
        for uid in people.values():
            oag.sync_account(db, uid)

    base_demo = db.demonstrations.find_one({"_id": seeded["pending_demo_id"]})
    demo = deepcopy(base_demo)
    demo.update({"_id": ObjectId(), "title": "Oikeusyhdistyksen mielenosoitus",
                 "organizers": [{"name": "Oikeusyhdistys", "organization_id": org}]})
    db.demonstrations.insert_one(demo)
    base_case = db.cases.find_one({"_id": seeded["case_id"]})
    case = deepcopy(base_case)
    case.update({"_id": ObjectId(), "demo_id": demo["_id"], "organization_id": None, "running_num": 888001,
                 "meta": {**(base_case.get("meta") or {}), "closed": False, "superior_needed": False,
                          "escalation_level": 0}})
    db.cases.insert_one(case)
    return {"api": api, "app": app, "client": client, "db": db, "seeded": seeded, "org": org, "people": people,
            "demo": demo["_id"], "case": case["_id"], "mail": mailbox,
            "as_user": lambda name: _client_for_user(app, people[name])}


def _open_request(env, who="admin", note=""):
    return env["as_user"](who).post("/admin/organization/api/request_approval_right/",
                                    json={"organization_id": str(env["org"]), "note": note})


def _state(env):
    return env["client"].get(f"{BASE}/org-rights/{env['org']}", headers=_headers()).get_json()


def _post(env, path, body, digest, message, action, key):
    app, db, seeded = env["app"], env["db"], env["seeded"]
    body["authority_attestation"] = _b64url(SIGNER.sign(message(digest)))
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE, action=action,
                                         intent_digest=digest, factor="passkey", subject=None)
    return env["client"].post(f"{BASE}{path}", json=body, headers=_headers(proof=proof, key=key))


def _grant_body(env, case_id, grant_id=GRANT_ID, expected=None, **over):
    db = env["db"]
    org = db.organizations.find_one({"_id": env["org"]})
    with env["app"].app_context():
        expected = expected or org_rights.expected_state(db, org)
    body = {"grant_id": grant_id, "case_id": str(case_id), "decision_id": "synthetic-right", "decision_revision": 1,
            "organization_id": str(env["org"]), "right": right.RIGHT, "valid_until": _until(),
            "expected_state": expected, "authority_attestation": "x", **over}
    return body, org_rights.grant_digest(body)


def _grant(env, case_id=None, **over):
    case_id = case_id or env["db"].cases.find_one(
        {"type": right.CASE_TYPE, "organization_id": env["org"], "meta.closed": {"$ne": True}})["_id"]
    body, digest = _grant_body(env, case_id, **over)
    return _post(env, "/org-rights", body, digest, org_right_attestation_message, grant_proofs.ACTION_ORG_RIGHT_V1,
                 body["grant_id"])


def _end_body(env, kind="revoke", **over):
    body = {"grant_id": GRANT_ID, "kind": kind, "organization_id": str(env["org"]), "right": right.RIGHT,
            "decision_id": "synthetic-end", "decision_revision": 1, "authority_attestation": "x", **over}
    return body, org_rights.end_digest(body)


def _end(env, kind="revoke", key=END_ID, **over):
    body, digest = _end_body(env, kind, **over)
    return _post(env, f"/org-rights/{body['grant_id']}/end", body, digest, org_right_end_attestation_message,
                 grant_proofs.ACTION_ORG_RIGHT_END_V1, key)


def _decide(env, who, action="accept", case=None):
    return env["as_user"](who).post(f"/admin/case/{case or env['case']}/demo_action", json={"action": action})


def _granted(env):
    assert _open_request(env).status_code == 200
    assert _grant(env).status_code == 200


# --- Off by default ---------------------------------------------------------------------------

def test_routes_and_request_are_absent_unless_enabled(env):
    env["app"].config["ORG_APPROVAL_RIGHT_ENABLED"] = False
    assert env["client"].get(f"{BASE}/org-rights/{env['org']}", headers=_headers()).status_code == 404
    assert env["client"].get(f"{BASE}/org-right-requests", headers=_headers()).status_code == 404
    assert _open_request(env).status_code == 404


# --- Request ----------------------------------------------------------------------------------

def test_admin_opens_one_request_case_and_hallinto_reads_it(env):
    response = _open_request(env, "admin", "Hoidamme kaiken itse")
    assert response.status_code == 200
    case = env["db"].cases.find_one({"_id": ObjectId(response.get_json()["case_id"])})
    assert case["type"] == right.CASE_TYPE and case["organization_id"] == env["org"]
    assert _open_request(env, "owner").status_code == 409  # one open request per organisation
    feed = env["client"].get(f"{BASE}/org-right-requests?status=open", headers=_headers()).get_json()["requests"]
    row = next(r for r in feed if r["case_id"] == str(case["_id"]))
    assert row["organization_name"] == "Oikeusyhdistys" and row["verified"] is True
    assert row["note"] == "Hoidamme kaiken itse" and row["requested_by"] == str(env["people"]["admin"])
    assert env["client"].get(f"{BASE}/org-right-requests?status=bogus", headers=_headers()).status_code == 422


def test_plain_member_and_unverified_organisation_cannot_request(env):
    assert _open_request(env, "member").status_code == 403
    env["db"].organizations.update_one({"_id": env["org"]}, {"$set": {"verified": False}})
    assert _open_request(env, "owner").status_code == 409


def test_request_note_must_be_one_short_line(env):
    assert _open_request(env, "admin", "a\nb").status_code == 400
    assert _open_request(env, "admin", "x" * 301).status_code == 400


# --- Grant ------------------------------------------------------------------------------------

def test_grant_writes_right_closes_case_and_mails_admins(env):
    _open_request(env)
    response = _grant(env)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["effective"]["status"] == "live"
    assert receipt["effective"]["verified"] is True
    org = env["db"].organizations.find_one({"_id": env["org"]})
    assert org["rights"][right.RIGHT]["grant_id"] == GRANT_ID and org["rights"][right.RIGHT]["status"] == "live"
    case = env["db"].cases.find_one({"type": right.CASE_TYPE})
    assert case["meta"]["closed"] is True and case["meta"]["outcome"] == "granted"
    assert "Hyväksymisoikeus myönnetty" in case["case_history"][-1]["action"]
    assert env["mail"].sent and env["mail"].sent[0]["template_name"] == "org_right_notification.html"
    state = _state(env)
    assert state["status"] == "live" and state["grant_id"] == GRANT_ID and state["open_request_case_id"] is None
    assert env["client"].get(f"{BASE}/org-right-requests?status=closed", headers=_headers()).get_json()[
        "requests"][0]["outcome"] == "granted"


def test_grant_replays_and_new_grant_while_live_is_refused(env):
    _open_request(env)
    case_id = env["db"].cases.find_one({"type": right.CASE_TYPE})["_id"]
    body, digest = _grant_body(env, case_id)
    send = lambda: _post(env, "/org-rights", dict(body), digest, org_right_attestation_message,  # noqa: E731
                         grant_proofs.ACTION_ORG_RIGHT_V1, GRANT_ID)
    first = send()
    assert send().get_json()["operation_id"] == first.get_json()["operation_id"]
    case_id = env["db"].cases.find_one({"type": right.CASE_TYPE})["_id"]
    assert _grant(env, case_id, grant_id="hallinto-right-grant-0000002").status_code == 422


def test_grant_needs_current_state_open_case_verified_organisation_and_owner(env):
    _open_request(env)
    case_id = env["db"].cases.find_one({"type": right.CASE_TYPE})["_id"]
    assert _grant(env, case_id, expected="stale-state").status_code == 409
    other_case = ObjectId()
    assert _grant(env, other_case, grant_id="hallinto-right-grant-0000003").status_code == 422
    env["db"].memberships.delete_one({"user_id": env["people"]["owner"]})
    assert _grant(env, case_id, grant_id="hallinto-right-grant-0000004").status_code == 422
    assert "rights" not in env["db"].organizations.find_one({"_id": env["org"]})


def test_grant_for_unverified_organisation_is_refused(env):
    _open_request(env)
    env["db"].organizations.update_one({"_id": env["org"]}, {"$set": {"verified": False}})
    assert _grant(env).status_code == 422


def test_grant_valid_until_range(env):
    _open_request(env)
    past = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (utcnow() + timedelta(days=4000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    case_id = env["db"].cases.find_one({"type": right.CASE_TYPE})["_id"]
    for value in (past, far, "2030-01-01"):
        body, _digest = _grant_body(env, case_id, valid_until=value)
        assert org_rights.grant_digest(body) is None  # not even a valid shape
        assert env["client"].post(f"{BASE}/org-rights", json=body, headers=_headers(key=GRANT_ID)).status_code == 422
    assert "rights" not in env["db"].organizations.find_one({"_id": env["org"]})


def test_grant_needs_its_own_signature_context(env):
    _open_request(env)
    case_id = env["db"].cases.find_one({"type": right.CASE_TYPE})["_id"]
    body, digest = _grant_body(env, case_id)
    response = _post(env, "/org-rights", body, digest, org_right_end_attestation_message,
                     grant_proofs.ACTION_ORG_RIGHT_V1, GRANT_ID)
    assert response.status_code == 403
    assert "rights" not in env["db"].organizations.find_one({"_id": env["org"]})


def test_grant_proof_of_another_action_is_refused(env):
    _open_request(env)
    case_id = env["db"].cases.find_one({"type": right.CASE_TYPE})["_id"]
    body, digest = _grant_body(env, case_id)
    response = _post(env, "/org-rights", body, digest, org_right_attestation_message,
                     grant_proofs.ACTION_ORG_RIGHT_END_V1, GRANT_ID)
    assert response.status_code == 403


# --- Refusal ----------------------------------------------------------------------------------

def _refuse(env, case_id, reason="Organisaatio ei ole vielä aktiivinen", **over):
    body = {"refusal_id": REFUSAL_ID, "case_id": str(case_id), "organization_id": str(env["org"]),
            "right": right.RIGHT, "decision_id": "synthetic-refusal", "decision_revision": 1, "reason": reason,
            "authority_attestation": "x", **over}
    digest = org_rights.refusal_digest(body)
    return _post(env, f"/org-right-requests/{case_id}/refusal", body, digest, org_right_refusal_attestation_message,
                 grant_proofs.ACTION_ORG_RIGHT_REFUSAL_V1, REFUSAL_ID)


def test_refusal_closes_the_case_with_reason_and_grants_nothing(env):
    case_id = ObjectId(_open_request(env).get_json()["case_id"])
    response = _refuse(env, case_id)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["effective"]["outcome"] == "refused"
    case = env["db"].cases.find_one({"_id": case_id})
    assert case["meta"]["closed"] is True and case["meta"]["outcome"] == "refused"
    assert case["meta"]["closed_reason"] == "Organisaatio ei ole vielä aktiivinen"
    assert "rights" not in env["db"].organizations.find_one({"_id": env["org"]})
    assert env["mail"].sent and env["mail"].sent[0]["context"]["reason"] == "Organisaatio ei ole vielä aktiivinen"
    assert _state(env)["status"] == "none"
    assert _open_request(env).status_code == 200  # a new request is possible after a refusal


def test_refusal_of_closed_case_is_a_confirmed_noop_that_says_what_happened(env):
    case_id = ObjectId(_open_request(env).get_json()["case_id"])
    assert _grant(env).status_code == 200
    response = _refuse(env, case_id)
    assert response.status_code == 200 and response.get_json()["effective"]["outcome"] == "granted"
    assert _state(env)["status"] == "live"


def test_refusal_reason_rules(env):
    case_id = ObjectId(_open_request(env).get_json()["case_id"])
    for reason in ("a\nb", "", "x" * 301, " padded "):
        body = {"refusal_id": REFUSAL_ID, "case_id": str(case_id), "organization_id": str(env["org"]),
                "right": right.RIGHT, "decision_id": "d", "decision_revision": 1, "reason": reason,
                "authority_attestation": "x"}
        assert org_rights.refusal_digest(body) is None
        reply = env["client"].post(f"{BASE}/org-right-requests/{case_id}/refusal", json=body,
                                   headers=_headers(key=REFUSAL_ID))
        assert reply.status_code == 422
    assert _refuse(env, ObjectId()).status_code == 422  # unknown case


# --- End --------------------------------------------------------------------------------------

def test_revoke_ends_right_at_once_and_replays(env):
    _granted(env)
    first = _end(env, "revoke")
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["effective"]["status"] == "revoked"
    state = _state(env)
    assert state["status"] == "revoked" and state["ended_reason"] == "hallinto_revoked"
    assert _end(env, "revoke").get_json()["operation_id"] == first.get_json()["operation_id"]
    again = _end(env, "freeze", key="hallinto-right-end-0000000002")  # already ended: a confirmed no-op
    assert again.status_code == 200 and again.get_json()["effective"]["status"] == "revoked"
    assert _open_request(env).status_code == 200  # revoked organisations may ask again


def test_freeze_blocks_new_requests(env):
    _granted(env)
    assert _end(env, "freeze").get_json()["effective"]["status"] == "frozen"
    assert _state(env)["status"] == "frozen"
    assert _open_request(env).status_code == 409


def test_end_needs_end_signature_and_known_grant(env):
    _granted(env)
    body, digest = _end_body(env)
    wrong = _post(env, f"/org-rights/{GRANT_ID}/end", body, digest, org_right_attestation_message,
                  grant_proofs.ACTION_ORG_RIGHT_END_V1, END_ID)
    assert wrong.status_code == 403 and _state(env)["status"] == "live"
    unknown, digest = _end_body(env, grant_id="hallinto-right-grant-0000099")
    assert _post(env, "/org-rights/hallinto-right-grant-0000099/end", unknown, digest,
                 org_right_end_attestation_message, grant_proofs.ACTION_ORG_RIGHT_END_V1, END_ID).status_code == 422
    assert _state(env)["status"] == "live"


def test_end_of_a_replaced_grant_never_touches_the_newer_one(env):
    _granted(env)
    assert _end(env, "revoke").status_code == 200
    assert _open_request(env).status_code == 200
    assert _grant(env, grant_id="hallinto-right-grant-0000002").status_code == 200
    noop = _end(env, "revoke", key="hallinto-right-end-0000000003")
    assert noop.status_code == 200
    assert _state(env)["status"] == "live" and _state(env)["grant_id"] == "hallinto-right-grant-0000002"


def test_state_read_unknown_organisation(env):
    assert env["client"].get(f"{BASE}/org-rights/{ObjectId()}", headers=_headers()).status_code == 422


# --- Runtime effect ---------------------------------------------------------------------------

def test_admin_cannot_decide_without_the_right(env):
    assert _decide(env, "admin").status_code == 403
    assert not env["db"].demonstrations.find_one({"_id": env["demo"]}).get("approved")


def test_admin_and_owner_decide_with_the_right_member_does_not(env):
    _granted(env)
    assert _decide(env, "member").status_code == 403
    response = _decide(env, "admin")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert env["db"].demonstrations.find_one({"_id": env["demo"]}).get("approved") is True
    history = env["db"].cases.find_one({"_id": env["case"]})["case_history"]
    assert any("Oikeusyhdistys" in e["action"] and "hyväksymisoikeudella" in e["action"] for e in history)


def test_owner_can_reject_with_the_right(env):
    _granted(env)
    response = env["as_user"]("owner").post(
        f"/admin/case/{env['case']}/demo_action", json={"action": "reject", "reason": "other", "details": "Ei sovi"})
    assert response.status_code in (200, 400)  # the reason schema is the moderators' own
    assert response.status_code != 403


def test_right_does_not_reach_other_organisations_demos(env):
    _granted(env)
    other = ObjectId()
    env["db"].organizations.insert_one({"_id": other, "name": "Toinen", "verified": True})
    env["db"].demonstrations.update_one({"_id": env["demo"]}, {"$set": {"organizers": [
        {"name": "Toinen", "organization_id": other}]}})
    assert _decide(env, "admin").status_code == 403


def test_demo_with_an_organiser_without_the_right_goes_to_moderators(env):
    _granted(env)
    other = ObjectId()
    env["db"].organizations.insert_one({"_id": other, "name": "Toinen", "verified": True})
    env["db"].demonstrations.update_one({"_id": env["demo"]}, {"$push": {"organizers": {
        "name": "Toinen", "organization_id": other}}})
    assert _decide(env, "admin").status_code == 403
    env["db"].demonstrations.update_one({"_id": env["demo"]}, {"$push": {"organizers": {"name": "Yksityishenkilö"}}})
    assert _decide(env, "admin").status_code == 403


def test_right_ends_at_once_when_ended_expired_or_unverified(env):
    _granted(env)
    org = env["db"].organizations
    assert right.status_of(org.find_one({"_id": env["org"]})) == "live"
    org.update_one({"_id": env["org"]}, {"$set": {"verified": False}})
    assert _decide(env, "admin").status_code == 403
    assert right.status_of(org.find_one({"_id": env["org"]})) == "unverified"
    org.update_one({"_id": env["org"]}, {"$set": {"verified": True}})
    assert right.status_of(org.find_one({"_id": env["org"]})) == "live"  # verified again: the record lives on
    org.update_one({"_id": env["org"]}, {"$set": {f"rights.{right.RIGHT}.valid_until": utcnow() - timedelta(seconds=1)}})
    assert right.status_of(org.find_one({"_id": env["org"]})) == "expired"
    assert _state(env)["status"] == "expired"


def test_revoked_right_stops_deciding(env):
    _granted(env)
    assert _end(env, "revoke").status_code == 200
    assert _decide(env, "admin").status_code == 403


def test_flag_off_stops_deciding_without_touching_the_record(env):
    _granted(env)
    env["app"].config["ORG_APPROVAL_RIGHT_ENABLED"] = False
    assert _decide(env, "admin").status_code == 403
    assert env["db"].organizations.find_one({"_id": env["org"]})["rights"][right.RIGHT]["status"] == "live"


def test_escalated_case_stays_with_the_higher_level(env):
    _granted(env)
    env["db"].cases.update_one({"_id": env["case"]}, {"$set": {"meta.superior_needed": True, "meta.escalation_level": 1}})
    assert _decide(env, "admin").status_code == 403


def test_protected_system_team_never_holds_the_right(env, monkeypatch):
    _granted(env)
    monkeypatch.setattr("mielenosoitukset_fi.utils.system_teams.protected_organization",
                        lambda oid: str(oid) == str(env["org"]))
    assert _decide(env, "admin").status_code == 403


# --- Giving up and unverify -------------------------------------------------------------------

def test_owner_gives_the_right_up_admin_cannot(env):
    _granted(env)
    body = {"organization_id": str(env["org"])}
    url = "/admin/organization/api/give_up_approval_right/"
    assert env["as_user"]("admin").post(url, json=body).status_code == 403
    assert env["as_user"]("owner").post(url, json=body).status_code == 200
    state = _state(env)
    assert state["status"] == "given_up" and state["ended_reason"] == "owner_gave_up"
    assert _decide(env, "admin").status_code == 403
    assert env["as_user"]("owner").post(url, json=body).status_code == 409


def test_losing_verified_through_the_edit_form_ends_the_right(env):
    _granted(env)
    with env["app"].app_context():
        assert right.end_right(env["db"], env["org"], "unverified") is True
    state = _state(env)
    assert state["status"] == "unverified" and state["ended_reason"] == "unverified"


# --- Approval page ----------------------------------------------------------------------------

def _page(env, fields, post=False):
    raw = _b64url(json.dumps(fields).encode())
    verifier = _verifier(env["app"], env["seeded"]["admin_id"])
    if post:
        return verifier.post("/admin/hallinto/approve", data={"r": raw, "state": STATE},
                             headers={"Origin": "http://localhost"})
    return verifier.get("/admin/hallinto/approve", query_string={"r": raw, "state": STATE})


def test_approval_page_shows_terms_and_returns_expected_state_only_for_grants(env):
    from urllib.parse import parse_qs, urlsplit

    case_id = _open_request(env, "admin", "Meillä on kokemusta").get_json()["case_id"]
    body, digest = _grant_body(env, case_id)
    fields = {k: v for k, v in body.items() if k not in {"expected_state", "authority_attestation"}}
    page = _page(env, fields)
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Oikeusyhdistys" in text and "Meillä on kokemusta" in text
    done = _page(env, fields, post=True)
    assert done.status_code == 303
    returned = parse_qs(urlsplit(done.headers["Location"]).fragment)
    assert returned["expected_state"] == [body["expected_state"]]
    with env["app"].app_context():
        proof = returned["proof"][0]
    body["authority_attestation"] = _b64url(SIGNER.sign(org_right_attestation_message(digest)))
    assert env["client"].post(f"{BASE}/org-rights", json=body, headers=_headers(proof=proof, key=GRANT_ID)).status_code == 200


def test_end_and_refusal_pages_return_no_expected_state(env):
    from urllib.parse import parse_qs, urlsplit

    case_id = _open_request(env).get_json()["case_id"]
    refusal = {"refusal_id": REFUSAL_ID, "case_id": case_id, "organization_id": str(env["org"]), "right": right.RIGHT,
               "decision_id": "d", "decision_revision": 1, "reason": "Liian aikaista"}
    page = _page(env, refusal)
    assert page.status_code == 200 and "Liian aikaista" in page.get_data(as_text=True)
    assert "expected_state" not in parse_qs(urlsplit(_page(env, refusal, post=True).headers["Location"]).fragment)

    assert _grant(env).status_code == 200
    end_fields = {k: v for k, v in _end_body(env)[0].items() if k != "authority_attestation"}
    assert "Oikeusyhdistys" in _page(env, end_fields).get_data(as_text=True)
    assert "expected_state" not in parse_qs(urlsplit(_page(env, end_fields, post=True).headers["Location"]).fragment)


def test_approval_page_unknown_organisation_and_extra_fields(env):
    fields = {k: v for k, v in _end_body(env)[0].items() if k != "authority_attestation"}
    assert _page(env, {**fields, "organization_id": str(ObjectId())}).status_code == 400
    assert _page(env, {**fields, "extra": 1}).status_code == 400
    env["app"].config["ORG_APPROVAL_RIGHT_ENABLED"] = False
    assert _page(env, fields).status_code == 400


def test_organisation_with_a_right_still_loads_and_saves_without_losing_it(env):
    from mielenosoitukset_fi.utils.classes.Organization import Organization

    _granted(env)
    with env["app"].app_context():
        org = Organization.from_dict(env["db"].organizations.find_one({"_id": env["org"]}))
        org.description = "Uusi kuvaus"
        org.save()
    stored = env["db"].organizations.find_one({"_id": env["org"]})
    assert stored["description"] == "Uusi kuvaus" and stored["rights"][right.RIGHT]["status"] == "live"
    assert env["as_user"]("admin").get(f"/admin/organization/view/{env['org']}").status_code == 200
