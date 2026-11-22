"""Approving or rejecting a duty shift, decided in Hallinto.

Booking stays in Flask; the OT member who approves does it in Hallinto's
Vuorot page. Hallinto sends one signed order naming the approver's Flask
account. Flask checks the signature (the caller already passed the mTLS
workload check), that the order is fresh, and then applies the same rules as
``duty_shifts.approve_shift``: the account must be an approver of the
moderator's appointment, may not approve their own shift, and a frozen or
revoked appointment approves nothing. Approving twice changes nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from bson import ObjectId

from mielenosoitukset_fi.utils import duty_shifts
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg

ACTIONS = {"approve", "reject"}
FIELDS = {"shift_id", "action", "approver_account_id", "issued_at", "reason", "authority_attestation"}
MAX_AGE = timedelta(minutes=5)
INSTANT = "%Y-%m-%dT%H:%M:%SZ"
STATUS = {
    "not_allowed": 403, "self_approval": 403, "invalid_actor": 403,
    "unknown_shift": 404, "unknown_grant": 404,
    "frozen_appointment": 409, "already_cancelled": 409, "stale": 409,
}


def validate(request: Any) -> Optional[dict]:
    """Canonical intent without the attestation, or None for an unsupported shape."""
    if not isinstance(request, dict) or set(request) != FIELDS:
        return None
    if not (isinstance(request["shift_id"], str) and cg.ACCOUNT_ID.match(request["shift_id"])
            and request["action"] in ACTIONS
            and isinstance(request["approver_account_id"], str) and cg.ACCOUNT_ID.match(request["approver_account_id"])
            and isinstance(request["issued_at"], str)
            and isinstance(request["reason"], str) and len(request["reason"]) <= 200
            and isinstance(request["authority_attestation"], str) and 1 <= len(request["authority_attestation"]) <= 16384):
        return None
    if request["action"] == "reject" and not request["reason"].strip():
        return None
    try:
        datetime.strptime(request["issued_at"], INSTANT)
    except ValueError:
        return None
    return {k: request[k] for k in sorted(FIELDS - {"authority_attestation"})}


def intent_digest(request: Any) -> Optional[str]:
    intent = validate(request)
    return cg._digest(intent) if intent is not None else None


def apply(db, request: dict, now: Optional[datetime] = None) -> tuple[int, dict]:
    """Carry out a validated, attested order. Returns (http status, body)."""
    current = now or utcnow()
    issued = datetime.strptime(request["issued_at"], INSTANT)
    naive_now = current.replace(tzinfo=None)
    if abs(naive_now - issued) > MAX_AGE:
        return 409, {"code": "stale"}
    approver = {"id": request["approver_account_id"], "username": "hallinto", "via": "hallinto"}
    shift_id = ObjectId(request["shift_id"])
    try:
        if request["action"] == "approve":
            changed = duty_shifts.approve_shift(db, shift_id=shift_id, actor=approver, now=current)
        else:
            pending = db[duty_shifts.SHIFTS].find_one({"_id": shift_id})
            if pending and str(pending["user_id"]) == approver["id"]:
                raise duty_shifts.DutyError("self_approval")
            if pending and pending.get("approved_at") and not pending.get("cancelled_at"):
                return 409, {"code": "stale"}
            changed = duty_shifts.cancel_shift(db, shift_id=shift_id, reason="Hylätty: " + request["reason"].strip(),
                                               actor=approver, now=current)
    except duty_shifts.DutyError as exc:
        return STATUS.get(exc.code, 422), {"code": exc.code}
    shift = db[duty_shifts.SHIFTS].find_one({"_id": shift_id}) or {}
    grant = db[duty_shifts.GRANTS].find_one({"_id": shift.get("grant_id")})
    state = duty_shifts.shift_state(shift, grant, current) if shift else "unknown"
    if request["action"] == "approve" and not changed and not shift.get("approved_at"):
        return 409, {"code": "already_cancelled"}
    if request["action"] == "reject" and not shift.get("cancelled_at"):
        return 409, {"code": "stale"}
    return 200, {"status": "confirmed", "shift_id": request["shift_id"], "action": request["action"], "state": state}
