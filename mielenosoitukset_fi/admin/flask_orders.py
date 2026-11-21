"""Orders decided in Hallinto and carried out by Flask (H12, H17).

Two orders share one route, journal and trust model with the city-appointment
operations in ``city_grants``:

* ``board_roster`` (H12): the board team becomes exactly the roster Hallinto
  sends, the active members of its Johtoryhmä with a linked account.
* ``role_removal`` (H17): an account permanently loses one Flask role.

The caller has already verified the mTLS workload identity, the action-bound
proof and Hallinto's attestation for the exact intent. Flask re-checks the
actor and every safeguard here, and never changes the board team by itself.
The receipt reports what Flask now has; Hallinto confirms by comparing it.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg

KIND_ROLE_REMOVAL = "role_removal"
KIND_BOARD_ROSTER = "board_roster"
COMMON_FIELDS = {"order_id", "kind", "decision_id", "decision_revision", "authority_attestation"}
KIND_FIELDS = {KIND_ROLE_REMOVAL: {"account_id", "role"}, KIND_BOARD_ROSTER: {"roster"}}
ROLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Roles an order may take away. Everything else is refused, not guessed at.
REMOVABLE_ROLES = {"city_admin", "admin", "global_admin", "superuser", "god"}
MAX_ROSTER = 200
BOARD_FENCE = "board-team"


def validate_order(request: Any) -> Optional[dict]:
    """Canonical order intent, or None for an unsupported shape."""
    if not isinstance(request, dict) or request.get("kind") not in KIND_FIELDS:
        return None
    kind = request["kind"]
    if set(request) != COMMON_FIELDS | KIND_FIELDS[kind]:
        return None
    revision = request["decision_revision"]
    attestation = request["authority_attestation"]
    if not (isinstance(request["order_id"], str) and cg.IDEMPOTENCY_KEY.match(request["order_id"])
            and cg._non_empty_id(request["decision_id"])
            and type(revision) is int and revision >= 1
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384):
        return None
    if kind == KIND_ROLE_REMOVAL:
        if not (isinstance(request["account_id"], str) and cg.ACCOUNT_ID.match(request["account_id"])
                and isinstance(request["role"], str) and ROLE.match(request["role"])):
            return None
    else:
        roster = request["roster"]
        if not (isinstance(roster, list) and 1 <= len(roster) <= MAX_ROSTER
                and all(isinstance(a, str) and cg.ACCOUNT_ID.match(a) for a in roster)
                and roster == sorted(set(roster))):
            return None
    return {key: request[key] for key in sorted((COMMON_FIELDS | KIND_FIELDS[kind]) - {"authority_attestation"})}


def order_digest(request: Any) -> Optional[str]:
    intent = validate_order(request)
    return cg._digest(intent) if intent is not None else None


def apply_order(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Carry out one order, idempotently under its own key (the order id)."""
    if not isinstance(integration, str) or not integration:
        return cg._error("denied")
    if not isinstance(idempotency_key, str) or not cg.IDEMPOTENCY_KEY.match(idempotency_key):
        return cg._error("unsupported_scope")
    intent = validate_order(request)
    if intent is None or intent["order_id"] != idempotency_key:
        return cg._error("unsupported_scope")
    actor = cg._actor_authorized(db, actor_id)
    if actor is None or str(actor._id) == intent.get("account_id"):
        return cg._error("denied")

    digest = cg._digest(intent)
    now = utcnow()
    target = intent.get("account_id") or BOARD_FENCE
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest, "intent": intent,
        "account_id": target, "status": "pending", "phase": "allocated",
        "created_at": now, "updated_at": now, "transitions": [{"status": "pending", "at": now}],
    }
    key = {"integration": integration, "actor_id": str(actor._id), "idempotency_key": idempotency_key}
    try:
        db[cg.OPERATIONS].insert_one(operation)
    except DuplicateKeyError:
        existing = db[cg.OPERATIONS].find_one(key)
        if existing is None:
            return cg._error("unavailable")
        return cg._replay(db, existing, digest)
    cg._log(operation, "pending")

    try:
        with cg.account_fence(db, target) as fence:
            return _order_fenced(db, operation, intent, actor, integration, target, fence)
    except cg.CityGrantConflict:
        current = db[cg.OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending" and current.get("phase") == "allocated":
            return cg._outcome_for(cg._reject(db, current, "stale_state"))
        return cg._outcome_for(current) if current else cg._error("unavailable")


def _board_organization(db):
    from mielenosoitukset_fi.utils.system_teams import team_ids

    organization = team_ids().get("board")
    if organization and db.organizations.find_one({"_id": organization}, {"_id": 1}):
        return organization
    return None


def _order_fenced(db, operation, intent, actor, integration, target, fence) -> cg.Outcome:
    if intent["kind"] == KIND_ROLE_REMOVAL:
        write = _plan_role_removal(db, operation, intent, actor)
    else:
        write = _plan_board_roster(db, operation, intent)
    if isinstance(write, str):
        return cg._outcome_for(cg._reject(db, operation, write))
    apply_write, verify = write

    def applying():
        nonlocal operation
        if not cg.fence_held(db, target, fence):
            raise cg.CityGrantConflict("fence expired before the write")
        stored = db[cg.OPERATIONS].find_one_and_update(
            {"_id": operation["_id"], "status": "pending", "phase": "allocated"},
            {"$set": {"phase": "applying", "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        if stored is None:
            raise cg.CityGrantConflict("operation was moved by another process")
        operation = stored

    from mielenosoitukset_fi.utils.audit_trail import audit_scope

    try:
        with audit_scope("integration", integration, actor={"kind": "user", "user_id": str(actor._id)},
                         operation_id=str(operation["_id"])):
            applying()
            apply_write(actor)
    except cg.CityGrantConflict:
        if operation.get("phase") != "applying":
            return cg._outcome_for(cg._reject(db, operation, "stale_state"))
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    except PyMongoError:
        logger.exception("flask order operation=%s write failed", operation["_id"])
        if operation.get("phase") != "applying":
            return cg._outcome_for(cg._reject(db, operation, "stale_state"))
        try:
            return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
        except PyMongoError:
            return cg._error("unavailable")

    ok, effective = verify()
    if not ok or not cg.fence_held(db, target, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {**cg._receipt_identity(operation), "status": "confirmed",
               "checked_at": utcnow().strftime(cg.INSTANT), "effective": effective}
    return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))


def _plan_board_roster(db, operation, intent):
    """Return a rejection code, or (write, verify) for a board roster."""
    from mielenosoitukset_fi.utils.system_teams import active_account, board_ids, olivia_user_id

    organization = _board_organization(db)
    if organization is None:
        return "unsupported_scope"
    roster = intent["roster"]
    for account in roster:
        if not active_account(db.users.find_one({"_id": ObjectId(account)})):
            return "unsupported_scope"
    current = set(board_ids(db))
    added = sorted(set(roster) - current)
    removed = sorted(current - set(roster))
    # Safeguards that stay in Flask: Olivia is never removed by an order, and
    # one order never removes more than half of the current team.
    if olivia_user_id() in removed or (current and len(removed) * 2 > len(current)):
        return "unsupported_scope"
    operation_id = str(operation["_id"])

    def write(actor):
        for account in added:
            db.memberships.update_one(
                {"user_id": ObjectId(account), "organization_id": organization},
                {"$set": {"governance_approved": True, "grant_source": "hallinto_decision",
                          "approval_request_id": operation_id},
                 "$setOnInsert": {"role": "member", "permissions": [], "created_at": utcnow()}},
                upsert=True,
            )
        for account in removed:
            uid = ObjectId(account)
            db.memberships.update_many(
                {"user_id": {"$in": [uid, account]}, "organization_id": {"$in": [organization, str(organization)]}},
                {"$set": {"governance_approved": False, "removed_by_operation": operation_id}},
            )

    def verify():
        team = sorted(board_ids(db))
        return team == roster, {"roster": team}

    return write, verify


def _plan_role_removal(db, operation, intent, actor):
    """Return a rejection code, or (write, verify) for a permanent role removal."""
    from mielenosoitukset_fi.utils.system_teams import SUPER_ROLES, olivia_user_id, team_ids

    role = intent["role"]
    account_id = ObjectId(intent["account_id"])
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return "denied"  # Conceal existence.
    if role not in REMOVABLE_ROLES or intent["account_id"] == olivia_user_id():
        return "unsupported_scope"

    def holds(document) -> bool:
        return document.get("role") == role or (role == "global_admin" and document.get("global_admin") is True)

    def write(acting):
        document = db.users.find_one({"_id": account_id})
        if role == "city_admin":
            cg.sync_city_scope_grant(db, account_id, [], [], acting, fenced=True,
                                     end_marker={"revoked_reason": "hallinto_role_removed"})
            return
        if not document or not holds(document):
            return  # Already gone: removal is idempotent.
        from mielenosoitukset_fi.utils.security_cases import _revoke

        cg.commit_profile_change(
            db, document,
            {"role": "user", "global_admin": False, "global_permissions": [], "governance_managed": True},
            acting, "hallinto_role_removed")
        db.users.update_one({"_id": account_id}, {"$inc": {"auth_epoch": 1}})
        _revoke(db, document, operation["_id"])
        if role in SUPER_ROLES or role == "admin":
            teams = [*team_ids().values(), *(str(v) for v in team_ids().values())]
            db.memberships.update_many(
                {"user_id": {"$in": [account_id, str(account_id)]}, "organization_id": {"$in": teams}},
                {"$set": {"governance_approved": False, "removed_by_operation": str(operation["_id"])}})

    def verify():
        after = db.users.find_one({"_id": account_id}) or {}
        gone = not holds(after)
        if role == "city_admin":
            gone = gone and not cg._active_grants(db, account_id)
        return gone, {"role": after.get("role"), "global_admin": bool(after.get("global_admin"))}

    return write, verify
