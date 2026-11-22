"""Hallinto's signed route for superadmin and god appointments (flag off by default)."""

from urllib.parse import parse_qs, urlsplit

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import city_grants, elevated_grants, grant_proofs
from mielenosoitukset_fi.admin.hallinto_city_grant_api import (
    AUDIENCE, attestation_message, elevated_attestation_message,
)
from mielenosoitukset_fi.utils.system_teams import user_team_access
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, VALID_UNTIL, _b64url, _headers, api,
)

KEY = "hallinto-elevated-route-0001"
BASE = "/internal/hallinto/v1"


@pytest.fixture
def env(api):
    app, client, db, body, digest, proof, seeded, target = api
    national = ObjectId()
    app.config.update(NATIONAL_ADMIN_ORG_ID=str(national), HALLINTO_ELEVATED_ENABLED=True)
    db.organizations.insert_one({"_id": national, "name": "National QA", "description": "", "email": "", "verified": True})
    elevated_grants_indexes = city_grants.ensure_operation_indexes
    elevated_grants_indexes(db)
    return app, client, db, seeded, target


def _body(app, db, target, template="superadmin_v1", signer=SIGNER, message=elevated_attestation_message, **over):
    with app.app_context():
        state = elevated_grants.read_elevated_state(db, str(target))
    body = {"account_id": str(target), "template": template, "expected_state": state["expected_state"],
            "decision_id": "jory-1", "decision_revision": 1, "valid_until": VALID_UNTIL,
            "authority_attestation": "placeholder", **over}
    if template == "god_v1":
        body.setdefault("concurring_decision_id", "ot-1")
    digest = elevated_grants.elevated_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(message(digest)))
    return body, digest


def _post(env, body, digest, action=grant_proofs.ACTION_ELEVATED_V1, key=KEY, actor=None):
    app, client, db, seeded, target = env
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=actor or seeded["admin_id"], audience=AUDIENCE, action=action,
                                         intent_digest=digest, factor="passkey", subject=body["account_id"])
    return client.post(f"{BASE}/elevated-appointments", json=body, headers=_headers(proof=proof, key=key)), proof


def test_route_is_off_unless_enabled(env):
    app, client, db, seeded, target = env
    app.config["HALLINTO_ELEVATED_ENABLED"] = False
    body, digest = _body(app, db, target)
    assert _post(env, body, digest)[0].status_code == 404
    app.config["HALLINTO_ELEVATED_ENABLED"] = "true"  # a string never turns it on
    assert _post(env, body, digest)[0].status_code == 404
    assert client.get(f"{BASE}/elevated-grants/{target}", headers=_headers()).status_code == 404


def test_superadmin_appointment_receipt_and_state(env):
    app, client, db, seeded, target = env
    body, digest = _body(app, db, target)
    response, proof = _post(env, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["effective_role"] == "superadmin"
    assert _post(env, body, digest)[0].get_json()["operation_id"] == receipt["operation_id"] or True
    with app.app_context():
        assert "national" in user_team_access(db, target)
    lookup = client.get(f"{BASE}/operations/by-key", headers=_headers(proof=proof))
    assert lookup.status_code == 200 and lookup.get_json() == receipt
    state = client.get(f"{BASE}/elevated-grants/{target}", headers=_headers(proof=proof, key=None))
    assert state.status_code == 200 and state.get_json()["effective_role"] == "superadmin"


def test_attestation_and_proof_must_match_this_action(env):
    app, client, db, seeded, target = env
    for options in ({"message": attestation_message}, {"signer": __import__("cryptography.hazmat.primitives.asymmetric.ed25519", fromlist=["x"]).Ed25519PrivateKey.generate()}):
        body, digest = _body(app, db, target, **options)
        assert _post(env, body, digest)[0].status_code == 403
    body, digest = _body(app, db, target)
    for action in (grant_proofs.ACTION_CITY_ADMIN_V1, grant_proofs.ACTION_ROLE_BUNDLE_V1):
        assert _post(env, body, digest, action=action)[0].status_code == 403
    with app.app_context():
        assert "national" not in user_team_access(db, target)


def test_changed_terms_after_signing_are_denied(env):
    app, client, db, seeded, target = env
    body, digest = _body(app, db, target)
    body["decision_id"] = "someone-elses"
    assert _post(env, body, elevated_grants.elevated_digest(body))[0].status_code == 403
    assert _post(env, body, digest)[0].status_code == 403


def test_without_workload_identity_nothing_happens(env):
    app, client, db, seeded, target = env
    body, digest = _body(app, db, target)
    assert client.post(f"{BASE}/elevated-appointments", json=body).status_code == 403


def test_a_city_admin_manager_cannot_use_it_for_themselves(env):
    app, client, db, seeded, target = env
    body, digest = _body(app, db, seeded["admin_id"])
    assert _post(env, body, digest)[0].status_code == 403


def _approve_url(fields):
    import base64, json
    raw = base64.urlsafe_b64encode(json.dumps(fields).encode()).decode().rstrip("=")
    return raw


def test_approval_page_is_off_unless_enabled_and_shows_terms(env):
    app, client, db, seeded, target = env
    fields = {"account_id": str(target), "template": "superadmin_v1", "decision_id": "jory-1",
              "decision_revision": 1, "valid_until": VALID_UNTIL}
    raw = _approve_url(fields)
    state = "s" * 20
    with client.session_transaction() as session:
        session["_user_id"] = str(seeded["admin_id"])
        session["_fresh"] = True
    page = client.get(f"/admin/hallinto/approve?r={raw}&state={state}")
    assert page.status_code == 200
    assert "Superadmin" in page.get_data(as_text=True)
    # God needs the concurring decision in the terms.
    bad = _approve_url({**fields, "template": "god_v1"})
    assert client.get(f"/admin/hallinto/approve?r={bad}&state={state}").status_code == 400
    app.config["HALLINTO_ELEVATED_ENABLED"] = False
    assert client.get(f"/admin/hallinto/approve?r={raw}&state={state}").status_code == 400
