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
import json
import re
from datetime import timedelta, timezone
from urllib.parse import unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from bson import ObjectId

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding
from flask import Blueprint, current_app, g, jsonify, request, url_for

from . import city_grants, grant_proofs

HELSINKI = ZoneInfo("Europe/Helsinki")
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


def _proxy_ok() -> bool:
    """The internal vhost adds a secret header that public vhosts strip.

    A client certificate is public, so the forwarded certificate headers alone
    could be replayed through a public vhost that forgot to strip them.
    """
    secret = request.headers.get("X-Hallinto-Proxy-Secret", "")
    if not secret or len(secret) > 256:
        return False
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, _settings()["proxy_secret_sha256"])


def _workload_ok() -> bool:
    if not _proxy_ok():
        return False
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
        key = request.headers.get("Idempotency-Key", "")
        actor_id = grant_proofs.proof_actor(db, _bearer(), audience=AUDIENCE, idempotency_key=key)
        if actor_id is None:
            return _error("denied")
        return _reply(city_grants.read_operation_receipt(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key,
        ))

    @bp.get("/city-grants/<account_id>")
    def read_state(account_id):
        actor_id = grant_proofs.proof_actor(db, _bearer(), audience=AUDIENCE, subject=account_id)
        if actor_id is None or city_grants.authorized_actor(db, actor_id) is None:
            return _error("denied")
        state = city_grants.read_city_grant_state(db, account_id)
        # Conceal whether an account exists.
        return jsonify(state) if state else _error("denied")

    return bp


APPROVAL_WINDOW_SECONDS = 15 * 60  # Maintainer decision, 21 November 2026.
APPROVAL_FIELDS = {"account_id", "city", "template", "allow_city_enablement", "decision_id",
                   "decision_revision", "application_id", "valid_until"}
STATE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _decode_fields(raw: str):
    """The appointment terms Hallinto sends, as base64url JSON."""
    try:
        fields = json.loads(_b64url(raw or "").decode("utf-8")) if raw and len(raw) <= 4096 else None
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(fields, dict) or set(fields) - APPROVAL_FIELDS:
        return None
    return fields


def _same_origin() -> bool:
    origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
    host = request.host_url.rstrip("/")
    return origin == host or origin.startswith(host + "/")


def _no_framing(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return response


def _hallinto_home() -> str:
    url = urlsplit(_settings()["return_url"])
    return f"{url.scheme}://{url.netloc}/"


def build_approval_blueprint(db) -> Blueprint:
    """Step-up page between Hallinto's "Hyväksy" and the internal route.

    Hallinto redirects the verifier here with the appointment terms. Flask
    shows what it is about to sign for. After a passkey or password+TOTP
    step-up (valid 15 minutes) the verifier confirms; Flask issues a proof
    bound to the exact intent and the owner state shown, and redirects back
    to Hallinto's configured return URL with the proof in the fragment.
    """
    from flask import abort, redirect, render_template
    from flask_login import current_user, login_required

    from mielenosoitukset_fi.utils.cities import CITY_KEY_TO_NAME
    from mielenosoitukset_fi.utils.step_up import elevation_method, has_strong_elevation

    bp = Blueprint("hallinto_city_grant_approval", __name__, url_prefix="/admin/hallinto")

    def prepare():
        if city_grants.authorized_actor(db, current_user._id) is None:
            abort(403)
        fields = _decode_fields(request.values.get("r", ""))
        state_param = request.values.get("state", "")
        if fields is None or not STATE.match(state_param):
            abort(400)
        state = city_grants.read_city_grant_state(db, str(fields.get("account_id", "")))
        if state is None:
            abort(400)
        intent = {**fields, "expected_state": state["expected_state"], "authority_attestation": "-"}
        digest = city_grants.intent_digest(intent)
        if digest is None:
            abort(400)
        return fields, state_param, state, digest

    @bp.get("/approve")
    @login_required
    def approve_page():
        fields, state_param, state, digest = prepare()
        account = db.users.find_one({"_id": ObjectId(fields["account_id"])}, {"username": 1, "displayname": 1})
        end = city_grants.parse_valid_until(fields["valid_until"])
        last_day = end.replace(tzinfo=timezone.utc).astimezone(HELSINKI) - timedelta(seconds=1)
        from mielenosoitukset_fi.users.BPs.passkeys import _user_requires_totp
        from mielenosoitukset_fi.utils import webauthn_utils as webauthn
        response = current_app.make_response(render_template(
            "admin_V2/hallinto/approve.html",
            fields=fields, state_param=state_param, raw=request.values.get("r", ""),
            account=account or {}, city_name=CITY_KEY_TO_NAME.get(fields["city"], fields["city"]),
            last_day=last_day.strftime("%-d.%-m.%Y"), eligible=state["eligible"],
            elevated=has_strong_elevation(APPROVAL_WINDOW_SECONDS),
            has_passkey=webauthn.passkey_count(current_user._id) > 0,
            has_totp=_user_requires_totp(), hallinto_url=_hallinto_home(),
        ))
        return _no_framing(response)

    @bp.post("/approve")
    @login_required
    def approve_confirm():
        if not _same_origin():
            abort(403)
        fields, state_param, state, digest = prepare()
        if not has_strong_elevation(APPROVAL_WINDOW_SECONDS):
            return redirect(url_for(".approve_page", r=request.values.get("r"), state=state_param), code=303)
        factor = "passkey" if elevation_method() == "webauthn" else "password_totp"
        proof = grant_proofs.issue_proof(
            db, actor_id=current_user._id, audience=AUDIENCE, action=grant_proofs.ACTION_CITY_ADMIN_V1,
            intent_digest=digest, factor=factor, subject=str(fields["account_id"]),
        )
        fragment = urlencode({"state": state_param, "proof": proof, "expected_state": state["expected_state"]})
        response = _no_framing(redirect(_settings()["return_url"] + "#" + fragment, code=303))
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return bp


def register(app, db, settings: dict) -> None:
    fingerprints = settings.get("workload_cert_sha256")
    key = settings.get("attestation_public_key")
    proxy = settings.get("proxy_secret_sha256")
    if (not isinstance(proxy, str) or len(proxy) != 64 or proxy != proxy.lower()
            or not isinstance(fingerprints, list) or not fingerprints
            or not all(isinstance(f, str) and len(f) == 64 and f == f.lower() for f in fingerprints)
            or not isinstance(key, str)
            or not isinstance(settings.get("return_url"), str)
            or not settings["return_url"].startswith("https://")
            or "#" in settings["return_url"]):
        raise ValueError("Hallinto city grant settings are incomplete")
    app.extensions["hallinto_city_grants"] = {
        "workload_cert_sha256": list(fingerprints),
        "attestation_key": Ed25519PublicKey.from_public_bytes(_b64url(key)),
        "return_url": settings["return_url"],
        "proxy_secret_sha256": proxy,
    }
    city_grants.ensure_operation_indexes(db)
    grant_proofs.ensure_proof_indexes(db)
    app.register_blueprint(build_blueprint(db))
    app.register_blueprint(build_approval_blueprint(db))


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
