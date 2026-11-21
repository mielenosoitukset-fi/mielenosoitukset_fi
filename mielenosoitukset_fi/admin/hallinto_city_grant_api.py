"""Internal Hallinto city-appointment route (C02 step 3), off unless configured.

Registered only when ``HALLINTO_CITY_GRANT_CONFIG_FILE`` names a restricted
JSON file (or a test sets ``HALLINTO_CITY_GRANTS``). Every request needs:

* workload identity: Apache verifies the Hallinto client certificate on the
  internal vhost and forwards it in ``X-SSL-Client-Verify`` and
  ``X-SSL-Client-Cert`` (URL-escaped PEM), overwriting any client value.
  The certificate's SHA-256 must be configured.
* actor proof: ``Authorization: Bearer <proof>`` issued by Flask after a fresh
  passkey or password+TOTP approval of the exact intent (grant_proofs).
* for writes, Hallinto's Ed25519 authority attestation over the same intent.

See docs/hallinto-city-grant.md and docs/hallinto-city-grant-v1.yaml.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import unquote

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding
from flask import Blueprint, current_app, g, jsonify, request

from . import city_grants, grant_proofs

INTEGRATION = "hallinto"
AUDIENCE = "hallinto-city-grants"
ATTESTATION_CONTEXT = b"hallinto-city-admin-v1-attestation\n"


def attestation_message(intent_digest: str) -> bytes:
    """Bytes Hallinto signs: a fixed context line and the canonical intent digest."""
    return ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _settings() -> dict:
    return current_app.extensions["hallinto_city_grants"]


def _error(code: str):
    status = city_grants.ERROR_STATUS[code]
    response = jsonify({"code": code})
    response.status_code = status
    return response


def _reply(outcome: city_grants.Outcome):
    response = jsonify(outcome.body)
    response.status_code = outcome.status_code
    return response


def _workload_ok() -> bool:
    if request.headers.get("X-SSL-Client-Verify") != "SUCCESS":
        return False
    raw = request.headers.get("X-SSL-Client-Cert", "")
    if not raw or len(raw) > 16384:
        return False
    try:
        cert = x509.load_pem_x509_certificate(unquote(raw).encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False
    fingerprint = hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()
    return any(hmac.compare_digest(fingerprint, allowed) for allowed in _settings()["workload_cert_sha256"])


def _bearer() -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


def _attestation_ok(signature: str, intent_digest: str) -> bool:
    try:
        _settings()["attestation_key"].verify(_b64url(signature), attestation_message(intent_digest))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def build_blueprint(db) -> Blueprint:
    bp = Blueprint("hallinto_city_grants", __name__, url_prefix="/internal/hallinto/v1")

    @bp.before_request
    def require_workload():
        # Never reveal anything, including whether a proof is valid, to an
        # unauthenticated caller.
        if not _workload_ok():
            return _error("denied")
        from mielenosoitukset_fi.utils.audit_trail import set_request_actor
        set_request_actor("integration", name=INTEGRATION)
        g.hallinto_workload = INTEGRATION

    @bp.post("/city-appointments")
    def apply_city_appointment():
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = city_grants.intent_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_CITY_ADMIN_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest):
            return _error("denied")
        return _reply(city_grants.apply_city_admin_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.get("/operations/by-key")
    def read_receipt():
        actor_id = grant_proofs.proof_actor(db, _bearer(), audience=AUDIENCE)
        if actor_id is None:
            return _error("denied")
        return _reply(city_grants.read_operation_receipt(
            db, integration=INTEGRATION, actor_id=actor_id,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        ))

    @bp.get("/city-grants/<account_id>")
    def read_state(account_id):
        actor_id = grant_proofs.proof_actor(db, _bearer(), audience=AUDIENCE)
        if actor_id is None or city_grants.authorized_actor(db, actor_id) is None:
            return _error("denied")
        state = city_grants.read_city_grant_state(db, account_id)
        # Conceal whether an account exists.
        return jsonify(state) if state else _error("denied")

    return bp


def register(app, db, settings: dict) -> None:
    fingerprints = settings.get("workload_cert_sha256")
    key = settings.get("attestation_public_key")
    if (not isinstance(fingerprints, list) or not fingerprints
            or not all(isinstance(f, str) and len(f) == 64 and f == f.lower() for f in fingerprints)
            or not isinstance(key, str)):
        raise ValueError("Hallinto city grant settings are incomplete")
    app.extensions["hallinto_city_grants"] = {
        "workload_cert_sha256": list(fingerprints),
        "attestation_key": Ed25519PublicKey.from_public_bytes(_b64url(key)),
    }
    city_grants.ensure_operation_indexes(db)
    grant_proofs.ensure_proof_indexes(db)
    app.register_blueprint(build_blueprint(db))


def configure(app, db) -> None:
    """Register only on explicit operator opt-in or a synthetic test override."""
    import os

    test_settings = app.config.get("HALLINTO_CITY_GRANTS") if app.testing else None
    path = os.environ.get("HALLINTO_CITY_GRANT_CONFIG_FILE")
    if not path and not test_settings:
        return
    if test_settings:
        register(app, db, test_settings)
        return
    from mielenosoitukset_fi.users.hallinto_issuer_config import _read_settings

    try:
        settings = _read_settings(path)
        register(app, db, settings)
    except Exception:
        raise RuntimeError("Hallinto city grant configuration is invalid") from None
