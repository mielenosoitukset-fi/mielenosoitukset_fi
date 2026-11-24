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

from . import (
    city_grants, elevated_grants, flask_orders, grant_proofs, moderator_appointments, org_admin_grants,
    org_outreach, org_recovery, org_rights, role_bundles,
)

HELSINKI = ZoneInfo("Europe/Helsinki")
INTEGRATION = "hallinto"
AUDIENCE = "hallinto-city-grants"
ATTESTATION_CONTEXT = b"hallinto-city-admin-v1-attestation\n"
END_ATTESTATION_CONTEXT = b"hallinto-city-admin-end-v1-attestation\n"
ORDER_ATTESTATION_CONTEXT = b"hallinto-flask-order-v1-attestation\n"
MODERATOR_ATTESTATION_CONTEXT = b"hallinto-national-moderator-v1-attestation\n"
MODERATOR_END_ATTESTATION_CONTEXT = b"hallinto-national-moderator-end-v1-attestation\n"
BUNDLE_ATTESTATION_CONTEXT = b"hallinto-role-bundle-v1-attestation\n"
BUNDLE_END_ATTESTATION_CONTEXT = b"hallinto-role-bundle-end-v1-attestation\n"
SHIFT_APPROVAL_ATTESTATION_CONTEXT = b"hallinto-shift-approval-v1-attestation\n"
ELEVATED_ATTESTATION_CONTEXT = b"hallinto-elevated-v1-attestation\n"
INVITATION_ATTESTATION_CONTEXT = b"hallinto-org-invitation-v1-attestation\n"
RECOVERY_ATTESTATION_CONTEXT = b"hallinto-org-recovery-v1-attestation\n"
ORG_ADMIN_END_ATTESTATION_CONTEXT = b"hallinto-org-admin-end-v1-attestation\n"
ORG_RIGHT_ATTESTATION_CONTEXT = b"hallinto-org-right-v1-attestation\n"
ORG_RIGHT_REFUSAL_ATTESTATION_CONTEXT = b"hallinto-org-right-refusal-v1-attestation\n"
ORG_RIGHT_END_ATTESTATION_CONTEXT = b"hallinto-org-right-end-v1-attestation\n"


def attestation_message(intent_digest: str) -> bytes:
    """Bytes Hallinto signs: a fixed context line and the canonical intent digest."""
    return ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def end_attestation_message(intent_digest: str) -> bytes:
    """Ending signs a different context, so an appointment signature never ends a grant."""
    return END_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def order_attestation_message(intent_digest: str) -> bytes:
    """Orders sign yet another context, so no other signature can carry one out."""
    return ORDER_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def elevated_attestation_message(intent_digest: str) -> bytes:
    """Superadmin and god appointments sign their own context; no other signature can grant them."""
    return ELEVATED_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def invitation_attestation_message(intent_digest: str) -> bytes:
    return INVITATION_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def recovery_attestation_message(intent_digest: str) -> bytes:
    return RECOVERY_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def moderator_attestation_message(intent_digest: str) -> bytes:
    return MODERATOR_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def shift_approval_attestation_message(intent_digest: str) -> bytes:
    return SHIFT_APPROVAL_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def moderator_end_attestation_message(intent_digest: str) -> bytes:
    """Ending signs its own context, so an appointment signature never ends one."""
    return MODERATOR_END_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def bundle_attestation_message(intent_digest: str) -> bytes:
    return BUNDLE_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def org_admin_end_attestation_message(intent_digest: str) -> bytes:
    """Ending an org_admin grant signs its own context."""
    return ORG_ADMIN_END_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def org_right_attestation_message(intent_digest: str) -> bytes:
    """Granting the organisation's approval right signs its own context."""
    return ORG_RIGHT_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def org_right_refusal_attestation_message(intent_digest: str) -> bytes:
    return ORG_RIGHT_REFUSAL_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def org_right_end_attestation_message(intent_digest: str) -> bytes:
    """A grant signature can never end a right."""
    return ORG_RIGHT_END_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


def bundle_end_attestation_message(intent_digest: str) -> bytes:
    """Ending a bundle signs its own context, so an appointment signature never ends one."""
    return BUNDLE_END_ATTESTATION_CONTEXT + intent_digest.encode("ascii")


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


def _attestation_ok(signature: str, intent_digest: str, message=attestation_message) -> bool:
    try:
        _settings()["attestation_key"].verify(_b64url(signature), message(intent_digest))
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

    @bp.post("/city-appointment-ends")
    def end_city_appointment():
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = city_grants.end_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_CITY_ADMIN_END_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, end_attestation_message):
            return _error("denied")
        return _reply(city_grants.apply_city_admin_end_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/orders")
    def apply_order():
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        if (isinstance(body, dict) and body.get("kind") == flask_orders.KIND_SITE_LOCKDOWN
                and not _lockdown_orders_on()):
            return "", 404
        digest = flask_orders.order_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_FLASK_ORDER_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, order_attestation_message):
            return _error("denied")
        return _reply(flask_orders.apply_order(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.get("/orders/<order_id>")
    def read_order_status(order_id):
        """State of one order, so Hallinto can settle one whose answer it lost.

        Read-only. The mTLS workload identity is the only check, as for the
        duty-shift feed: nothing here changes anything.
        """
        return _reply(flask_orders.read_order_status(db, integration=INTEGRATION, order_id=order_id))

    @bp.get("/duty-shifts")
    def read_duty_shifts():
        """Who is on duty when: names, times and state, for Hallinto's Vuorot page.

        The mTLS workload identity is the only check: nothing here changes
        anything, and nothing beyond the shift calendar is shown.
        """
        from datetime import datetime

        from mielenosoitukset_fi.utils import duty_shifts

        try:
            day = datetime.fromisoformat(request.args.get("from", ""))
            span = int(request.args.get("days", "7"))
            start = datetime(day.year, day.month, day.day, tzinfo=HELSINKI).astimezone(timezone.utc)
            return jsonify({"shifts": duty_shifts.week_overview(
                db, start, start + timedelta(days=span), with_approval=True)})
        except (ValueError, OverflowError, duty_shifts.DutyError):
            return _error("unsupported_scope")

    @bp.post("/shift-approvals")
    def decide_shift():
        """Approve or reject a booked shift for an approver Hallinto names.

        Signed by Hallinto for exactly this shift, action, approver and moment.
        Flask still checks that the account approves this moderator's shifts.
        """
        from . import shift_approvals

        body = request.get_json(silent=True)
        digest = shift_approvals.intent_digest(body)
        if digest is None:
            return _error("unsupported_scope")
        if not _attestation_ok(body["authority_attestation"], digest, shift_approval_attestation_message):
            return _error("denied")
        status, payload = shift_approvals.apply(db, body)
        response = jsonify(payload)
        response.status_code = status
        return response

    @bp.post("/moderator-appointments")
    def apply_moderator_appointment():
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = moderator_appointments.intent_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_MODERATOR_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, moderator_attestation_message):
            return _error("denied")
        return _reply(moderator_appointments.apply_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/moderator-appointments/<appointment_id>/end")
    def end_moderator_appointment(appointment_id):
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = moderator_appointments.end_digest(body)
        if (digest is None or not city_grants.IDEMPOTENCY_KEY.match(key)
                or body["appointment_id"] != appointment_id):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_MODERATOR_END_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, moderator_end_attestation_message):
            return _error("denied")
        return _reply(moderator_appointments.apply_end_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/role-bundles")
    def apply_role_bundle():
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = role_bundles.bundle_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ROLE_BUNDLE_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, bundle_attestation_message):
            return _error("denied")
        return _reply(role_bundles.apply_role_bundle_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/role-bundles/<appointment_id>/end")
    def end_role_bundle(appointment_id):
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = role_bundles.end_digest(body)
        if (digest is None or not city_grants.IDEMPOTENCY_KEY.match(key)
                or body["appointment_id"] != appointment_id):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ROLE_BUNDLE_END_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, bundle_end_attestation_message):
            return _error("denied")
        return _reply(role_bundles.apply_role_bundle_end_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    def _org_admin_on() -> bool:
        return current_app.config.get("ORG_ADMIN_HALLINTO_ENABLED") is True

    @bp.post("/org-admin-ends")
    def end_org_admin():
        if not _org_admin_on():
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = org_admin_grants.end_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key) or body["end_id"] != key:
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ORG_ADMIN_END_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, org_admin_end_attestation_message):
            return _error("denied")
        return _reply(org_admin_grants.apply_org_admin_end_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.get("/org-admins/<account_id>")
    def read_org_admin_account(account_id):
        """Read only, workload identity only: which organisations the account administers."""
        if not _org_admin_on():
            return "", 404
        state = org_admin_grants.read_account_state(db, account_id)
        return jsonify(state) if state is not None else _error("unsupported_scope")

    @bp.get("/organizations/<organization_id>/org-admins")
    def read_organization_org_admins(organization_id):
        if not _org_admin_on():
            return "", 404
        state = org_admin_grants.read_organization_admins(db, organization_id)
        return jsonify(state) if state is not None else _error("unsupported_scope")

    def _org_right_on() -> bool:
        return current_app.config.get("ORG_APPROVAL_RIGHT_ENABLED") is True

    @bp.get("/org-right-requests")
    def read_org_right_requests():
        """Read only, workload identity only: the organisations' requests for the approval right."""
        if not _org_right_on():
            return "", 404
        feed = org_rights.request_feed(db, request.args.get("status", "open"))
        return jsonify(feed) if feed is not None else _error("unsupported_scope")

    @bp.post("/org-rights")
    def grant_org_right():
        if not _org_right_on():
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = org_rights.grant_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key) or body["grant_id"] != key:
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ORG_RIGHT_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, org_right_attestation_message):
            return _error("denied")
        return _reply(org_rights.apply_grant(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/org-right-requests/<case_id>/refusal")
    def refuse_org_right(case_id):
        if not _org_right_on():
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = org_rights.refusal_digest(body)
        if (digest is None or not city_grants.IDEMPOTENCY_KEY.match(key) or body["refusal_id"] != key
                or body["case_id"] != case_id):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ORG_RIGHT_REFUSAL_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest,
                                                   org_right_refusal_attestation_message):
            return _error("denied")
        return _reply(org_rights.apply_refusal(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/org-rights/<grant_id>/end")
    def end_org_right(grant_id):
        if not _org_right_on():
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = org_rights.end_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key) or body["grant_id"] != grant_id:
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ORG_RIGHT_END_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest,
                                                   org_right_end_attestation_message):
            return _error("denied")
        return _reply(org_rights.apply_end(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.get("/org-rights/<organization_id>")
    def read_org_right(organization_id):
        if not _org_right_on():
            return "", 404
        state = org_rights.read_state(db, organization_id)
        return jsonify(state) if state is not None else _error("unsupported_scope")

    def _outreach_on() -> bool:
        return current_app.config.get("ORG_OUTREACH_ENABLED") is True

    @bp.post("/org-invitations")
    def send_org_invitation():
        if not _outreach_on():
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = org_outreach.invitation_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ORG_INVITATION_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, invitation_attestation_message):
            return _error("denied")
        return _reply(org_outreach.apply_invitation(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/org-recoveries")
    def apply_org_recovery():
        if current_app.config.get("ORG_RECOVERY_ENABLED") is not True:
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = org_recovery.recovery_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ORG_RECOVERY_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, recovery_attestation_message):
            return _error("denied")
        return _reply(org_recovery.apply_recovery(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.post("/org-invitations/check")
    def check_org_invitation():
        if not _outreach_on():
            return "", 404
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"address"}:
            return _error("unsupported_scope")
        return jsonify(org_outreach.read_check(db, body["address"]))

    def _elevated_on() -> bool:
        return current_app.config.get("HALLINTO_ELEVATED_ENABLED") is True

    @bp.post("/elevated-appointments")
    def apply_elevated_appointment():
        if not _elevated_on():
            return "", 404
        key = request.headers.get("Idempotency-Key", "")
        body = request.get_json(silent=True)
        digest = elevated_grants.elevated_digest(body)
        if digest is None or not city_grants.IDEMPOTENCY_KEY.match(key):
            return _error("unsupported_scope")
        actor_id = grant_proofs.consume_proof(
            db, _bearer(), audience=AUDIENCE, action=grant_proofs.ACTION_ELEVATED_V1,
            intent_digest=digest, idempotency_key=key,
        )
        if actor_id is None or not _attestation_ok(body["authority_attestation"], digest, elevated_attestation_message):
            return _error("denied")
        return _reply(elevated_grants.apply_elevated_v1(
            db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key, request=body,
        ))

    @bp.get("/elevated-grants/<account_id>")
    def read_elevated(account_id):
        if not _elevated_on():
            return "", 404
        actor_id = grant_proofs.proof_actor(db, _bearer(), audience=AUDIENCE, subject=account_id)
        if actor_id is None or city_grants.authorized_actor(db, actor_id) is None:
            return _error("denied")
        state = elevated_grants.read_elevated_state(db, account_id)
        return jsonify(state) if state else _error("denied")

    @bp.get("/operations/by-key")
    def read_receipt():
        key = request.headers.get("Idempotency-Key", "")
        actor_id = grant_proofs.proof_actor(db, _bearer(), audience=AUDIENCE, idempotency_key=key)
        if actor_id is None:
            return _error("denied")
        if _elevated_on():
            outcome = elevated_grants.read_elevated_receipt(
                db, integration=INTEGRATION, actor_id=actor_id, idempotency_key=key)
            if outcome.status_code != 404:
                return _reply(outcome)
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
END_APPROVAL_FIELDS = {"appointment_id", "kind", "account_id", "city", "template", "decision_id",
                       "decision_revision"}
ORDER_APPROVAL_FIELDS = {
    flask_orders.KIND_ROLE_REMOVAL: {"order_id", "kind", "decision_id", "decision_revision", "account_id", "role"},
    flask_orders.KIND_BOARD_ROSTER: {"order_id", "kind", "decision_id", "decision_revision", "roster"},
    flask_orders.KIND_SITE_LOCKDOWN: {"order_id", "kind", "decision_id", "decision_revision", "action"},
    flask_orders.KIND_ACCESS_REVIEW: {"order_id", "kind", "decision_id", "decision_revision", "roster"},
}
ELEVATED_APPROVAL_FIELDS = {"account_id", "template", "decision_id", "decision_revision", "valid_until"}
BUNDLE_APPROVAL_FIELDS = {"account_id", "template", "decision_id", "decision_revision", "valid_until"}
BUNDLE_END_APPROVAL_FIELDS = {"appointment_id", "kind", "account_id", "template", "decision_id", "decision_revision"}
INVITATION_APPROVAL_FIELDS = {"invitation_id", "case_id", "decision_id", "decision_revision",
                              "organization_name", "address", "source_url"}
RECOVERY_APPROVAL_FIELDS = {"request_id", "case_id", "decision_id", "decision_revision", "need",
                            "organization_id", "person_email", "person_role"}
STATE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _lockdown_orders_on() -> bool:
    return current_app.config.get("HALLINTO_LOCKDOWN_ORDERS_ENABLED") is True


def _decode_fields(raw: str):
    """The appointment terms Hallinto sends, as base64url JSON."""
    try:
        # A board roster lists accounts; the longest still fits a request line.
        fields = json.loads(_b64url(raw or "").decode("utf-8")) if raw and len(raw) <= 8000 else None
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(fields, dict):
        return None
    if "order_id" in fields:
        if fields.get("kind") == flask_orders.KIND_SITE_LOCKDOWN and not _lockdown_orders_on():
            return None
        return fields if set(fields) == ORDER_APPROVAL_FIELDS.get(fields.get("kind")) else None
    if org_admin_grants.is_org_admin_fields(fields):
        if current_app.config.get("ORG_ADMIN_HALLINTO_ENABLED") is not True:
            return None
        return fields if set(fields) == org_admin_grants.END_APPROVAL_FIELDS else None
    if org_rights.is_right_fields(fields):
        if current_app.config.get("ORG_APPROVAL_RIGHT_ENABLED") is not True:
            return None
        return fields if org_rights.field_kind(fields) else None
    if "request_id" in fields and "need" in fields:
        return fields if set(fields) == RECOVERY_APPROVAL_FIELDS else None
    if "invitation_id" in fields:
        allowed = INVITATION_APPROVAL_FIELDS
        return fields if INVITATION_APPROVAL_FIELDS <= set(fields) <= allowed | {"note"} else None
    if fields.get("template") in elevated_grants.TEMPLATES:
        if not _elevated_enabled():
            return None
        extra = {"concurring_decision_id"} if fields["template"] == elevated_grants.GOD_V1 else set()
        return fields if set(fields) == ELEVATED_APPROVAL_FIELDS | extra else None
    if moderator_appointments.is_moderator_fields(fields):
        wanted = (moderator_appointments.END_APPROVAL_FIELDS if "kind" in fields
                  else moderator_appointments.APPROVAL_FIELDS)
        return fields if set(fields) == wanted else None
    if fields.get("template") in role_bundles.BUNDLES and "city" not in fields:
        wanted = BUNDLE_END_APPROVAL_FIELDS if "kind" in fields else BUNDLE_APPROVAL_FIELDS
        return fields if set(fields) == wanted else None
    if "kind" in fields:
        return fields if set(fields) == END_APPROVAL_FIELDS else None
    return fields if not set(fields) - APPROVAL_FIELDS else None


def _elevated_enabled() -> bool:
    return current_app.config.get("HALLINTO_ELEVATED_ENABLED") is True


def _is_elevated(fields: dict) -> bool:
    return fields.get("template") in elevated_grants.TEMPLATES


def _is_bundle(fields: dict) -> bool:
    return fields.get("template") in role_bundles.BUNDLES and "city" not in fields


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
        if org_rights.is_right_fields(fields):
            state = org_rights.approval_state(db, fields)
            if state is None:
                abort(400)
            kind = state["kind"]
            terms = {**fields, "authority_attestation": "-"}
            digest = (org_rights.grant_digest({**terms, "expected_state": state["expected_state"]}) if kind == "grant"
                      else org_rights.refusal_digest(terms) if kind == "refusal" else org_rights.end_digest(terms))
            if digest is None:
                abort(400)
            return fields, state_param, state, digest
        if org_admin_grants.is_org_admin_fields(fields):
            digest = org_admin_grants.end_digest({**fields, "authority_attestation": "-"})
            organization = (db.organizations.find_one({"_id": ObjectId(fields["organization_id"])}, {"name": 1})
                            if digest and org_admin_grants._id_ok(fields["organization_id"]) else None)
            if digest is None or organization is None or not org_admin_grants._id_ok(fields["account_id"]):
                abort(400)
            # Ending is idempotent and only removes access: no expected_state.
            return fields, state_param, {"eligible": True, "organization_name": organization.get("name") or ""}, digest
        if "request_id" in fields:
            if current_app.config.get("ORG_RECOVERY_ENABLED") is not True:
                abort(404)
            state = org_recovery.approval_state(db, fields)
            if state is None:
                abort(400)
            digest = org_recovery.recovery_digest({**fields, "expected_state": state["expected_state"],
                                                   "authority_attestation": "-"})
            if digest is None:
                abort(400)
            return fields, state_param, state, digest
        if "invitation_id" in fields:
            if current_app.config.get("ORG_OUTREACH_ENABLED") is not True:
                abort(404)
            digest = org_outreach.invitation_digest({**fields, "authority_attestation": "-"})
            if digest is None:
                abort(400)
            # Eligible unless the address is already known to be off limits (opt-out or role box).
            return fields, state_param, {"eligible": org_outreach.check_address(db, fields["address"]) is None}, digest
        if "order_id" in fields:
            # An order names no appointment: it is idempotent and checked at apply.
            digest = flask_orders.order_digest({**fields, "authority_attestation": "-"})
            if digest is None or (fields.get("account_id") and db.users.find_one(
                    {"_id": ObjectId(fields["account_id"])}, {"_id": 1}) is None):
                abort(400)
            return fields, state_param, {"eligible": True}, digest
        if _is_elevated(fields):
            state = elevated_grants.read_elevated_state(db, str(fields.get("account_id", "")))
            if state is None:
                abort(400)
            digest = elevated_grants.elevated_digest(
                {**fields, "expected_state": state["expected_state"], "authority_attestation": "-"})
            if digest is None:
                abort(400)
            return fields, state_param, {**state, "eligible": state["eligible"][fields["template"]]}, digest
        if moderator_appointments.is_moderator_fields(fields):
            state = moderator_appointments.read_state(db, str(fields.get("account_id", "")))
            if state is None:
                abort(400)
            if "kind" in fields:
                # Ending needs no owner-state check: it is idempotent and removes access.
                digest = moderator_appointments.end_digest({**fields, "authority_attestation": "-"})
                state = {**state, "eligible": True}
            else:
                digest = moderator_appointments.intent_digest(
                    {**fields, "expected_state": state["expected_state"], "authority_attestation": "-"})
            if digest is None:
                abort(400)
            return fields, state_param, state, digest
        if _is_bundle(fields):
            state = role_bundles.read_bundle_state(db, str(fields.get("account_id", "")), fields["template"])
            if state is None:
                abort(400)
            if "kind" in fields:
                # Ending needs no owner-state check: it is idempotent and removes access.
                digest = role_bundles.end_digest({**fields, "authority_attestation": "-"})
                state = {**state, "eligible": True}
            else:
                digest = role_bundles.bundle_digest(
                    {**fields, "expected_state": state["expected_state"], "authority_attestation": "-"})
            if digest is None:
                abort(400)
            return fields, state_param, state, digest
        state = city_grants.read_city_grant_state(db, str(fields.get("account_id", "")))
        if state is None:
            abort(400)
        if "kind" in fields:
            # Ending needs no owner-state check: it is idempotent and removes access.
            digest = city_grants.end_digest({**fields, "authority_attestation": "-"})
            state = {**state, "eligible": True}
        else:
            intent = {**fields, "expected_state": state["expected_state"], "authority_attestation": "-"}
            digest = city_grants.intent_digest(intent)
        if digest is None:
            abort(400)
        return fields, state_param, state, digest

    @bp.get("/approve")
    @login_required
    def approve_page():
        fields, state_param, state, digest = prepare()
        is_invite = "invitation_id" in fields
        is_recovery = "request_id" in fields
        is_order = "order_id" in fields
        masked = ""
        if is_invite or is_recovery:
            local, _, domain = fields["address" if is_invite else "person_email"].partition("@")
            masked = f"{local[:1]}***@{domain}"
        account = (db.users.find_one({"_id": ObjectId(fields["account_id"])}, {"username": 1, "displayname": 1})
                   if fields.get("account_id") else None)
        roster_names, removed_names, added_names = [], [], []
        if is_order and fields.get("roster"):
            names = {str(u["_id"]): u.get("displayname") or u.get("username") or "?" for u in db.users.find(
                {"_id": {"$in": [ObjectId(a) for a in fields["roster"]]}}, {"username": 1, "displayname": 1})}
            roster_names = [names.get(a, "?") for a in fields["roster"]]
            from mielenosoitukset_fi.utils.system_teams import board_ids
            current = set(board_ids(db)) if fields.get("kind") == flask_orders.KIND_BOARD_ROSTER else set(fields["roster"])
            leaving = sorted(current - set(fields["roster"]))
            removed_names = [u.get("displayname") or u.get("username") or "?" for u in db.users.find(
                {"_id": {"$in": [ObjectId(a) for a in leaving]}}, {"username": 1, "displayname": 1})]
            added_names = [names.get(a, "?") for a in fields["roster"] if a not in current]
        is_end = "kind" in fields and not is_order and not is_invite
        is_elevated = _is_elevated(fields)
        is_org_admin = org_admin_grants.is_org_admin_fields(fields)
        is_org_right = org_rights.is_right_fields(fields)
        right_kind = state.get("kind") if is_org_right else ""
        is_bundle = _is_bundle(fields)
        bundle = role_bundles.BUNDLES.get(fields.get("template")) if is_bundle else None
        is_moderator = moderator_appointments.is_moderator_fields(fields)
        approver_names = []
        permission_copy = []
        if is_moderator:
            from mielenosoitukset_fi.admin.permission_copy import PERMISSION_DESCRIPTIONS
            permission_copy = [str(PERMISSION_DESCRIPTIONS.get(p, p)) for p in moderator_appointments.PERMISSIONS]
            approver_names = sorted(u.get("displayname") or u.get("username") or "?" for u in db.users.find(
                {"_id": {"$in": [ObjectId(a) for a in fields.get("shift_approvers", [])]}}, {"username": 1, "displayname": 1}))
        if bundle:
            from mielenosoitukset_fi.admin.permission_copy import PERMISSION_DESCRIPTIONS
            permission_copy = [str(PERMISSION_DESCRIPTIONS.get(p, p)) for p in bundle["permissions"]]
        page_title = ("Vahvista superadmin- tai god-nimitys" if is_elevated else "Vahvista kutsun lähetys" if is_invite else "Vahvista pääsyn palautus" if is_recovery else "Vahvista roolin poisto" if fields.get("kind") == flask_orders.KIND_ROLE_REMOVAL
                      else "Vahvista hätäsulku" if fields.get("kind") == flask_orders.KIND_SITE_LOCKDOWN and fields.get("action") == "close"
                      else "Vahvista sivuston avaaminen" if fields.get("kind") == flask_orders.KIND_SITE_LOCKDOWN
                      else "Vahvista ylläpitotilien tarkistus" if fields.get("kind") == flask_orders.KIND_ACCESS_REVIEW
                      else "Vahvista hallitustiimin päivitys" if is_order
                      else {"grant": "Vahvista hyväksymisoikeuden myöntäminen", "refusal": "Vahvista hyväksymisoikeuspyynnön hylkääminen",
                            "end": "Vahvista hyväksymisoikeuden päättäminen"}[right_kind] if is_org_right
                      else "Vahvista organisaation ylläpito-oikeuden päättäminen" if is_org_admin
                      else "Vahvista roolipaketin päättäminen" if (is_end and is_bundle)
                      else "Vahvista roolipaketti" if is_bundle
                      else "Vahvista nimityksen päättäminen" if is_end else "Vahvista nimitys")
        last_day = ""
        if not is_end and not is_order and not is_invite and not is_recovery and "valid_until" in fields:
            end = city_grants.parse_valid_until(fields["valid_until"])
            last_day = (end.replace(tzinfo=timezone.utc).astimezone(HELSINKI)
                        - timedelta(seconds=1)).strftime("%-d.%-m.%Y")
        from mielenosoitukset_fi.users.BPs.passkeys import _user_requires_totp
        from mielenosoitukset_fi.utils import webauthn_utils as webauthn
        response = current_app.make_response(render_template(
            "admin_V2/hallinto/approve.html",
            fields=fields, state_param=state_param, raw=request.values.get("r", ""),
            account=account or {}, city_name=CITY_KEY_TO_NAME.get(fields.get("city"), fields.get("city")),
            last_day=last_day, is_end=is_end, is_order=is_order, roster_names=roster_names, removed_names=removed_names, added_names=added_names,
            is_invite=is_invite, masked_address=masked, is_recovery=is_recovery,
            recovery_organization=(state.get("name") if is_recovery else ""),
            recovery_replaced=(state.get("replaced_owners", 0) if is_recovery else 0),
            is_elevated=is_elevated, elevated_label=elevated_grants.TEMPLATES.get(fields.get("template"), ""),
            is_moderator=is_moderator, approver_names=approver_names,
            is_org_right=is_org_right, right_kind=right_kind, right_state=(state if is_org_right else {}),
            is_org_admin=is_org_admin, org_admin_organization=(state.get("organization_name", "") if is_org_admin else ""),
            is_bundle=is_bundle, bundle_label=(bundle or {}).get("label", ""), permission_copy=permission_copy,
            page_title=page_title,
            eligible=state["eligible"],
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
        is_order = "order_id" in fields
        is_invite = "invitation_id" in fields
        is_recovery = "request_id" in fields
        is_end = "kind" in fields and not is_order and not is_invite
        is_bundle = _is_bundle(fields)
        is_org_right = org_rights.is_right_fields(fields)
        action = ({"grant": grant_proofs.ACTION_ORG_RIGHT_V1, "refusal": grant_proofs.ACTION_ORG_RIGHT_REFUSAL_V1,
                   "end": grant_proofs.ACTION_ORG_RIGHT_END_V1}[state["kind"]] if is_org_right
                  else grant_proofs.ACTION_ORG_ADMIN_END_V1 if org_admin_grants.is_org_admin_fields(fields)
                  else grant_proofs.ACTION_ELEVATED_V1 if _is_elevated(fields)
                  else (grant_proofs.ACTION_MODERATOR_END_V1 if is_end else grant_proofs.ACTION_MODERATOR_V1)
                  if moderator_appointments.is_moderator_fields(fields)
                  else grant_proofs.ACTION_ORG_INVITATION_V1 if is_invite
                  else grant_proofs.ACTION_ORG_RECOVERY_V1 if is_recovery
                  else grant_proofs.ACTION_FLASK_ORDER_V1 if is_order
                  else (grant_proofs.ACTION_ROLE_BUNDLE_END_V1 if is_end else grant_proofs.ACTION_ROLE_BUNDLE_V1) if is_bundle
                  else grant_proofs.ACTION_CITY_ADMIN_END_V1 if is_end else grant_proofs.ACTION_CITY_ADMIN_V1)
        proof = grant_proofs.issue_proof(
            db, actor_id=current_user._id, audience=AUDIENCE, action=action,
            intent_digest=digest, factor=factor, subject=fields.get("account_id"),
        )
        returned = {"state": state_param, "proof": proof}
        if is_org_right:
            if state["kind"] == "grant":
                returned["expected_state"] = state["expected_state"]
        elif not is_end and not is_order and not is_invite:
            returned["expected_state"] = state["expected_state"]
        fragment = urlencode(returned)
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
    from mielenosoitukset_fi.users.hallinto_issuer_config import _read_settings, config_error_reason
    from mielenosoitukset_fi.utils.logger import logger

    try:
        settings = _read_settings(path)
        register(app, db, settings)
    except Exception as error:  # noqa: BLE001
        # A missing, unreadable or invalid file must not crash every worker and
        # take the whole site down (incident 2026-11-21). The optional grant
        # routes stay off, nothing is registered, and the error is loud. The
        # message names no path or value: they may be secrets.
        app.extensions.pop("hallinto_city_grants", None)
        reason = config_error_reason(error)
        app.extensions["hallinto_city_grants_disabled"] = reason
        logger.error(
            "HALLINTO_CITY_GRANT_CONFIG_FILE is set but unusable (%s): the Hallinto grant, "
            "end, order and role-bundle routes are DISABLED. Fix the file (readable by the "
            "service user, mode 0600, valid JSON) and restart.", reason,
        )
