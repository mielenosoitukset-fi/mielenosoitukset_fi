"""National moderator appointments decided in Hallinto and written by Flask.

A national moderator appointment (``national_moderator_v1``) records who may
book duty shifts and who approves them. It grants no rights by itself: rights
exist only during an approved shift (``utils/duty_shifts``). This module shares
the journal, the account fence and the trust model with ``city_grants`` and
``role_bundles``. The caller has already verified the mTLS workload identity,
the action-bound proof and Hallinto's attestation for the exact intent; Flask
re-checks the actor, the account and its state here.

Hallinto sends the shift approvers (the deciding group's account-linked
members) and ``self_booking`` as part of the signed intent, so neither can be
changed on the way.
"""

from __future__ import annotations

from typing import Any, Optional

from bson import ObjectId

from mielenosoitukset_fi.utils import duty_shifts
from mielenosoitukset_fi.utils.duty_shifts import DutyError, NATIONAL_MODERATOR_V1
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg
from . import role_bundles as rb

TEMPLATE = "national_moderator_v1"
LABEL = "Valtakunnallinen moderaattori"
# Flask owns this list; Hallinto confirms only exactly this set in the receipt.
PERMISSIONS = sorted(NATIONAL_MODERATOR_V1)
MAX_APPROVERS = 50

APPLY_FIELDS = {"account_id", "template", "decision_id", "decision_revision", "valid_until",
                "self_booking", "shift_approvers", "expected_state", "authority_attestation"}
APPROVAL_FIELDS = {"account_id", "template", "decision_id", "decision_revision", "valid_until",
                   "self_booking", "shift_approvers"}


END_FIELDS = {"appointment_id", "kind", "account_id", "template", "decision_id", "decision_revision",
              "authority_attestation"}
END_APPROVAL_FIELDS = END_FIELDS - {"authority_attestation"}
END_KINDS = {"revoke": "hallinto_revoked", "freeze": "hallinto_frozen"}


def is_moderator_fields(fields: Any) -> bool:
    return isinstance(fields, dict) and fields.get("template") == TEMPLATE


def _approvers_ok(value: Any, account_id: str) -> bool:
    return bool(isinstance(value, list) and len(value) <= MAX_APPROVERS
                and all(isinstance(a, str) and cg.ACCOUNT_ID.match(a) for a in value)
                and value == sorted(set(value)) and account_id not in value)


def validate_apply(request: Any) -> Optional[dict]:
    """Canonical intent (attestation excluded, the permissions added), or None."""
    if not isinstance(request, dict) or set(request) != APPLY_FIELDS:
        return None
    account_id, revision = request["account_id"], request["decision_revision"]
    attestation = request["authority_attestation"]
    if not (isinstance(account_id, str) and cg.ACCOUNT_ID.match(account_id)
            and request["template"] == TEMPLATE
            and cg._non_empty_id(request["decision_id"])
            and type(revision) is int and revision >= 1
            and type(request["self_booking"]) is bool
            and _approvers_ok(request["shift_approvers"], account_id)
            and cg._non_empty_id(request["expected_state"])
            and cg.parse_valid_until(request["valid_until"]) is not None
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384):
        return None
    intent = {key: request[key] for key in sorted(APPLY_FIELDS - {"authority_attestation"})}
    intent["permissions"] = list(PERMISSIONS)
    return intent


def intent_digest(request: Any) -> Optional[str]:
    intent = validate_apply(request)
    return cg._digest(intent) if intent is not None else None


# --------------------------------------------------------------------------- #
# Owner state
# --------------------------------------------------------------------------- #

def _live_grants(db, account_id: ObjectId) -> list:
    """Appointments not revoked, frozen or expired. A frozen one is lifted only by a new decision."""
    now = utcnow()
    return [g for g in db[duty_shifts.GRANTS].find(
                {"user_id": account_id, "revoked_at": None, "frozen_at": None})
            if duty_shifts._naive(duty_shifts._utc(g["valid_until"], "valid_until")) > duty_shifts._naive(now)]


def _inputs(db, doc: dict) -> dict:
    """Owner fields that affect the command, for the opaque expected_state."""
    return {
        "account": str(doc["_id"]),
        "active": doc.get("active", True), "banned": doc.get("banned", False),
        "confirmed": doc.get("confirmed", False),
        "suspended": bool((doc.get("security_suspension") or {}).get("active")),
        "organization": doc.get("account_type") == "organization",
        "grants": sorted(str(g["_id"]) for g in _live_grants(db, doc["_id"])),
    }


def _eligible(inputs: dict) -> bool:
    """An active personal account without a live moderator appointment."""
    from mielenosoitukset_fi.utils.system_teams import active_account

    return bool(active_account({"active": inputs["active"], "banned": inputs["banned"],
                                "confirmed": inputs["confirmed"],
                                "security_suspension": {"active": inputs["suspended"]}})
                and not inputs["organization"] and not inputs["grants"])


def read_state(db, account_id: str) -> Optional[dict]:
    """Authoritative state for the approval page; None when the account does not exist."""
    if not isinstance(account_id, str) or not cg.ACCOUNT_ID.match(account_id):
        return None
    doc = db.users.find_one({"_id": ObjectId(account_id)})
    if not doc:
        return None
    inputs = _inputs(db, doc)
    return {"account_id": account_id, "expected_state": cg._digest(inputs), "eligible": _eligible(inputs)}


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def apply_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Appoint one account as national moderator for the approvers and terms signed."""
    intent = validate_apply(request)
    actor, error = rb._precheck(db, integration, idempotency_key, intent, actor_id)
    if error is not None:
        return error
    operation, replayed = rb._new_operation(db, actor, integration, idempotency_key, intent, cg._digest(intent))
    if operation is None:
        return replayed
    return rb._run_fenced(db, operation, intent, actor, integration, _apply_fenced)


def _grant_of(db, operation_id) -> Optional[dict]:
    return db[duty_shifts.GRANTS].find_one({"source.operation_id": str(operation_id)})


def _apply_fenced(db, operation, intent, actor, integration, account_id, fence) -> cg.Outcome:
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return cg._outcome_for(cg._reject(db, operation, "denied"))  # Conceal existence.
    inputs = _inputs(db, doc)
    if cg._digest(inputs) != intent["expected_state"]:
        return cg._outcome_for(cg._reject(db, operation, "stale_state"))
    if not _eligible(inputs):
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))
    expires_at = cg.parse_valid_until(intent["valid_until"])
    now = utcnow()
    if not (now < expires_at <= now + cg.MAX_VALIDITY):
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))
    known = {str(u["_id"]) for u in db.users.find(
        {"_id": {"$in": [ObjectId(a) for a in intent["shift_approvers"]]}}, {"_id": 1})}
    if known != set(intent["shift_approvers"]):
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))

    def write(acting):
        try:
            _appoint(acting)
        except DutyError as exc:  # Checked above; if it still happens an operator reconciles.
            raise cg.CityGrantConflict(f"appointment refused: {exc}") from exc

    def _appoint(acting):
        duty_shifts.appoint(
            db, user_id=account_id, valid_from=now, valid_until=expires_at,
            source={"decision": f"Hallinto {intent['decision_id']} v{intent['decision_revision']}",
                    "hallinto_decision_id": intent["decision_id"],
                    "hallinto_decision_revision": intent["decision_revision"],
                    "operation_id": str(operation["_id"])},
            actor={"id": str(acting._id), "username": getattr(acting, "username", None), "via": "hallinto"},
            permissions=PERMISSIONS, approvers=intent["shift_approvers"],
            self_booking=intent["self_booking"], now=now)

    operation, failed = rb._write(db, operation, intent, actor, integration, account_id, fence,
                                  "national_moderator_v1", write)
    if failed is not None:
        return failed

    grant = _grant_of(db, operation["_id"])
    ok = bool(grant and grant["user_id"] == account_id and sorted(grant["permissions"]) == PERMISSIONS
              and bool(grant.get("self_booking")) == intent["self_booking"]
              and sorted(str(a) for a in grant.get("approvers") or ()) == intent["shift_approvers"])
    if not ok or not cg.fence_held(db, account_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {
        **cg._receipt_identity(operation), "status": "confirmed",
        "checked_at": utcnow().strftime(cg.INSTANT),
        "effective": {"city": "", "permissions": list(PERMISSIONS), "valid_until": intent["valid_until"]},
        "resulted_in_city_enablement": False,
    }
    return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))


# --------------------------------------------------------------------------- #
# End (revoke or freeze)
# --------------------------------------------------------------------------- #

def validate_end(request: Any) -> Optional[dict]:
    """Canonical end intent. ``appointment_id`` is the Idempotency-Key of the appointment."""
    if not isinstance(request, dict) or set(request) != END_FIELDS:
        return None
    revision, attestation = request["decision_revision"], request["authority_attestation"]
    if not (isinstance(request["account_id"], str) and cg.ACCOUNT_ID.match(request["account_id"])
            and request["template"] == TEMPLATE and request["kind"] in END_KINDS
            and isinstance(request["appointment_id"], str) and cg.IDEMPOTENCY_KEY.match(request["appointment_id"])
            and cg._non_empty_id(request["decision_id"]) and type(revision) is int and revision >= 1
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384):
        return None
    return {key: request[key] for key in sorted(END_APPROVAL_FIELDS)}


def end_digest(request: Any) -> Optional[str]:
    intent = validate_end(request)
    return cg._digest(intent) if intent is not None else None


def apply_end_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Revoke or freeze the appointment an earlier operation wrote. Idempotent."""
    intent = validate_end(request)
    actor, error = rb._precheck(db, integration, idempotency_key, intent, actor_id)
    if error is not None:
        return error
    operation, replayed = rb._new_operation(db, actor, integration, idempotency_key, intent, cg._digest(intent))
    if operation is None:
        return replayed
    return rb._run_fenced(db, operation, intent, actor, integration, _end_fenced)


def _original(db, integration: str, intent: dict) -> Optional[dict]:
    """The appointment operation this end refers to, for the same account.

    Not only a confirmed one: a write that ended in reconciliation_required may
    have left the appointment recorded, and it must still be endable.
    """
    return db[cg.OPERATIONS].find_one({
        "integration": integration, "idempotency_key": intent["appointment_id"],
        "account_id": intent["account_id"], "status": {"$ne": "rejected"},
        "intent.template": TEMPLATE, "intent.kind": {"$exists": False},
    })


def _end_fenced(db, operation, intent, actor, integration, account_id, fence) -> cg.Outcome:
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return cg._outcome_for(cg._reject(db, operation, "denied"))
    original = _original(db, integration, intent)
    if original is None:
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))

    def write(acting):
        grant = _grant_of(db, original["_id"])
        if grant is None:
            return  # Nothing was written, or it is gone: ending is idempotent.
        who = {"id": str(acting._id), "username": getattr(acting, "username", None), "via": "hallinto"}
        reason = f"{END_KINDS[intent['kind']]}: {intent['decision_id']} v{intent['decision_revision']}"
        try:
            if intent["kind"] == "revoke":
                duty_shifts.revoke(db, grant_id=grant["_id"], reason=reason, actor=who)
            else:
                duty_shifts.set_frozen(db, grant_id=grant["_id"], frozen=True, reason=reason, actor=who)
        except DutyError as exc:
            raise cg.CityGrantConflict(f"end refused: {exc}") from exc

    operation, failed = rb._write(db, operation, intent, actor, integration, account_id, fence,
                                  "national_moderator_end_v1", write)
    if failed is not None:
        return failed

    grant = _grant_of(db, original["_id"])
    still_usable = bool(grant and not grant.get("revoked_at") and not grant.get("frozen_at"))
    if still_usable or not cg.fence_held(db, account_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    after = db.users.find_one({"_id": account_id}) or {}
    receipt = {**cg._receipt_identity(operation), "status": "confirmed",
               "checked_at": utcnow().strftime(cg.INSTANT),
               # Nothing here comes from the appointment any more: no role, no city, no rights.
               "effective": {"account_id": intent["account_id"], "role": "", "city": "", "cities": [],
                             "permissions": [], "global_admin": bool(after.get("global_admin"))}}
    return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))
