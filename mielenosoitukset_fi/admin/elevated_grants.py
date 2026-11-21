"""Superadmin and god appointments decided in Hallinto (roadmap phase F).

The governance model (hallintomalli, 15 November 2026) replaces the legacy
board votes, 48-hour veto and 75% rounds for these rights: Johtoryhmä decides
superadmin, and Johtoryhmä and OT decide god unanimously with at least two
different approvers. Hallinto checks those decisions; Flask records the
decision's identity as the grant's evidence, keeps every technical safeguard
(complete god enrollment and login, fresh proof, emergency revocation) and
re-checks owner state itself.

Like ``city_admin_v1`` this operation is not reachable from any route. The
internal route, gated on Hallinto's action-bound fresh proof (roadmap I02),
must keep it disabled by default.

Effects:

``superadmin_v1``
    An approved membership of the protected national team with
    ``grant_source = "hallinto_decision"`` and the decision as clearance
    evidence. ``clearance_record_approved`` honours that evidence only until
    ``valid_until``, so access ends without a scheduled job.

``god_v1``
    The same national evidence plus god appointment consent for an enrolled
    god candidate. Activation still needs the complete god security
    enrollment; until then the receipt stays pending. God login checks the
    clearance on every load, so it also ends at ``valid_until``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

from .city_grants import (
    ACCOUNT_ID, APPLY_LEASE, ERROR_STATUS, IDEMPOTENCY_KEY, INSTANT, MAX_VALIDITY, OPERATIONS, CityGrantConflict,
    Outcome, _actor_authorized, _digest, _error, account_fence, fence_held, parse_valid_until,
)

SUPERADMIN_V1 = "superadmin_v1"
GOD_V1 = "god_v1"
TEMPLATES = {SUPERADMIN_V1: "superadmin", GOD_V1: "god"}
SOURCE = "hallinto_decision"
REQUEST_FIELDS = {
    "account_id", "template", "expected_state", "decision_id", "decision_revision",
    "concurring_decision_id", "authority_attestation", "valid_until",
}


def _id(value) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128


def validate_elevated(request: Any) -> Optional[dict]:
    """Canonical intent, or None. God names both bodies' decisions."""
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
        return None
    template = request.get("template")
    required = REQUEST_FIELDS - ({"concurring_decision_id"} if template == SUPERADMIN_V1 else set())
    if required - set(request) or template not in TEMPLATES:
        return None
    revision, attestation = request["decision_revision"], request["authority_attestation"]
    concurring = request.get("concurring_decision_id")
    if not (isinstance(request["account_id"], str) and ACCOUNT_ID.match(request["account_id"])
            and _id(request["expected_state"]) and _id(request["decision_id"])
            and type(revision) is int and revision >= 1
            and (template == SUPERADMIN_V1 or (_id(concurring) and concurring != request["decision_id"]))
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384
            and parse_valid_until(request["valid_until"]) is not None):
        return None
    return {key: request[key] for key in sorted(REQUEST_FIELDS - {"authority_attestation"}) if key in request}


# --------------------------------------------------------------------------- #
# Owner state
# --------------------------------------------------------------------------- #
def _inputs(db, doc) -> dict:
    """Owner fields that decide eligibility, for the opaque expected_state."""
    from mielenosoitukset_fi.utils.system_teams import user_team_access

    clearance = db.board_clearances.find_one({"user_id": str(doc["_id"])}) or {}
    return {
        "account": str(doc["_id"]), "role": doc.get("role"), "global_admin": bool(doc.get("global_admin")),
        "governance_managed": bool(doc.get("governance_managed")),
        "active": doc.get("active", True), "banned": doc.get("banned", False),
        "confirmed": doc.get("confirmed", False), "god_candidate": bool(doc.get("god_candidate")),
        "teams": sorted(user_team_access(db, doc["_id"])),
        "clearance_revision": clearance.get("revision") or 0,
    }


def read_elevated_state(db, account_id: str) -> Optional[dict]:
    """Authoritative state for Hallinto; None when the account does not exist."""
    if not isinstance(account_id, str) or not ACCOUNT_ID.match(account_id):
        return None
    doc = db.users.find_one({"_id": ObjectId(account_id)})
    if not doc:
        return None
    inputs = _inputs(db, doc)
    return {"account_id": account_id, "expected_state": _digest(inputs), "effective_role": effective_role(db, doc),
            "eligible": {template: _eligible(inputs, template) for template in sorted(TEMPLATES)}}


def _eligible(inputs: dict, template: str) -> bool:
    from mielenosoitukset_fi.utils.system_teams import active_account

    if inputs["role"] == "god" or inputs["global_admin"] or "national" in inputs["teams"]:
        return False  # Already elevated: a new decision does not stack on old rights.
    if template == GOD_V1:
        # Only an enrolled-or-enrolling god candidate account; its activation
        # stays with the god security enrollment.
        return inputs["god_candidate"] and not inputs["banned"]
    return (not inputs["god_candidate"]
            and active_account({"active": inputs["active"], "banned": inputs["banned"], "confirmed": inputs["confirmed"]}))


def effective_role(db, doc) -> Optional[str]:
    """The national role this account holds now, as Hallinto confirms it."""
    from mielenosoitukset_fi.utils.system_teams import active_account, clearance_approved, user_team_access

    if not doc or not active_account(doc) or not clearance_approved(db, doc["_id"]):
        return None
    if "national" not in user_team_access(db, doc["_id"]):
        return None
    return "god" if doc.get("role") == "god" else "superadmin"


def hallinto_evidence_valid(evidence: dict, now=None) -> bool:
    """A Hallinto decision is clearance evidence only until its end."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get("valid_until"), datetime):
        return False
    return bool(evidence.get("source") == SOURCE and evidence.get("approved") is True
                and _id(evidence.get("decision_id")) and (now or utcnow()) < evidence["valid_until"])


# --------------------------------------------------------------------------- #
# Operation journal (shared with city_admin_v1, keyed by integration+actor+key)
# --------------------------------------------------------------------------- #
def _identity(operation: dict) -> dict:
    intent = operation["intent"]
    result = {"operation_id": str(operation["_id"]), "account_id": operation["account_id"],
              "template": intent["template"], "decision_id": intent["decision_id"],
              "decision_revision": intent["decision_revision"], "valid_until": intent["valid_until"]}
    if intent.get("concurring_decision_id"):
        result["concurring_decision_id"] = intent["concurring_decision_id"]
    return result


def _outcome(operation: dict) -> Outcome:
    status = operation["status"]
    if status == "confirmed":
        return Outcome(200, operation["receipt"])
    if status == "rejected":
        code = operation["receipt"]["error"]["code"]
        return Outcome(ERROR_STATUS[code], {"code": code})
    return Outcome(202, {**_identity(operation), "status": status})


def _move(db, operation: dict, frm: set, status: str, receipt: Optional[dict] = None, **extra) -> dict:
    now = utcnow()
    update = {"$set": {"status": status, "updated_at": now, **extra},
              "$push": {"transitions": {"status": status, "at": now}}}
    if receipt is not None:
        update["$set"]["receipt"] = receipt
    stored = db[OPERATIONS].find_one_and_update({"_id": operation["_id"], "status": {"$in": sorted(frm)}}, update,
                                                return_document=ReturnDocument.AFTER)
    if stored is None:
        return db[OPERATIONS].find_one({"_id": operation["_id"]})
    logger.info("%s operation=%s transition=%s integration=%s actor=%s account=%s decision=%s@%s",
                stored["intent"]["template"], stored["_id"], status, stored["integration"], stored["actor_id"],
                stored["account_id"], stored["intent"]["decision_id"], stored["intent"]["decision_revision"])
    return stored


def _reject(db, operation: dict, code: str) -> dict:
    return _move(db, operation, {"pending"}, "rejected", {**_identity(operation), "status": "rejected", "error": {"code": code}})


def apply_elevated_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> Outcome:
    """Grant superadmin or god rights that Hallinto's decisions approved.

    The caller must already have authenticated the integration and the human
    executor, and verified the action-bound proof and Hallinto's authority
    attestation for this exact intent. The executor never grants to their own
    account; owner state is re-checked here under the account fence.
    """
    if not isinstance(integration, str) or not integration:
        return _error("denied")
    if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY.match(idempotency_key):
        return _error("unsupported_scope")
    intent = validate_elevated(request)
    if intent is None:
        return _error("unsupported_scope")
    actor = _actor_authorized(db, actor_id)
    if actor is None or str(actor._id) == intent["account_id"]:
        return _error("denied")

    digest, now = _digest(intent), utcnow()
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest, "intent": intent,
        "account_id": intent["account_id"], "status": "pending", "phase": "allocated",
        "created_at": now, "updated_at": now, "transitions": [{"status": "pending", "at": now}],
    }
    try:
        db[OPERATIONS].insert_one(operation)
    except DuplicateKeyError:
        existing = db[OPERATIONS].find_one({"integration": integration, "actor_id": str(actor._id),
                                            "idempotency_key": idempotency_key})
        if existing is None:
            return _error("unavailable")
        if existing["intent_digest"] != digest:
            return _error("idempotency_conflict")
        return _settle(db, existing)

    account_id = ObjectId(intent["account_id"])
    try:
        with account_fence(db, account_id) as fence:
            return _apply_fenced(db, operation, intent, actor, account_id, fence)
    except CityGrantConflict:
        current = db[OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending" and current.get("phase") == "allocated":
            return _outcome(_reject(db, current, "stale_state"))
        return _outcome(current) if current else _error("unavailable")


def _apply_fenced(db, operation, intent, actor, account_id, fence) -> Outcome:
    from mielenosoitukset_fi.utils.system_teams import team_ids

    target = db.users.find_one({"_id": account_id})
    if target is None:
        return _outcome(_reject(db, operation, "denied"))
    inputs = _inputs(db, target)
    if _digest(inputs) != intent["expected_state"]:
        return _outcome(_reject(db, operation, "stale_state"))
    expires_at = parse_valid_until(intent["valid_until"])
    now = utcnow()
    if (not _eligible(inputs, intent["template"]) or not (now < expires_at <= now + MAX_VALIDITY)
            or "national" not in team_ids()):
        return _outcome(_reject(db, operation, "unsupported_scope"))
    if not fence_held(db, account_id, fence):
        return _outcome(_reject(db, operation, "stale_state"))
    stored = db[OPERATIONS].find_one_and_update(
        {"_id": operation["_id"], "status": "pending", "phase": "allocated"},
        {"$set": {"phase": "applying", "updated_at": utcnow()}}, return_document=ReturnDocument.AFTER)
    if stored is None:
        return _outcome(db[OPERATIONS].find_one({"_id": operation["_id"]}))
    operation = stored
    try:
        _write_evidence(db, operation, intent, actor, account_id, expires_at, inputs["clearance_revision"])
    except (CityGrantConflict, PyMongoError):
        logger.exception("%s operation=%s write failed", intent["template"], operation["_id"])
        try:
            return _outcome(_move(db, operation, {"pending"}, "reconciliation_required"))
        except PyMongoError:
            return _error("unavailable")
    if not fence_held(db, account_id, fence):
        return _outcome(_move(db, operation, {"pending"}, "reconciliation_required"))
    return _settle(db, operation)


def _write_evidence(db, operation, intent, actor, account_id, expires_at, revision) -> None:
    """Record the decision as clearance evidence and enroll the national team."""
    from mielenosoitukset_fi.utils.team_governance import _enroll

    candidate = str(account_id)
    now = utcnow()
    evidence = {
        "id": str(operation["_id"]), "source": SOURCE, "approved": True, "required": 0,
        "template": intent["template"], "decision_id": intent["decision_id"],
        "decision_revision": intent["decision_revision"], "valid_until": expires_at,
        "executor_id": str(actor._id), "updated_at": now,
    }
    if intent.get("concurring_decision_id"):
        evidence["concurring_decision_id"] = intent["concurring_decision_id"]
    # Legacy vote tallies never count as evidence beside the decision.
    update = {"team_requests.national": evidence, "approved": True, "approvals": {},
              "revision": revision + 1, "timestamp": now,
              "granted_by": actor.username, "granted_by_id": str(actor._id)}
    if intent["template"] == GOD_V1:
        update["team_requests.god"] = {**evidence, "method": SOURCE, "phase": "decided", "window_ends_at": None}
    try:
        db.board_clearances.update_one({"user_id": candidate},
                                       {"$setOnInsert": {"approved": False, "revision": 0, "created_at": now}},
                                       upsert=True)
    except DuplicateKeyError:
        pass
    # Compare-and-set on the revision read before the eligibility check.
    current = {"$in": [0, None]} if revision == 0 else revision
    changed = db.board_clearances.update_one({"user_id": candidate, "revision": current}, {"$set": update})
    if not changed.matched_count:
        raise CityGrantConflict("clearance changed concurrently")
    db.users.update_one({"_id": account_id}, {"$set": {"governance_managed": True}})
    _enroll(db, account_id, "national", evidence)
    db.board_audit_logs.insert_one({
        "user_id": candidate, "action": "Hallinnon päätös: " + TEMPLATES[intent["template"]],
        "granted_by": actor.username, "granted_by_id": str(actor._id), "team": "national",
        "request_id": evidence["id"], "source": SOURCE, "decision_id": intent["decision_id"],
        "decision_revision": intent["decision_revision"],
        "concurring_decision_id": intent.get("concurring_decision_id"), "valid_until": expires_at,
        "approved": True, "timestamp": now,
    })


def _settle(db, operation: dict) -> Outcome:
    """Report the operation, confirming only the exact role now in effect."""
    if operation["status"] != "pending":
        return _outcome(operation)
    intent = operation["intent"]
    if operation.get("phase") == "allocated":
        if operation["updated_at"] <= utcnow() - APPLY_LEASE:
            operation = _move(db, operation, {"pending"}, "reconciliation_required")
        return _outcome(operation)
    doc = db.users.find_one({"_id": ObjectId(operation["account_id"])})
    role = effective_role(db, doc)
    wanted = TEMPLATES[intent["template"]]
    if role == wanted:
        receipt = {**_identity(operation), "status": "confirmed", "checked_at": utcnow().strftime(INSTANT),
                   "effective_role": role}
        return _outcome(_move(db, operation, {"pending"}, "confirmed", receipt))
    if intent["template"] == GOD_V1 and doc and doc.get("god_candidate"):
        # Awaits the god security enrollment, which activates the account.
        return _outcome(operation)
    return _outcome(_move(db, operation, {"pending"}, "reconciliation_required"))


def read_elevated_receipt(db, *, integration: str, actor_id, idempotency_key: str) -> Outcome:
    """Receipt lookup; a pending god appointment confirms once activated."""
    actor = _actor_authorized(db, actor_id)
    if actor is None:
        return _error("denied")
    if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY.match(idempotency_key):
        return _error("unknown_operation")
    operation = db[OPERATIONS].find_one({"integration": integration, "actor_id": str(actor._id),
                                         "idempotency_key": idempotency_key,
                                         "intent.template": {"$in": sorted(TEMPLATES)}})
    if operation is None:
        return _error("unknown_operation")
    if operation["status"] == "rejected":
        return Outcome(200, operation["receipt"])
    return _settle(db, operation)
