"""Hallinto role bundles (translator_v1, api_access_v1, admin_v1) written by Flask."""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import city_grants, grant_proofs, role_bundles
from mielenosoitukset_fi.admin.hallinto_city_grant_api import (
    AUDIENCE, attestation_message, bundle_attestation_message, bundle_end_attestation_message,
)
from mielenosoitukset_fi.admin.permission_copy import PERMISSION_DESCRIPTIONS
from mielenosoitukset_fi.users.models import User
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, STATE, VALID_UNTIL, _b64url, _headers, _terms, _verifier, api,
)

KEY = "hallinto-bundle-000000000001"
END_KEY = "hallinto-bundle-end-00000001"
BASE = "/internal/hallinto/v1/role-bundles"


class Mailbox:
    def __init__(self):
        self.sent = []

    def queue_email(self, **kwargs):
        self.sent.append(kwargs)


@pytest.fixture
def bundles(api, monkeypatch):
    app, client, db, body, digest, proof, seeded, target = api
    mailbox = Mailbox()
    monkeypatch.setattr(role_bundles, "_email_sender", lambda: mailbox)
    yield app, client, db, seeded, target, mailbox


def _person(db, name, **updates):
    uid = ObjectId()
    db.users.insert_one(_user_doc(name, f"{name}@example.test", "Passw0rd!x", _id=uid, **updates))
    return uid


def _bundle(app, db, target, template="translator_v1", *, key=KEY, signer=SIGNER,
            message=bundle_attestation_message, **over):
    with app.app_context():
        state = role_bundles.read_bundle_state(db, str(target), template)
    body = {"account_id": str(target), "template": template, "decision_id": "synthetic-bundle",
            "decision_revision": 1, "valid_until": VALID_UNTIL, "expected_state": state["expected_state"],
            "authority_attestation": "placeholder", **over}
    digest = role_bundles.bundle_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(message(digest)))
    return body, digest


def _post(env, body, digest, key=KEY, action=grant_proofs.ACTION_ROLE_BUNDLE_V1, actor=None):
    app, client, db, seeded = env[:4]
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=actor or seeded["admin_id"], audience=AUDIENCE,
                                         action=action, intent_digest=digest, factor="passkey",
                                         subject=body["account_id"])
    return client.post(BASE, json=body, headers=_headers(proof=proof, key=key))


def _give(env, target, template="translator_v1", key=KEY, **over):
    app, client, db = env[:3]
    body, digest = _bundle(app, db, target, template, **over)
    return _post(env, body, digest, key=key)


def _end_body(target, kind="revoke", appointment=KEY, template="translator_v1", signer=SIGNER,
              message=bundle_end_attestation_message, **over):
    body = {"appointment_id": appointment, "kind": kind, "account_id": str(target), "template": template,
            "decision_id": "synthetic-end", "decision_revision": 1, "authority_attestation": "placeholder", **over}
    digest = role_bundles.end_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(message(digest)))
    return body, digest


def _end(env, target, kind="revoke", key=END_KEY, appointment=KEY, template="translator_v1", **over):
    app, client, db, seeded = env[:4]
    body, digest = _end_body(target, kind, appointment, template, **over)
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_ROLE_BUNDLE_END_V1, intent_digest=digest,
                                         factor="passkey", subject=str(target))
    return client.post(f"{BASE}/{appointment}/end", json=body, headers=_headers(proof=proof, key=key))


def _user(app, db, target):
    with app.app_context():
        return User.from_db(db.users.find_one({"_id": target}))


def test_bundle_lists_are_real_permissions_and_never_reach_rights_or_system():
    forbidden = {"CREATE_USER", "EDIT_USER", "DELETE_USER", "FORCE_PASSWORD_CHANGE", "MANAGE_CLEARANCE",
                 "VIEW_CLEARANCE_AUDIT", "MANAGE_CITIES", "MANAGE_BACKGROUND_JOBS", "CREATE_ORGANIZATION",
                 "EDIT_ORGANIZATION", "DELETE_ORGANIZATION", "INVITE_TO_ORGANIZATION", "API_WRITE", "API_ADMIN"}
    for template, bundle in role_bundles.BUNDLES.items():
        assert bundle["permissions"] == sorted(set(bundle["permissions"]))
        assert set(bundle["permissions"]) <= set(PERMISSION_DESCRIPTIONS), template
        assert not set(bundle["permissions"]) & forbidden, template
    assert role_bundles.BUNDLES["admin_v1"]["role"] == "admin" and len(role_bundles.BUNDLES["admin_v1"]["permissions"]) == 22
    assert role_bundles.BUNDLES["translator_v1"]["permissions"] == [
        "REVIEW_DEMO_TRANSLATIONS", "REVIEW_UI_TRANSLATIONS", "TRANSLATE_DEMO", "TRANSLATE_UI"]


@pytest.mark.parametrize("template,role", [("translator_v1", "user"), ("api_access_v1", "user"), ("admin_v1", "admin")])
def test_bundle_is_written_exactly_and_receipt_is_the_exact_set(bundles, template, role):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target, template)
    response = _post(bundles, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    permissions = role_bundles.BUNDLES[template]["permissions"]
    assert receipt["status"] == "confirmed" and receipt["valid_until"] == VALID_UNTIL
    assert receipt["effective"] == {"role": role, "city": "", "permissions": permissions, "valid_until": VALID_UNTIL}
    assert receipt["resulted_in_city_enablement"] is False and receipt["account_id"] == str(target)
    doc = db.users.find_one({"_id": target})
    assert doc["role"] == role and doc["global_permissions"] == permissions and doc["governance_managed"] is True
    assert doc["role_bundle"]["operation_id"] == receipt["operation_id"]
    user = _user(app, db, target)
    assert user.role == role and sorted(user.global_permissions) == permissions and not user.global_admin
    # Replay: same receipt, no second write and no second email.
    again = _post(bundles, body, digest)
    assert again.get_json() == receipt
    assert len(mailbox.sent) == 1 and target and mailbox.sent[0]["recipients"] == ["carol@example.test"]
    assert "myönnetty" in mailbox.sent[0]["subject"]


def test_receipt_can_be_read_back_by_key(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_ROLE_BUNDLE_V1, intent_digest=digest,
                                         factor="passkey", subject=str(target))
    receipt = client.post(BASE, json=body, headers=_headers(proof=proof, key=KEY)).get_json()
    lookup = client.get("/internal/hallinto/v1/operations/by-key", headers=_headers(proof=proof, key=KEY))
    assert lookup.status_code == 200 and lookup.get_json() == receipt


def test_wrong_attestation_context_or_key_is_denied(bundles):
    app, client, db, seeded, target, mailbox = bundles
    wrong = [dict(message=attestation_message), dict(message=bundle_end_attestation_message),
             dict(signer=__import__("cryptography.hazmat.primitives.asymmetric.ed25519",
                                    fromlist=["x"]).Ed25519PrivateKey.generate())]
    for options in wrong:
        body, digest = _bundle(app, db, target, **options)
        assert _post(bundles, body, digest).status_code == 403
    assert db.users.find_one({"_id": target})["global_permissions"] == []
    assert not mailbox.sent


def test_proof_must_be_for_the_bundle_action(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    assert _post(bundles, body, digest, action=grant_proofs.ACTION_CITY_ADMIN_V1).status_code == 403
    assert _post(bundles, body, digest, action=grant_proofs.ACTION_ROLE_BUNDLE_END_V1).status_code == 403
    assert db.users.find_one({"_id": target})["global_permissions"] == []


def test_stale_state_and_changed_terms(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    db.users.update_one({"_id": target}, {"$set": {"bio": "x", "global_permissions": ["API_READ"]}})
    response = _post(bundles, body, digest)
    assert response.status_code == 409 and response.get_json() == {"code": "stale_state"}
    assert db.users.find_one({"_id": target})["global_permissions"] == ["API_READ"]


@pytest.mark.parametrize("make", [
    lambda db, uid: db.users.update_one({"_id": uid}, {"$set": {"global_admin": True}}),
    lambda db, uid: db.users.update_one({"_id": uid}, {"$set": {"role": "superuser"}}),
    lambda db, uid: db.users.update_one({"_id": uid}, {"$set": {"role": "city_admin"}}),
    lambda db, uid: db.users.update_one({"_id": uid}, {"$set": {"global_permissions": ["EDIT_USER"]}}),
    lambda db, uid: db.users.update_one({"_id": uid}, {"$set": {"banned": True}}),
    lambda db, uid: db.users.update_one({"_id": uid}, {"$set": {"confirmed": False}}),
    lambda db, uid: db.admin_scope_grants.insert_one({
        "_id": ObjectId(), "user_id": uid, "scope_type": "city", "scope_keys": ["kuopio"],
        "permissions": ["VIEW_DEMO"], "revoked_at": None}),
])
def test_elevated_or_inactive_accounts_are_refused(bundles, make):
    app, client, db, seeded, target, mailbox = bundles
    make(db, target)
    response = _give(bundles, target, "translator_v1")
    assert response.status_code == 422, response.get_json()
    assert "role_bundle" not in db.users.find_one({"_id": target})
    assert not mailbox.sent


def test_nobody_gets_a_bundle_from_themselves_or_a_missing_account(bundles):
    app, client, db, seeded, target, mailbox = bundles
    with app.app_context():
        state = role_bundles.read_bundle_state(db, str(seeded["admin_id"]), "admin_v1")
    body, digest = _bundle(app, db, seeded["admin_id"], "admin_v1")
    assert _post(bundles, body, digest).status_code == 403
    ghost = ObjectId()
    body = {"account_id": str(ghost), "template": "translator_v1", "decision_id": "d", "decision_revision": 1,
            "valid_until": VALID_UNTIL, "expected_state": "x" * 20, "authority_attestation": "-"}
    digest = role_bundles.bundle_digest(body)
    body["authority_attestation"] = _b64url(SIGNER.sign(bundle_attestation_message(digest)))
    assert _post(bundles, body, digest).status_code == 403
    assert state is not None


def test_old_admin_tool_rights_inside_the_bundle_are_taken_over_not_refused(bundles):
    app, client, db, seeded, target, mailbox = bundles
    db.users.update_one({"_id": target}, {"$set": {"global_permissions": ["TRANSLATE_DEMO"]}})
    response = _give(bundles, target, "translator_v1")
    assert response.status_code == 200
    assert response.get_json()["effective"]["permissions"] == role_bundles.BUNDLES["translator_v1"]["permissions"]


@pytest.mark.parametrize("bad", [{"valid_until": "2000-01-01T00:00:00Z"},
                                 {"valid_until": (city_grants.utcnow() + timedelta(days=4000)).strftime("%Y-%m-%dT%H:%M:%SZ")}])
def test_validity_must_be_in_the_future_and_bounded(bundles, bad):
    app, client, db, seeded, target, mailbox = bundles
    assert _give(bundles, target, **bad).status_code == 422
    assert "role_bundle" not in db.users.find_one({"_id": target})


def test_unknown_template_or_extra_fields_are_refused(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    for changed in ({**body, "template": "superadmin_v1"}, {**body, "permissions": ["EDIT_USER"]},
                    {**body, "city": "kuopio"}):
        response = app.test_client().post(BASE, json=changed, headers=_headers(proof="x", key=KEY))
        assert response.status_code in {403, 422}
    assert db.users.find_one({"_id": target})["global_permissions"] == []


def test_a_new_bundle_replaces_the_old_one_and_logs_sessions_out(bundles):
    app, client, db, seeded, target, mailbox = bundles
    assert _give(bundles, target, "admin_v1").status_code == 200
    epoch = db.users.find_one({"_id": target}).get("auth_epoch") or 0
    response = _give(bundles, target, "api_access_v1", key="hallinto-bundle-000000000002")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["effective"] == {"role": "user", "city": "",
                                                "permissions": ["API_READ", "API_WRITE_DEMOS"],
                                                "valid_until": VALID_UNTIL}
    doc = db.users.find_one({"_id": target})
    assert doc["role"] == "user" and doc["global_permissions"] == ["API_READ", "API_WRITE_DEMOS"]
    assert doc["auth_epoch"] == epoch + 1


def test_bundle_expires_on_read(bundles):
    app, client, db, seeded, target, mailbox = bundles
    assert _give(bundles, target, "admin_v1").status_code == 200
    assert _user(app, db, target).role == "admin"
    db.users.update_one({"_id": target}, {"$set": {"role_bundle.valid_until": city_grants.utcnow() - timedelta(seconds=1)}})
    user = _user(app, db, target)
    assert user.role == "user" and user.global_permissions == [] and not user.has_admin_access()
    # An expired bundle does not make the account elevated for a new decision.
    with app.app_context():
        state = role_bundles.read_bundle_state(db, str(target), "translator_v1")
    assert state["eligible"] is True
    assert _give(bundles, target, "translator_v1", key="hallinto-bundle-000000000003").status_code == 200


def test_revoke_returns_the_account_to_a_plain_user_and_is_idempotent(bundles):
    app, client, db, seeded, target, mailbox = bundles
    _give(bundles, target, "admin_v1")
    response = _end(bundles, target, template="admin_v1")
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["effective"] == {"role": "user", "permissions": [], "global_admin": False, "bundle": None}
    assert receipt["kind"] == "revoke" and receipt["appointment_id"] == KEY
    doc = db.users.find_one({"_id": target})
    assert doc["role"] == "user" and doc["global_permissions"] == [] and doc["role_bundle"]["ended"] is True
    assert doc["role_bundle"]["frozen"] is False and doc["auth_epoch"] >= 1
    assert _end(bundles, target, template="admin_v1").get_json() == receipt
    again = _end(bundles, target, template="admin_v1", key="hallinto-bundle-end-00000002")
    assert again.status_code == 200 and again.get_json()["effective"] == receipt["effective"]
    assert [m["subject"].split(":")[0] for m in mailbox.sent] == ["Roolipaketti myönnetty", "Roolipaketti peruttu"]


def test_freeze_removes_power_and_marks_the_bundle(bundles):
    app, client, db, seeded, target, mailbox = bundles
    _give(bundles, target, "translator_v1")
    assert _end(bundles, target, "freeze").status_code == 200
    doc = db.users.find_one({"_id": target})
    assert doc["global_permissions"] == [] and doc["role_bundle"]["frozen"] is True
    assert doc["role_bundle"]["revoked_reason"] == "hallinto_frozen"
    assert "jäädytetty" in mailbox.sent[-1]["subject"]
    # A new decision may give a bundle again.
    assert _give(bundles, target, "translator_v1", key="hallinto-bundle-000000000004").status_code == 200


def test_ending_an_old_appointment_leaves_a_newer_bundle_alone(bundles):
    app, client, db, seeded, target, mailbox = bundles
    _give(bundles, target, "translator_v1")
    _give(bundles, target, "api_access_v1", key="hallinto-bundle-000000000002")
    response = _end(bundles, target, template="translator_v1")
    assert response.status_code == 200
    assert db.users.find_one({"_id": target})["global_permissions"] == ["API_READ", "API_WRITE_DEMOS"]
    assert response.get_json()["effective"]["bundle"] == "api_access_v1"


def test_end_needs_a_confirmed_appointment_of_the_same_account_and_bundle(bundles):
    app, client, db, seeded, target, mailbox = bundles
    _give(bundles, target, "translator_v1")
    other = _person(db, "dora")
    assert _end(bundles, other).status_code == 422
    assert _end(bundles, target, template="admin_v1", key="hallinto-bundle-end-00000003").status_code == 422
    assert _end(bundles, target, appointment="hallinto-unknown-000000001", key="hallinto-bundle-end-00000004").status_code == 422
    assert db.users.find_one({"_id": target})["global_permissions"] == role_bundles.BUNDLES["translator_v1"]["permissions"]


def test_end_route_checks_path_context_and_proof_action(bundles):
    app, client, db, seeded, target, mailbox = bundles
    _give(bundles, target)
    body, digest = _end_body(target)
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_ROLE_BUNDLE_END_V1, intent_digest=digest,
                                         factor="passkey", subject=str(target))
    other_path = client.post(f"{BASE}/hallinto-other-0000000001/end", json=body, headers=_headers(proof=proof, key=END_KEY))
    assert other_path.status_code == 422
    wrong_context, wrong_digest = _end_body(target, message=bundle_attestation_message)
    with app.app_context():
        wrong_proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                               action=grant_proofs.ACTION_ROLE_BUNDLE_END_V1,
                                               intent_digest=wrong_digest, factor="passkey", subject=str(target))
    assert client.post(f"{BASE}/{KEY}/end", json=wrong_context,
                       headers=_headers(proof=wrong_proof, key=END_KEY)).status_code == 403
    assert db.users.find_one({"_id": target})["global_permissions"] != []


def test_demoted_actor_cannot_give_or_end(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    db.users.update_one({"_id": seeded["admin_id"]}, {"$set": {"global_admin": False, "role": "user"}})
    assert _post(bundles, body, digest).status_code == 403
    assert db.users.find_one({"_id": target})["global_permissions"] == []


def test_a_mail_failure_never_changes_the_result(bundles, monkeypatch):
    app, client, db, seeded, target, mailbox = bundles

    def boom():
        raise RuntimeError("smtp down")

    monkeypatch.setattr(role_bundles, "_email_sender", boom)
    response = _give(bundles, target)
    assert response.status_code == 200 and response.get_json()["status"] == "confirmed"


def test_admins_of_the_national_team_are_told_too(bundles):
    app, client, db, seeded, target, mailbox = bundles
    national = ObjectId()
    db.organizations.insert_one({"_id": national, "name": "Kansallinen"})
    app.config["NATIONAL_ADMIN_ORG_ID"] = str(national)
    member = _person(db, "nina")
    db.memberships.insert_one({"user_id": member, "organization_id": national, "governance_approved": True,
                               "role": "member", "permissions": []})
    _give(bundles, target)
    assert mailbox.sent[0]["recipients"] == ["carol@example.test", "nina@example.test"]
    assert mailbox.sent[0]["context"]["bundle_label"] == "Kääntäjä"


def test_email_template_renders():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader("mielenosoitukset_fi/templates/emails"),
                      autoescape=select_autoescape(["html"]))
    for action in ("applied", "revoke", "freeze"):
        html = env.get_template("role_bundle_notification.html").render(
            action=action, bundle_label="Kääntäjä", account_name="Carol", decision_id="d",
            decision_revision=2, valid_until="2027-01-01T00:00:00Z", permissions=["TRANSLATE_UI"])
        assert "Kääntäjä" in html and "Carol" in html


# --- approval page ---------------------------------------------------------------

def _fields(body):
    return {k: v for k, v in body.items() if k not in {"expected_state", "authority_attestation"}}


def test_approval_page_and_proof_for_a_bundle(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target, "admin_v1")
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(body), "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Roolipaketti: Ylläpitäjä" in text and "Vahvista nimitys" in text
    assert str(PERMISSION_DESCRIPTIONS["VIEW_LOGS"]) in text and "Kaupunki" not in text
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(body), "state": STATE},
                             headers={"Origin": "http://localhost"})
    assert response.status_code == 303
    returned = parse_qs(urlsplit(response.headers["Location"]).fragment)
    assert returned["expected_state"] == [body["expected_state"]]
    assert client.post(BASE, json=body, headers=_headers(proof=returned["proof"][0], key=KEY)).status_code == 200


def test_approval_page_and_proof_for_ending_a_bundle(bundles):
    app, client, db, seeded, target, mailbox = bundles
    _give(bundles, target)
    end, digest = _end_body(target, "freeze")
    fields = _fields(end)
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(fields), "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Nimityksen jäädytys" in text and "palaa tavalliseksi käyttäjäksi" in text
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(fields), "state": STATE},
                             headers={"Origin": "http://localhost"})
    returned = parse_qs(urlsplit(response.headers["Location"]).fragment)
    assert "expected_state" not in returned
    done = client.post(f"{BASE}/{KEY}/end", json=end, headers=_headers(proof=returned["proof"][0], key=END_KEY))
    assert done.status_code == 200


def test_approval_page_refuses_unknown_bundles_and_extra_terms(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    for fields in ({**_fields(body), "permissions": ["EDIT_USER"]}, {**_fields(body), "template": "god_v1"},
                   {k: v for k, v in _fields(body).items() if k != "valid_until"}):
        assert verifier.get("/admin/hallinto/approve",
                            query_string={"r": _terms(fields), "state": STATE}).status_code == 400


def test_approval_page_warns_for_an_elevated_account(bundles):
    app, client, db, seeded, target, mailbox = bundles
    body, digest = _bundle(app, db, target)
    db.users.update_one({"_id": target}, {"$set": {"role": "superuser"}})
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(body), "state": STATE})
    assert "Nimitystä ei voi vahvistaa" in page.get_data(as_text=True)
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(body), "state": STATE},
                             headers={"Origin": "http://localhost"})
    # A proof can still be issued; Flask refuses at apply (422 above). The page does not offer it.
    assert response.status_code == 303
