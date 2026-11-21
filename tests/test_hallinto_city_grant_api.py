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
from mielenosoitukset_fi.admin.hallinto_city_grant_api import (
    AUDIENCE, attestation_message, end_attestation_message,
)
from mielenosoitukset_fi.utils.city_settings import upsert_city_setting
from tests.conftest import _seed_database, _user_doc

KEY = "hallinto-api-000000000001"
RETURN_URL = "https://hallinto.example.test/approved"
STATE = "browser-state-000000000001"
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
PROXY_SECRET = "internal-vhost-secret-0000000000000001"


def _headers(cert=WORKLOAD, proof=None, key=KEY):
    headers = {"X-Hallinto-Proxy-Secret": PROXY_SECRET, "X-SSL-Client-Verify": "SUCCESS",
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
        "return_url": RETURN_URL,
        "proxy_secret_sha256": hashlib.sha256(PROXY_SECRET.encode()).hexdigest(),
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
                                         intent_digest=digest, factor="passkey", subject=str(target))
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
    {"X-Hallinto-Proxy-Secret": PROXY_SECRET, "X-SSL-Client-Verify": "NONE"},
    {"X-Hallinto-Proxy-Secret": PROXY_SECRET, "X-SSL-Client-Verify": "SUCCESS",
     "X-SSL-Client-Cert": "not-a-certificate"},
    "other-cert",
    "no-proxy-secret",
])
def test_workload_identity_is_required(api, headers):
    app, client, db, body, digest, proof, seeded, target = api
    if headers == "other-cert":
        headers = _headers(cert=OTHER, proof=proof)
    elif headers == "no-proxy-secret":
        # A public vhost that forwards the (public) certificate is not enough.
        headers = {k: v for k, v in _headers(proof=proof).items() if k != "X-Hallinto-Proxy-Secret"}
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


def test_lookups_are_limited_to_what_the_proof_was_for(api):
    app, client, db, body, digest, proof, seeded, target = api
    # Not yet used: no receipt lookup.
    assert client.get("/internal/hallinto/v1/operations/by-key", headers=_headers(proof=proof)).status_code == 403
    assert client.post("/internal/hallinto/v1/city-appointments", json=body,
                       headers=_headers(proof=proof)).status_code == 200
    other_key = _headers(proof=proof, key="hallinto-api-000000000003")
    assert client.get("/internal/hallinto/v1/operations/by-key", headers=other_key).status_code == 403
    other = client.get(f"/internal/hallinto/v1/city-grants/{seeded['admin_id']}", headers=_headers(proof=proof, key=None))
    assert other.status_code == 403


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


def _verifier(app, user_id, *, strong=True, age=0):
    import time
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        if strong is not None:
            session["sudo"] = {"authenticated_at": time.time() - age, "user_id": str(user_id),
                               "method": "webauthn" if strong else "password_totp", "strong": strong}
    return client



def _terms(body):
    import json
    fields = {k: v for k, v in body.items() if k not in {"expected_state", "authority_attestation"}}
    return _b64url(json.dumps(fields).encode())


def _confirm(client, body, origin="http://localhost"):
    return client.post("/admin/hallinto/approve", data={"r": _terms(body), "state": STATE},
                       headers={"Origin": origin})


@pytest.mark.parametrize("strong,age,expect_proof", [
    (None, 0, False),        # never stepped up
    (False, 0, False),       # password without TOTP
    (True, 16 * 60, False),  # older than the 15-minute window
    (True, 14 * 60, True),
])
def test_approval_needs_a_strong_factor_within_15_minutes(api, strong, age, expect_proof):
    from urllib.parse import parse_qs, urlsplit
    app, client, db, body, digest, proof, seeded, target = api
    verifier = _verifier(app, seeded["admin_id"], strong=strong, age=age)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(body), "state": STATE})
    assert page.status_code == 200 and "Kuopio" in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    response = _confirm(verifier, body)
    assert response.status_code == 303
    location = response.headers["Location"]
    if not expect_proof:
        assert location.startswith("/admin/hallinto/approve") or "/admin/hallinto/approve" in location
        return
    url = urlsplit(location)
    assert f"{url.scheme}://{url.netloc}{url.path}" == RETURN_URL and not url.query
    returned = parse_qs(url.fragment)
    assert returned["state"] == [STATE] and returned["expected_state"] == [body["expected_state"]]
    # The new proof works for the exact intent through the internal route.
    assert client.post("/internal/hallinto/v1/city-appointments", json=body,
                       headers=_headers(proof=returned["proof"][0])).status_code == 200


def test_approval_rejects_cross_site_posts_and_non_managers(api):
    app, client, db, body, digest, proof, seeded, target = api
    assert _confirm(_verifier(app, seeded["admin_id"]), body, origin="https://evil.example").status_code == 403
    assert _confirm(_verifier(app, seeded["friend_id"]), body).status_code == 403
    assert db[grant_proofs.PROOFS].count_documents({}) == 1  # only the fixture's proof


def test_approval_without_any_strong_factor_points_to_settings(api):
    app, client, db, body, digest, proof, seeded, target = api
    page = _verifier(app, seeded["admin_id"], strong=None).get(
        "/admin/hallinto/approve", query_string={"r": _terms(body), "state": STATE})
    text = page.get_data(as_text=True)
    assert "Lisää passkey asetuksissa" in text and 'href="https://hallinto.example.test/"' in text


# --- ending an appointment (revoke or freeze) ---------------------------------

END_KEY = "hallinto-end-0000000000001"


def _end_body(target, kind="revoke", appointment=KEY, signer=SIGNER, message=end_attestation_message, **over):
    body = {"appointment_id": appointment, "kind": kind, "account_id": str(target), "city": "kuopio",
            "template": "city_admin_v1", "decision_id": "synthetic-end", "decision_revision": 1,
            "authority_attestation": "placeholder", **over}
    digest = city_grants.end_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(message(digest)))
    return body, digest


def _end_proof(app, db, seeded, digest, target, action=grant_proofs.ACTION_CITY_ADMIN_END_V1):
    with app.app_context():
        return grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE, action=action,
                                        intent_digest=digest, factor="passkey", subject=str(target))


def _appointed(api):
    app, client, db, body, digest, proof, seeded, target = api
    assert client.post("/internal/hallinto/v1/city-appointments", json=body,
                       headers=_headers(proof=proof)).status_code == 200
    return app, client, db, seeded, target


@pytest.mark.parametrize("kind,reason", [("revoke", "hallinto_revoked"), ("freeze", "hallinto_frozen")])
def test_end_removes_access_at_once_and_is_idempotent(api, kind, reason):
    app, client, db, seeded, target = _appointed(api)
    end, end_digest = _end_body(target, kind)
    proof = _end_proof(app, db, seeded, end_digest, target)
    response = client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                           headers=_headers(proof=proof, key=END_KEY))
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["kind"] == kind and receipt["appointment_id"] == KEY
    assert receipt["effective"] == {"cities": [], "permissions": [], "role": "user"}
    grant = db.admin_scope_grants.find_one({"user_id": target})
    assert grant["revoked_reason"] == reason and bool(grant.get("frozen")) == (kind == "freeze")
    assert db.users.find_one({"_id": target})["role"] == "user"
    # The same command replays the receipt; the owner state shows no scope.
    again = client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                        headers=_headers(proof=proof, key=END_KEY))
    assert again.get_json() == receipt
    lookup = client.get("/internal/hallinto/v1/operations/by-key", headers=_headers(proof=proof, key=END_KEY))
    assert lookup.get_json() == receipt
    state = client.get(f"/internal/hallinto/v1/city-grants/{target}", headers=_headers(proof=proof, key=None))
    assert state.get_json()["scopes"] == []
    events = list(db.audit_events.find({"via.operation_id": receipt["operation_id"]}))
    assert all(e["actor"].get("user_id") == str(seeded["admin_id"]) for e in events)


def test_a_new_end_for_an_ended_grant_is_a_confirmed_no_op(api):
    app, client, db, seeded, target = _appointed(api)
    for key, kind in (("hallinto-end-0000000000001", "revoke"), ("hallinto-end-0000000000002", "freeze")):
        end, end_digest = _end_body(target, kind)
        proof = _end_proof(app, db, seeded, end_digest, target)
        response = client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                               headers=_headers(proof=proof, key=key))
        assert response.status_code == 200 and response.get_json()["effective"]["cities"] == []


def test_end_needs_its_own_attestation_and_proof_action(api):
    app, client, db, seeded, target = _appointed(api)
    post = lambda end, proof: client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                                          headers=_headers(proof=proof, key=END_KEY))
    # An appointment-context signature cannot end a grant.
    end, end_digest = _end_body(target, message=attestation_message)
    assert post(end, _end_proof(app, db, seeded, end_digest, target)).status_code == 403
    # A forged signature, and a proof issued for appointing, are refused too.
    forged, forged_digest = _end_body(target, signer=Ed25519PrivateKey.generate())
    assert post(forged, _end_proof(app, db, seeded, forged_digest, target)).status_code == 403
    end, end_digest = _end_body(target)
    assert post(end, _end_proof(app, db, seeded, end_digest, target,
                                action=grant_proofs.ACTION_CITY_ADMIN_V1)).status_code == 403
    assert db.admin_scope_grants.count_documents({"user_id": target, "revoked_at": None}) == 1


def test_end_must_refer_to_a_confirmed_appointment_of_that_account(api):
    app, client, db, seeded, target = _appointed(api)
    for number, over in enumerate(({"appointment_id": "hallinto-unknown-000000001"}, {"city": "helsinki"})):
        end, end_digest = _end_body(target, **over)
        proof = _end_proof(app, db, seeded, end_digest, target)
        response = client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                               headers=_headers(proof=proof, key=f"hallinto-end-refer-{number:08d}"))
        assert response.status_code == 422
    assert db.admin_scope_grants.count_documents({"user_id": target, "revoked_at": None}) == 1


def test_end_is_denied_for_a_demoted_actor_and_a_different_intent(api):
    app, client, db, seeded, target = _appointed(api)
    end, end_digest = _end_body(target)
    proof = _end_proof(app, db, seeded, end_digest, target)
    changed = {**end, "kind": "freeze"}
    assert client.post("/internal/hallinto/v1/city-appointment-ends", json=changed,
                       headers=_headers(proof=proof, key=END_KEY)).status_code == 403
    db.users.update_one({"_id": seeded["admin_id"]}, {"$set": {"global_admin": False, "role": "user"}})
    assert client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                       headers=_headers(proof=proof, key=END_KEY)).status_code == 403
    assert db.admin_scope_grants.count_documents({"user_id": target, "revoked_at": None}) == 1


def test_approval_page_and_proof_for_ending(api):
    from urllib.parse import parse_qs, urlsplit
    app, client, db, seeded, target = _appointed(api)
    end, end_digest = _end_body(target, "freeze")
    fields = {k: v for k, v in end.items() if k != "authority_attestation"}
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(fields), "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Nimityksen jäädytys" in text and "Vahvista päättäminen" in text
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(fields), "state": STATE},
                             headers={"Origin": "http://localhost"})
    assert response.status_code == 303
    returned = parse_qs(urlsplit(response.headers["Location"]).fragment)
    assert "expected_state" not in returned and returned["state"] == [STATE]
    assert client.post("/internal/hallinto/v1/city-appointment-ends", json=end,
                       headers=_headers(proof=returned["proof"][0], key=END_KEY)).status_code == 200
