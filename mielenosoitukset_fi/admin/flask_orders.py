"""Orders decided in Hallinto and carried out by Flask (H12, H17).

Three orders share one route, journal and trust model with the city-appointment
operations in ``city_grants``:

* ``board_roster`` (H12): the board team becomes exactly the roster Hallinto
  sends, the active members of its Johtoryhmä with a linked account.
* ``role_removal`` (H17): an account permanently loses one Flask role.
* ``site_lockdown``: close or reopen the public site (hätäsulku). It writes
  the same lock and security case the Flask page does, so that page can always
  reopen it, and it works only while ``HALLINTO_LOCKDOWN_ORDERS_ENABLED`` is on.
  Flask's own page needs no Hallinto and stays the fallback.

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
KIND_SITE_LOCKDOWN = "site_lockdown"
KIND_ACCESS_REVIEW = "access_review"
KIND_ACCOUNT_LOCK = "account_lock"
LOCKDOWN_ACTIONS = {"close", "reopen"}
LOCKDOWN_FENCE = "site-lockdown"
COMMON_FIELDS = {"order_id", "kind", "decision_id", "decision_revision", "authority_attestation"}
KIND_FIELDS = {KIND_ROLE_REMOVAL: {"account_id", "role"}, KIND_BOARD_ROSTER: {"roster"},
               KIND_SITE_LOCKDOWN: {"action"}, KIND_ACCESS_REVIEW: {"roster"},
               KIND_ACCOUNT_LOCK: {"account_id"}}
ROLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Roles an order may take away. Everything else is refused, not guessed at.
REMOVABLE_ROLES = {"city_admin", "admin", "global_admin", "superuser", "god"}
MAX_ROSTER = 200
BOOTSTRAP_ADDITIONS = 5  # An order may add this many even to a small or empty team.
# Higher rank needs a higher actor: only god and superuser accounts can take those roles.
ROLE_RANK = {"city_admin": 1, "admin": 2, "global_admin": 3, "superuser": 4, "god": 5}
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
    elif kind == KIND_SITE_LOCKDOWN:
        if request["action"] not in LOCKDOWN_ACTIONS:
            return None
    elif kind == KIND_ACCOUNT_LOCK:
        if not (isinstance(request["account_id"], str) and cg.ACCOUNT_ID.match(request["account_id"])):
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
    target = (LOCKDOWN_FENCE if intent["kind"] == KIND_SITE_LOCKDOWN
              else intent.get("account_id") or BOARD_FENCE)
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


def read_order_status(db, *, integration: str, order_id: str) -> cg.Outcome:
    """What Flask did with one order, for Hallinto's automatic check. Writes nothing.

    It covers every order Flask journals under an idempotency key: the roster,
    role removal, lockdown and access review orders, outbound invitations,
    access recoveries and organization rights. Unlike the receipt route this
    needs no human proof: it is read-only, looks the order up by its id alone
    and shows only the order's own state, so
    Hallinto can settle an order whose answer it lost without asking the owner
    to approve a resend. A lease that ran out is reported as it would be on a
    replay, but is not recorded here.
    """
    if not isinstance(order_id, str) or not cg.IDEMPOTENCY_KEY.match(order_id):
        return cg._error("unknown_operation")
    operation = db[cg.OPERATIONS].find_one(
        {"integration": integration, "idempotency_key": order_id}, sort=[("created_at", -1)])
    if operation is None:
        return cg._error("unknown_operation")
    if operation["status"] == "rejected":
        return cg.Outcome(200, operation["receipt"])
    if operation["status"] == "pending" and operation["updated_at"] <= utcnow() - cg.APPLY_LEASE:
        operation = {**operation, "status": "reconciliation_required"}
    return cg._outcome_for(operation)


def _board_organization(db):
    from mielenosoitukset_fi.utils.system_teams import team_ids

    organization = team_ids().get("board")
    if organization and db.organizations.find_one({"_id": organization}, {"_id": 1}):
        return organization
    return None


def _order_fenced(db, operation, intent, actor, integration, target, fence) -> cg.Outcome:
    if intent["kind"] == KIND_ROLE_REMOVAL:
        write = _plan_role_removal(db, operation, intent, actor)
    elif intent["kind"] == KIND_SITE_LOCKDOWN:
        write = _plan_site_lockdown(db, operation, intent, actor)
    elif intent["kind"] == KIND_ACCESS_REVIEW:
        write = _plan_access_review(db, operation, intent, actor)
    elif intent["kind"] == KIND_ACCOUNT_LOCK:
        write = _plan_account_lock(db, operation, intent, actor)
    else:
        write = _plan_board_roster(db, operation, intent, actor)
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


def _plan_board_roster(db, operation, intent, actor):
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
    # Safeguards that stay in Flask: Olivia is never removed by an order, one
    # order never removes more than half of the current team, never adds more
    # than the team has (at least BOOTSTRAP_ADDITIONS), and nobody adds themselves.
    if olivia_user_id() in removed or (current and len(removed) * 2 > len(current)):
        return "unsupported_scope"
    if len(added) > max(len(current), BOOTSTRAP_ADDITIONS) or str(actor._id) in added:
        return "unsupported_scope"
    operation_id = str(operation["_id"])

    def write(acting):
        for account in added:
            uid = ObjectId(account)
            row = {"user_id": {"$in": [uid, account]}, "organization_id": {"$in": [organization, str(organization)]}}
            fields = {"governance_approved": True, "grant_source": "hallinto_decision",
                      "approval_request_id": operation_id, "role": "member", "permissions": []}
            if db.memberships.count_documents(row, limit=1):
                # Re-approval resets whatever role or permissions the old row held.
                db.memberships.update_many(row, {"$set": fields})
            else:
                db.memberships.insert_one({**fields, "user_id": uid, "organization_id": organization,
                                           "created_at": utcnow()})
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


def _plan_access_review(db, operation, intent, actor):
    """Return a rejection code, or (write, verify) for confirming reviewed admin accounts.

    Only stamped accounts can be confirmed, nobody confirms their own rights,
    and an account with a super role needs an actor of at least that rank.
    """
    from mielenosoitukset_fi.utils import access_review

    roster = intent["roster"]
    if str(actor._id) in roster:
        return "denied"
    docs = list(db.users.find({"_id": {"$in": [ObjectId(a) for a in roster]}}))
    # An order that already confirmed these accounts replays; otherwise they must be waiting for a decision.
    if not access_review.confirmable(db, roster) and access_review.confirmed_accounts(
            db, roster, intent["order_id"]) != roster:
        return "unsupported_scope"
    if any(ROLE_RANK.get(d.get("role"), 0) > _actor_rank(actor) for d in docs):
        return "denied"

    def write(acting):
        access_review.confirm(db, roster, decision_id=intent["decision_id"],
                              decision_revision=intent["decision_revision"], order_id=intent["order_id"],
                              actor_id=acting._id)

    def verify():
        done = access_review.confirmed_accounts(db, roster, intent["order_id"])
        return done == roster, {"roster": done}

    return write, verify


def _plan_site_lockdown(db, operation, intent, actor):
    """Return a rejection code, or (write, verify) for closing or reopening the site.

    Closing records a security case in status ``locked`` and the shared lock
    that case owns, exactly as a decision on the Flask page does, so the Flask
    page can reopen it. Reopening ends the case that holds the lock and clears
    both lock names. A change that would not change anything is refused as
    stale: the decision was made for a state the site no longer has.
    """
    from mielenosoitukset_fi.utils.site_lock import LOCK_NAMES, site_locked

    close = intent["action"] == "close"
    if site_locked(db) == close:
        return "stale_state"
    operation_id = str(operation["_id"])
    reason = f"Hallinnon päätös {intent['decision_id']}, versio {intent['decision_revision']}"

    def event(action, now):
        return {"id": operation_id, "action": action, "reason": reason, "actor_id": str(actor._id),
                "actor_name": actor.username, "at": now, "approved": True, "method": "hallinto_order",
                "votes": {}, "required": 1, "hearing": "", "roster": {}}

    def write(acting):
        now = utcnow()
        if close:
            case_id = f"hallinto-{operation_id}"
            db.security_cases.update_one(
                {"_id": case_id},
                {"$setOnInsert": {"revision": 1, "kind": "lockdown", "target_id": None, "target_name": "Julkinen sivusto",
                                  "affected_person": None, "reason": reason, "opened_by": str(actor._id),
                                  "created_at": now, "status": "locked", "events": [event("lockdown", now)],
                                  "credential_before": None, "updated_at": now, "source": "hallinto_order"}},
                upsert=True)
            db.panic.update_one(
                {"_id": "security-lockdown"},
                {"$set": {"name": "security", "panic": True, "case_id": case_id, "event_id": operation_id,
                          "updated_at": now}, "$addToSet": {"applied_events": operation_id}},
                upsert=True)
            return
        locked = db.panic.find_one({"_id": "security-lockdown"}) or {}
        db.panic.update_many({"name": {"$in": LOCK_NAMES}}, {"$set": {"panic": False, "updated_at": now}})
        if locked:
            db.panic.update_one({"_id": "security-lockdown"}, {"$set": {"event_id": operation_id},
                                                               "$addToSet": {"applied_events": operation_id}})
        if locked.get("case_id"):
            db.security_cases.update_one(
                {"_id": locked["case_id"], "kind": "lockdown", "status": "locked"},
                {"$set": {"status": "restored", "updated_at": now, "effect_pending": None, "proposal": {}},
                 "$inc": {"revision": 1}, "$push": {"events": event("reopen", now)}})

    def verify():
        closed = site_locked(db)
        return closed == close, {"lockdown": "closed" if closed else "open"}

    return write, verify


def _plan_account_lock(db, operation, intent, actor):
    """Return a rejection code, or (write, verify) for suspending one account.

    The emergency lock of one account decided in Hallinto. It writes the same
    security case (kind ``account``, status ``suspended``) and the same
    suspension the Flask page's "Keskeytä" does, so the board can review and
    restore it there. Sessions, API tokens and magic links of the account end
    with it. Only a suspension is ordered: restoring needs the hearing and the
    renewed credentials that page already demands. An account that is already
    suspended is a stale decision. The protected accounts (god, the board team,
    Olivia) are refused: they have their own Flask safeguards.
    """
    from mielenosoitukset_fi.utils.security_cases import (
        _fingerprint, person_id, protected_account, reconcile_case, suspended,
    )
    from mielenosoitukset_fi.utils.system_teams import olivia_user_id

    account_id = ObjectId(intent["account_id"])
    target = db.users.find_one({"_id": account_id})
    if target is None:
        return "denied"  # Conceal existence.
    if intent["account_id"] == olivia_user_id() or protected_account(db, target) and not suspended(target):
        return "unsupported_scope"
    if suspended(target):
        return "stale_state"
    operation_id = str(operation["_id"])
    case_id = f"hallinto-{operation_id}"
    reason = f"Hallinnon päätös {intent['decision_id']}, versio {intent['decision_revision']}"

    def write(acting):
        now = utcnow()
        event = {"id": operation_id, "action": "suspend", "reason": reason, "actor_id": str(actor._id),
                 "actor_name": actor.username, "at": now, "approved": True, "method": "hallinto_order",
                 "votes": {}, "required": 1, "hearing": "", "roster": {}}
        db.security_cases.update_one(
            {"_id": case_id},
            {"$setOnInsert": {"revision": 1, "kind": "account", "target_id": str(account_id),
                              "target_name": target.get("username"), "affected_person": person_id(target),
                              "reason": reason, "opened_by": str(actor._id), "created_at": now,
                              "status": "pending", "events": [event], "effect_pending": event,
                              "credential_before": _fingerprint(db, target), "updated_at": now,
                              "source": "hallinto_order"}},
            upsert=True)
        # The shared reconciler applies the suspension and ends sessions, so a
        # retry after an interruption cannot suspend twice or skip the revoke.
        reconcile_case(db, db.security_cases.find_one({"_id": case_id}))

    def verify():
        after = db.users.find_one({"_id": account_id}) or {}
        locked = suspended(after)
        return locked, {"account_locked": locked}

    return write, verify


def held_removable_roles(db, doc, max_rank: int = 5) -> list[str]:
    """The roles of an account that a role removal could take, strongest first.

    Hallinto shows these in a list when a role removal is started, so the choice
    is never typed. It follows the same test as the removal itself, so a role
    listed here is one the removal finds. Roles above ``max_rank`` (the rank of
    the person asking) and the protected owner account are left out.
    """
    from mielenosoitukset_fi.users.models import User
    from mielenosoitukset_fi.utils.system_teams import olivia_user_id

    if str(doc["_id"]) == olivia_user_id():
        return []
    held = set()
    role = doc.get("role")
    if role in REMOVABLE_ROLES:
        held.add(role)
    if (doc.get("global_admin") is True or "national" in User.from_db(doc).system_team_access) and role not in {"superuser", "god"}:
        held.add("global_admin")
    if cg._active_grants(db, doc["_id"]):
        held.add("city_admin")
    return sorted((r for r in held if ROLE_RANK[r] <= max_rank), key=lambda r: -ROLE_RANK[r])


def _actor_rank(actor) -> int:
    """An authorized actor manages grants, which makes them at least global admin."""
    return {"god": 5, "superuser": 4}.get(getattr(actor, "role", None), 3)


def _plan_role_removal(db, operation, intent, actor):
    """Return a rejection code, or (write, verify) for a permanent role removal.

    Only the named role changes. The check and the receipt use the loaded
    ``User`` model, so access that comes from a team rather than a stored field
    is seen too.
    """
    from mielenosoitukset_fi.users.models import User
    from mielenosoitukset_fi.utils.system_teams import olivia_user_id, team_ids

    role = intent["role"]
    account_id = ObjectId(intent["account_id"])
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return "denied"  # Conceal existence.
    if role not in REMOVABLE_ROLES or intent["account_id"] == olivia_user_id():
        return "unsupported_scope"
    if ROLE_RANK[role] > _actor_rank(actor):
        return "denied"

    def holds(document) -> bool:
        if role == "global_admin":
            # The stored flag, the role, or the national team. A super role is a different role.
            return (document.get("role") == "global_admin" or document.get("global_admin") is True
                    or "national" in User.from_db(document).system_team_access)
        return document.get("role") == role

    def write(acting):
        document = db.users.find_one({"_id": account_id})
        if role == "city_admin":
            cg.sync_city_scope_grant(db, account_id, [], [], acting, fenced=True,
                                     end_marker={"revoked_reason": "hallinto_role_removed"})
            return
        if not document or not holds(document):
            return  # Already gone: removal is idempotent.
        from mielenosoitukset_fi.utils.security_cases import _revoke

        updates = {"auth_epoch": (document.get("auth_epoch") or 0) + 1}
        new_role = "user" if document.get("role") == role else document.get("role")
        updates["role"] = new_role
        if new_role not in {"superuser", "god"}:
            # Later grants must come through governance. Not for a remaining
            # super role: governance-managed accounts get no power from a role.
            updates["governance_managed"] = True
        if role == "global_admin":
            updates.update(global_admin=False, global_permissions=[])
            national = team_ids().get("national")
            if national:
                db.memberships.update_many(
                    {"user_id": {"$in": [account_id, str(account_id)]},
                     "organization_id": {"$in": [national, str(national)]}},
                    {"$set": {"governance_approved": False, "removed_by_operation": str(operation["_id"])}})
        cg.commit_profile_change(db, document, updates, acting, "hallinto_role_removed")
        _revoke(db, document, operation["_id"])

    def verify():
        after = db.users.find_one({"_id": account_id}) or {}
        user = User.from_db(after) if after else None
        gone = not holds(after)
        if role == "city_admin":
            gone = gone and not cg._active_grants(db, account_id)
        effective = {"role": after.get("role"), "global_admin": bool(user and user.global_admin),
                     "full_permissions": bool(user and user.has_full_permissions()),
                     "system_team_access": sorted(user.system_team_access) if user else []}
        return gone, effective

    return write, verify
