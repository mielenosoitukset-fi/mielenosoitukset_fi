"""Internal Hallinto city-appointment route: workload, fresh proof, attestation."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from bson import ObjectId
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

from mielenosoitukset_fi.admin import city_grants, grant_proofs
from mielenosoitukset_fi.admin.hallinto_city_grant_api import AUDIENCE, attestation_message
from mielenosoitukset_fi.utils.city_settings import upsert_city_setting
from tests.conftest import _seed_database, _user_doc

KEY = "hallinto-api-000000000001"
VALID_UNTIL = (city_grants.utcnow() + timedelta(days=60)).strftime("%Y-%m-%dT22:00:00Z")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _certificate():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hallinto-workload")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    return cert


WORKLOAD = _certificate()
OTHER = _certificate()
SIGNER = Ed25519PrivateKey.generate()


def _headers(cert=WORKLOAD, proof=None, key=KEY):
    headers = {"X-SSL-Client-Verify": "SUCCESS",
               "X-SSL-Client-Cert": quote(cert.public_bytes(Encoding.PEM).decode())}
    if proof:
        headers["Authorization"] = f"Bearer {proof}"
    if key:
        headers["Idempotency-Key"] = key
    return headers


@pytest.fixture
def api(app_factory, db):
    app = app_factory(HALLINTO_CITY_GRANTS={
        "workload_cert_sha256": [hashlib.sha256(WORKLOAD.public_bytes(Encoding.DER)).hexdigest()],
        "attestation_public_key": _b64url(SIGNER.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
    })
    seeded = _seed_database(app, db)
    target = ObjectId()
    db.users.insert_one(_user_doc("carol", "carol@example.test", "CarolPass1!", _id=target))
    upsert_city_setting(db, "kuopio", True)
    with app.app_context():
        state = city_grants.read_city_grant_state(db, str(target))
    body = {"account_id": str(target), "city": "kuopio", "template": "city_admin_v1",
            "expected_state": state["expected_state"], "allow_city_enablement": False,
            "decision_id": "synthetic-decision", "decision_revision": 2, "valid_until": VALID_UNTIL,
            "authority_attestation": "placeholder"}
    digest = city_grants.intent_digest(body)
    body["authority_attestation"] = _b64url(SIGNER.sign(attestation_message(digest)))
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_CITY_ADMIN_V1,
                                         intent_digest=digest, factor="passkey")
    try:
        yield app, app.test_client(), db, body, digest, proof, seeded, target
    finally:
        from tests.conftest import _cleanup_app_resources
        _cleanup_app_resources(app)


def test_route_is_absent_unless_configured(app):
    assert app.test_client().post("/internal/hallinto/v1/city-appointments", json={}).status_code == 404


def test_appointment_through_the_route(api):
    app, client, db, body, digest, proof, seeded, target = api
    response = client.post("/internal/hallinto/v1/city-appointments", json=body, headers=_headers(proof=proof))
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["valid_until"] == VALID_UNTIL
    events = list(db.audit_events.find({"via.operation_id": receipt["operation_id"]}))
    assert all(event["actor"].get("user_id") == str(seeded["admin_id"]) for event in events)
    # Retrying the same command with the same proof replays the receipt.
    again = client.post("/internal/hallinto/v1/city-appointments", json=body, headers=_headers(proof=proof))
    assert again.get_json() == receipt
    lookup = client.get("/internal/hallinto/v1/operations/by-key", headers=_headers(proof=proof))
    assert lookup.status_code == 200 and lookup.get_json() == receipt
    state = client.get(f"/internal/hallinto/v1/city-grants/{target}", headers=_headers(proof=proof, key=None))
    assert state.get_json()["scopes"][0]["valid_until"] == VALID_UNTIL


@pytest.mark.parametrize("headers", [
    {},
    {"X-SSL-Client-Verify": "NONE"},
    {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-Cert": "not-a-certificate"},
    "other-cert",
])
def test_workload_identity_is_required(api, headers):
    app, client, db, body, digest, proof, seeded, target = api
    if headers == "other-cert":
        headers = _headers(cert=OTHER, proof=proof)
    else:
        headers = {**headers, "Authorization": f"Bearer {proof}", "Idempotency-Key": KEY}
    response = client.post("/internal/hallinto/v1/city-appointments", json=body, headers=headers)
    assert (response.status_code, response.get_json()) == (403, {"code": "denied"})
    assert db[grant_proofs.PROOFS].find_one({})["used_at"] is None


def test_proof_is_bound_to_the_intent_and_the_command(api):
    app, client, db, body, digest, proof, seeded, target = api
    changed = {**body, "decision_revision": 3}
    response = client.post("/internal/hallinto/v1/city-appointments", json=changed, headers=_headers(proof=proof))
    assert response.status_code == 403
    assert client.post("/internal/hallinto/v1/city-appointments", json=body,
                       headers=_headers(proof=proof)).status_code == 200
    # A spent proof cannot start another command.
    other_key = client.post("/internal/hallinto/v1/city-appointments", json=body,
                            headers=_headers(proof=proof, key="hallinto-api-000000000002"))
    assert other_key.status_code == 403


def test_attestation_must_sign_the_same_intent(api):
    app, client, db, body, digest, proof, seeded, target = api
    forged = Ed25519PrivateKey.generate().sign(attestation_message(digest))
    response = client.post("/internal/hallinto/v1/city-appointments",
                           json={**body, "authority_attestation": _b64url(forged)}, headers=_headers(proof=proof))
    assert response.status_code == 403
    assert db.admin_scope_grants.count_documents({"user_id": target}) == 0


def test_expired_proof_and_weak_factor(api):
    app, client, db, body, digest, proof, seeded, target = api
    db[grant_proofs.PROOFS].update_many({}, {"$set": {"expires_at": city_grants.utcnow() - timedelta(seconds=1)}})
    assert client.post("/internal/hallinto/v1/city-appointments", json=body,
                       headers=_headers(proof=proof)).status_code == 403
    with pytest.raises(ValueError):
        grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                 action=grant_proofs.ACTION_CITY_ADMIN_V1, intent_digest=digest, factor="password")


def test_proof_of_a_demoted_admin_is_denied(api):
    app, client, db, body, digest, proof, seeded, target = api
    db.users.update_one({"_id": seeded["admin_id"]}, {"$set": {"global_admin": False, "role": "user"}})
    response = client.post("/internal/hallinto/v1/city-appointments", json=body, headers=_headers(proof=proof))
    assert (response.status_code, response.get_json()) == (403, {"code": "denied"})
    assert db.admin_scope_grants.count_documents({"user_id": target}) == 0
