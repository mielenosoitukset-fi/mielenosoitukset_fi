"""Shared city-scope grant writer (C02).

One implementation of the city-admin grant mutation, used by the existing
admin user views and by the additive ``city_admin_v1`` appointment operation
for Hallinto. Every function takes the acting account explicitly instead of
reading ``flask_login.current_user`` so that a non-request caller attributes
its writes to the authenticated actor, never to an ambient session.

The appointment operation is not reachable from any route. A future internal
route (gated on Hallinto's action-bound fresh proof, roadmap I02) must keep
it disabled by default. See ``docs/hallinto-city-grant.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.utils.scope_grants import live_grant_filter
from mielenosoitukset_fi.utils.cities import CITY_KEY_TO_NAME, normalize_city_key
from mielenosoitukset_fi.utils.city_settings import enabled_city_keys, upsert_city_setting
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

from .user_history import (
    FIELDS, PENDING_HISTORY, commit_history_change, flush_user_history, history_event, user_snapshot,
)


CITY_ADMIN_PERMISSIONS = [
    "LIST_DEMOS",
    "VIEW_DEMO",
    "EDIT_DEMO",
    "ACCEPT_DEMO",
    "CREATE_DEMO",
    "GENERATE_EDIT_LINK",
]
CITY_ADMIN_AVAILABLE_PERMISSIONS = CITY_ADMIN_PERMISSIONS + [
    "LIST_CASES", "VIEW_CASE", "EDIT_CASE",
]
# Maintainer decision A3, 20 November 2026: exactly these nine, for one city.
CITY_ADMIN_V1_TEMPLATE = "city_admin_v1"
CITY_ADMIN_V1_PERMISSIONS = sorted(CITY_ADMIN_AVAILABLE_PERMISSIONS)

ELEVATED_ROLES = {"admin", "global_admin", "god", "superuser", "city_admin"}


class CityGrantConflict(Exception):
    """A guarded write lost to a concurrent change or could not be audited."""


# --------------------------------------------------------------------------- #
# Shared writer
# --------------------------------------------------------------------------- #
def history_actor(actor) -> dict:
    """Capture attribution now, never from a later projection request."""
    return {"id": str(actor._id), "username": actor.username, "role": actor.role}


def can_manage_scope_grants(actor) -> bool:
    return bool(getattr(actor, "global_admin", False)) or getattr(actor, "role", None) in {
        "global_admin",
        "god",
    }


def _active_city_grant_query(user_id) -> dict:
    return {
        "user_id": {"$in": [user_id, str(user_id)]},
        "scope_type": "city",
        **live_grant_filter(),
    }


def _profile_snapshot(document):
    """User fields only; scope grants have their own atomic mutation events."""
    return {key: value for key, value in user_snapshot(document, {}).items()
            if not key.startswith("city_scope_")}


def commit_profile_change(db, document, updates, actor, event="user_details_changed", *,
                          always_record=False, flush=flush_user_history):
    """CAS profile state and its outbox together, then try audit projection.

    Raises CityGrantConflict when the guarded write loses or cannot be audited.
    """
    user_id = document["_id"]
    flush(db, user_id)
    entry = history_event(user_id, _profile_snapshot(document),
                          _profile_snapshot({**document, **updates}),
                          history_actor(actor), event, always_record=always_record)
    guard_fields = (set(FIELDS) - {"city_scope_keys", "city_scope_permissions"}) | {
        "global_admin", "username_canonical", "email_canonical",
    } | set(updates)
    result = commit_history_change(db.users, document, updates, entry, guard_fields)
    if result is None:
        raise CityGrantConflict("profile changed concurrently or audit outbox is full")
    flush(db, user_id)
    return result


def city_scope_grant_for_user(db, user_id: ObjectId) -> dict:
    grant = db.admin_scope_grants.find_one(_active_city_grant_query(user_id))
    if not grant:
        return {"scope_keys": [], "permissions": []}

    scope_keys = grant.get("scope_keys")
    if scope_keys is None and grant.get("scope_key"):
        scope_keys = [grant.get("scope_key")]
    grant["scope_keys"] = [normalize_city_key(key) for key in scope_keys or [] if normalize_city_key(key)]
    grant["permissions"] = [
        permission for permission in grant.get("permissions", []) if permission in CITY_ADMIN_AVAILABLE_PERMISSIONS
    ]
    return grant


def auto_activate_granted_cities(db, city_keys: list[str], actor, *, allow: bool = True) -> list[str]:
    """Activate city_settings for granted cities that are currently inactive.

    Granting a city admin for an inactive city would otherwise hand over
    moderation of a city that is invisible on the site, so the city is
    enabled automatically. With ``allow=False`` an inactive city raises
    CityGrantConflict before anything is written. Returns the enabled keys.
    """
    active_keys = enabled_city_keys(db)
    inactive = [city_key for city_key in city_keys if city_key not in active_keys]
    if inactive and not allow:
        raise CityGrantConflict("granting would enable a city the caller did not allow")
    for city_key in inactive:
        upsert_city_setting(db, city_key, True, actor_id=str(actor._id))
    return inactive


@dataclass
class SyncResult:
    city_keys: list
    permissions: list
    enabled_cities: list


FENCES = "city_grant_fences"
# A fence left by a crashed writer can be taken over after this long.
# Longer than APPLY_LEASE, so an operation is reported stale before its
# fence can be taken over; holders still recheck the fence before confirming.
FENCE_LEASE = timedelta(minutes=10)


@contextmanager
def account_fence(db, user_id):
    """Serialize city grant writers for one account across processes.

    Raises CityGrantConflict when another writer holds a live fence. The
    fence is a document keyed by the account id, so taking it is atomic.
    """
    token = ObjectId()
    now = utcnow()
    try:
        db[FENCES].find_one_and_update(
            {"_id": str(user_id), "expires_at": {"$lte": now}},
            {"$set": {"token": token, "expires_at": now + FENCE_LEASE}},
            upsert=True,
        )
    except DuplicateKeyError:
        raise CityGrantConflict("another city grant write for this account is in progress") from None
    try:
        yield token
    finally:
        try:
            db[FENCES].delete_one({"_id": str(user_id), "token": token})
        except PyMongoError:
            logger.exception("Could not release city grant fence for %s; it expires by lease", user_id)


def fence_held(db, user_id, token) -> bool:
    return db[FENCES].count_documents(
        {"_id": str(user_id), "token": token, "expires_at": {"$gt": utcnow()}}, limit=1,
    ) == 1


def sync_city_scope_grant(db, user_id: ObjectId, city_keys: list[str], permissions: list[str], actor, *,
                          allow_city_enablement: bool = True, additive_only: bool = False,
                          before_write: Optional[Callable[[], None]] = None,
                          fenced: bool = False, expires_at: Optional[datetime] = None) -> SyncResult:
    """Replace the user's city grant under the account fence.

    ``fenced=True`` means the caller already holds ``account_fence``.
    ``expires_at`` (naive UTC, exclusive) sets the grant's end. Without it an
    existing grant keeps its expiry, so a legacy save never extends one.
    """
    options = dict(allow_city_enablement=allow_city_enablement, additive_only=additive_only,
                   before_write=before_write, expires_at=expires_at)
    if fenced:
        return _sync_city_scope_grant(db, user_id, city_keys, permissions, actor, **options)
    with account_fence(db, user_id):
        return _sync_city_scope_grant(db, user_id, city_keys, permissions, actor, **options)


def _sync_city_scope_grant(db, user_id: ObjectId, city_keys: list[str], permissions: list[str], actor, *,
                           allow_city_enablement: bool = True, additive_only: bool = False,
                           before_write: Optional[Callable[[], None]] = None,
                           expires_at: Optional[datetime] = None) -> SyncResult:
    """Replace the user's city grant; audit each scope mutation in its own outbox.

    ``allow_city_enablement=False`` and ``additive_only=True`` raise
    CityGrantConflict before any write when the grant would enable a city or
    replace an existing city grant. ``before_write`` runs after those checks,
    before the first write; the appointment operation uses it to move its
    journal into the applying phase.
    """
    city_keys = sorted({normalize_city_key(key) for key in city_keys if normalize_city_key(key) in CITY_KEY_TO_NAME})
    permissions = sorted({permission for permission in permissions if permission in CITY_ADMIN_AVAILABLE_PERMISSIONS})
    query = _active_city_grant_query(user_id)
    granting = bool(city_keys and permissions)
    if granting:
        permissions = sorted(set(permissions) | {"LIST_DEMOS", "VIEW_DEMO"})
        if not allow_city_enablement and set(city_keys) - enabled_city_keys(db):
            raise CityGrantConflict("granting would enable a city the caller did not allow")
    if additive_only and (not granting or db.admin_scope_grants.find_one(query)):
        raise CityGrantConflict("an additive grant found an existing city grant")
    if before_write is not None:
        before_write()
    enabled: list[str] = []
    if granting:
        enabled = auto_activate_granted_cities(db, city_keys, actor, allow=allow_city_enablement)
    attribution = history_actor(actor)

    flush_user_history(db, user_id)

    def snapshot(grant):
        keys = grant.get("scope_keys")
        if keys is None:
            keys = [grant["scope_key"]] if grant.get("scope_key") else []
        return {"city_scope_keys": sorted(set(keys)),
                "city_scope_permissions": sorted(set(grant.get("permissions", [])))}

    def update_grant(grant, payload, after):
        entry = history_event(user_id, snapshot(grant), after, attribution)
        if commit_history_change(db.admin_scope_grants, grant, payload, entry, {
            "user_id", "scope_type", "scope_keys", "scope_key", "permissions", "revoked_at",
        }) is None:
            raise CityGrantConflict("city grant changed concurrently")

    if not city_keys or not permissions:
        for grant in db.admin_scope_grants.find(query):
            update_grant(grant, {"revoked_at": utcnow(), "revoked_by": str(actor._id)},
                         {"city_scope_keys": [], "city_scope_permissions": []})
        document = db.users.find_one({"_id": user_id})
        if document and document.get("role") == "city_admin":
            commit_profile_change(db, document, {"role": "user"}, actor)
        flush_user_history(db, user_id)
        return SyncResult([], [], enabled)

    existing = db.admin_scope_grants.find_one(query)
    payload = {
        "user_id": user_id,
        "scope_type": "city",
        "scope_keys": city_keys,
        "role": "city_reviewer",
        "permissions": permissions,
        "updated_at": utcnow(),
        "updated_by": str(actor._id),
        "revoked_at": None,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    after = {"city_scope_keys": city_keys, "city_scope_permissions": permissions}
    if existing:
        update_grant(existing, payload, after)
    else:
        payload["_id"] = ObjectId()
        payload["created_at"] = utcnow()
        payload["granted_by"] = str(actor._id)
        payload[PENDING_HISTORY] = [history_event(
            user_id, {"city_scope_keys": [], "city_scope_permissions": []}, after, attribution,
        )]
        db.admin_scope_grants.insert_one(payload)
    document = db.users.find_one({"_id": user_id})
    if document and document.get("role") not in {"admin", "global_admin", "god", "city_admin"}:
        commit_profile_change(db, document, {"role": "city_admin"}, actor)
    flush_user_history(db, user_id)
    return SyncResult(city_keys, permissions, enabled)


# --------------------------------------------------------------------------- #
# city_admin_v1 appointment operation (Hallinto, disabled: no route)
# --------------------------------------------------------------------------- #
OPERATIONS = "city_grant_operations"
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
ACCOUNT_ID = re.compile(r"^[a-f0-9]{24}$")
# An operation still pending after this long is reported reconciliation_required.
APPLY_LEASE = timedelta(minutes=5)
ERROR_STATUS = {
    "denied": 403, "stale_state": 409, "idempotency_conflict": 409,
    "unsupported_scope": 422, "unknown_operation": 404, "unavailable": 503,
}
REQUEST_FIELDS = {
    "account_id", "city", "template", "expected_state", "allow_city_enablement",
    "decision_id", "decision_revision", "application_id", "authority_attestation", "valid_until",
}
VALID_UNTIL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# Hallinto allows a last valid day under five years ahead; the end is the next midnight.
MAX_VALIDITY = timedelta(days=5 * 366 + 1)
INSTANT = "%Y-%m-%dT%H:%M:%SZ"


def parse_valid_until(value) -> Optional[datetime]:
    """Exclusive end instant, RFC 3339 UTC with seconds; naive UTC or None."""
    if not isinstance(value, str) or not VALID_UNTIL.match(value):
        return None
    try:
        return datetime.strptime(value, INSTANT)
    except ValueError:
        return None
REQUIRED_FIELDS = REQUEST_FIELDS - {"application_id"}


@dataclass
class Outcome:
    status_code: int
    body: dict


def _error(code: str) -> Outcome:
    return Outcome(ERROR_STATUS[code], {"code": code})


def ensure_operation_indexes(db) -> None:
    db[OPERATIONS].create_index(
        [("integration", 1), ("actor_id", 1), ("idempotency_key", 1)],
        unique=True, name="city_grant_operation_key",
    )
    db[OPERATIONS].create_index([("account_id", 1), ("created_at", -1)], name="city_grant_operation_account")


def _non_empty_id(value) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128


def validate_appointment(request: Any) -> Optional[dict]:
    """Return the canonical intent, or None for an unsupported shape.

    The authority attestation is a renewable proof, not part of the intent:
    it is checked by the caller and never stored or logged here.
    """
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS or REQUIRED_FIELDS - set(request):
        return None
    city = request["city"]
    revision = request["decision_revision"]
    attestation = request["authority_attestation"]
    if not (isinstance(request["account_id"], str) and ACCOUNT_ID.match(request["account_id"])
            and isinstance(city, str) and 1 <= len(city) <= 128
            and normalize_city_key(city) == city and city in CITY_KEY_TO_NAME
            and request["template"] == CITY_ADMIN_V1_TEMPLATE
            and _non_empty_id(request["expected_state"])
            and type(request["allow_city_enablement"]) is bool
            and _non_empty_id(request["decision_id"])
            and type(revision) is int and revision >= 1
            and ("application_id" not in request or _non_empty_id(request["application_id"]))
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384
            and parse_valid_until(request["valid_until"]) is not None):
        return None
    intent = {key: request[key] for key in sorted(REQUEST_FIELDS - {"authority_attestation"}) if key in request}
    intent["permissions"] = CITY_ADMIN_V1_PERMISSIONS
    return intent


def _digest(value) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _active_grants(db, user_id) -> list:
    return list(db.admin_scope_grants.find({
        "user_id": {"$in": [user_id, str(user_id)]},
        **live_grant_filter(),
    }))


def _owner_inputs(db, doc) -> dict:
    """Owner fields that affect the command, for the opaque expected_state."""
    from mielenosoitukset_fi.utils.security_cases import protected_account

    grants = _active_grants(db, doc["_id"])
    return {
        "account": str(doc["_id"]),
        "role": doc.get("role"),
        "global_admin": bool(doc.get("global_admin")),
        "governance_managed": bool(doc.get("governance_managed")),
        "global_permissions": sorted(set(doc.get("global_permissions") or [])),
        "active": doc.get("active", True), "banned": doc.get("banned", False),
        "confirmed": doc.get("confirmed", False),
        "protected": protected_account(db, doc),
        "grants": sorted(
            [{"id": str(g["_id"]), "type": g.get("scope_type"),
              "keys": sorted(g.get("scope_keys") or ([g["scope_key"]] if g.get("scope_key") else [])),
              "permissions": sorted(g.get("permissions") or []),
              "expires_at": g.get("expires_at")} for g in grants],
            key=lambda grant: grant["id"],
        ),
        "enabled_cities": sorted(enabled_city_keys(db)),
    }


def _eligible(inputs: dict) -> bool:
    from mielenosoitukset_fi.utils.system_teams import active_account

    return (inputs["role"] in {None, "user"} and not inputs["global_admin"]
            and not inputs["governance_managed"] and not inputs["global_permissions"]
            and not inputs["protected"] and not inputs["grants"]
            and active_account({"active": inputs["active"], "banned": inputs["banned"],
                                "confirmed": inputs["confirmed"]}))


def _scopes(grants: list) -> list:
    """Effective city scopes. ``valid_until`` appears when every grant for the
    city expires; it is the latest of those ends."""
    scopes: dict[str, set] = {}
    ends: dict[str, list] = {}
    for grant in grants:
        if grant.get("scope_type") != "city":
            continue
        keys = grant.get("scope_keys")
        if keys is None and grant.get("scope_key"):
            keys = [grant["scope_key"]]
        if isinstance(keys, str):
            keys = [keys]
        for key in keys or []:
            city = normalize_city_key(key)
            if city:
                scopes.setdefault(city, set()).update(grant.get("permissions") or [])
                ends.setdefault(city, []).append(grant.get("expires_at"))
    result = []
    for city, perms in sorted(scopes.items()):
        if not perms:
            continue
        scope = {"city": city, "permissions": sorted(perms)}
        if ends[city] and all(ends[city]):
            scope["valid_until"] = max(ends[city]).strftime(INSTANT)
        result.append(scope)
    return result


def read_city_grant_state(db, account_id: str) -> Optional[dict]:
    """Authoritative scoped state; None when the account does not exist."""
    if not isinstance(account_id, str) or not ACCOUNT_ID.match(account_id):
        return None
    doc = db.users.find_one({"_id": ObjectId(account_id)})
    if not doc:
        return None
    inputs = _owner_inputs(db, doc)
    return {
        "account_id": account_id,
        "expected_state": _digest(inputs),
        "eligible": _eligible(inputs),
        "scopes": _scopes(_active_grants(db, doc["_id"])),
    }


def _load_user(doc):
    from mielenosoitukset_fi.users.models import User

    return User.from_db(doc)


def _actor_authorized(db, actor_id) -> Optional[Any]:
    """Reload the actor; require an active account allowed to manage grants."""
    from mielenosoitukset_fi.utils.system_teams import active_account

    doc = db.users.find_one({"_id": ObjectId(str(actor_id))}) if ObjectId.is_valid(str(actor_id)) else None
    if not active_account(doc):
        return None
    actor = _load_user(doc)
    return actor if can_manage_scope_grants(actor) else None


def _receipt_identity(operation: dict) -> dict:
    return {
        "operation_id": str(operation["_id"]),
        "account_id": operation["account_id"],
        "decision_id": operation["intent"]["decision_id"],
        "decision_revision": operation["intent"]["decision_revision"],
        "valid_until": operation["intent"]["valid_until"],
    }


def _outcome_for(operation: dict) -> Outcome:
    status = operation["status"]
    if status == "confirmed":
        return Outcome(200, operation["receipt"])
    if status == "rejected":
        return Outcome(ERROR_STATUS[operation["receipt"]["error"]["code"]], {"code": operation["receipt"]["error"]["code"]})
    return Outcome(202, {**_receipt_identity(operation), "status": status})


def _log(operation: dict, transition: str) -> None:
    # Correlate actor, decision, account and operation; never proof bytes.
    logger.info(
        "city_admin_v1 operation=%s transition=%s integration=%s actor=%s account=%s decision=%s@%s",
        operation["_id"], transition, operation["integration"], operation["actor_id"],
        operation["account_id"], operation["intent"]["decision_id"], operation["intent"]["decision_revision"],
    )


def _transition(db, operation: dict, frm: set, status: str, receipt: Optional[dict] = None, **extra) -> dict:
    now = utcnow()
    update = {"$set": {"status": status, "updated_at": now, **extra},
              "$push": {"transitions": {"status": status, "at": now}}}
    if receipt is not None:
        update["$set"]["receipt"] = receipt
    stored = db[OPERATIONS].find_one_and_update(
        {"_id": operation["_id"], "status": {"$in": sorted(frm)}}, update,
        return_document=ReturnDocument.AFTER,
    )
    if stored is None:
        # Someone else moved it; report whatever is durable now.
        return db[OPERATIONS].find_one({"_id": operation["_id"]})
    _log(stored, status)
    return stored


def _reject(db, operation: dict, code: str) -> dict:
    receipt = {**_receipt_identity(operation), "status": "rejected", "error": {"code": code}}
    return _transition(db, operation, {"pending"}, "rejected", receipt)


def _replay(db, existing: dict, digest: str) -> Outcome:
    if existing["intent_digest"] != digest:
        return _error("idempotency_conflict")
    if existing["status"] == "pending" and existing["updated_at"] <= utcnow() - APPLY_LEASE:
        existing = _transition(db, existing, {"pending"}, "reconciliation_required")
    return _outcome_for(existing)


def verify_effective_grant(db, account_id: ObjectId, city: str, valid_until: str) -> dict:
    """Recheck the effective result with newly loaded owner state."""
    doc = db.users.find_one({"_id": account_id})
    if not doc:
        return {"ok": False}
    user = _load_user(doc)
    user._admin_scope_grants = _active_grants(db, account_id)  # fresh, not cached
    scopes = _scopes(user._admin_scope_grants)
    effective = [p for p in CITY_ADMIN_AVAILABLE_PERMISSIONS
                 if user.has_scoped_permission(p, scope_type="city", scope_key=city)]
    city_enabled = city in enabled_city_keys(db)
    ok = (user.is_active and not user.has_full_permissions() and doc.get("role") == "city_admin"
          and not user.global_permissions and city_enabled
          and scopes == [{"city": city, "permissions": CITY_ADMIN_V1_PERMISSIONS, "valid_until": valid_until}]
          and sorted(effective) == CITY_ADMIN_V1_PERMISSIONS)
    effective_scope = {"city": city, "permissions": sorted(effective)}
    if scopes and scopes[0].get("valid_until"):
        effective_scope["valid_until"] = scopes[0]["valid_until"]
    return {"ok": ok, "effective": effective_scope}


def apply_city_admin_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> Outcome:
    """Additive one-city appointment for one active account without a city grant.

    The caller must already have authenticated the integration (workload
    identity) and the human actor, and verified the action-bound proof and
    Hallinto's authority attestation for this exact intent. This function
    re-checks owner authority, target and state itself; an attestation never
    replaces those checks.
    """
    if not isinstance(integration, str) or not integration:
        return _error("denied")
    if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY.match(idempotency_key):
        return _error("unsupported_scope")
    intent = validate_appointment(request)
    if intent is None:
        return _error("unsupported_scope")
    actor = _actor_authorized(db, actor_id)
    if actor is None or str(actor._id) == intent["account_id"]:
        return _error("denied")

    digest = _digest(intent)
    now = utcnow()
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest, "intent": intent,
        "account_id": intent["account_id"], "status": "pending", "phase": "allocated",
        "created_at": now, "updated_at": now, "transitions": [{"status": "pending", "at": now}],
    }
    key = {"integration": integration, "actor_id": str(actor._id), "idempotency_key": idempotency_key}
    try:
        db[OPERATIONS].insert_one(operation)
    except DuplicateKeyError:
        existing = db[OPERATIONS].find_one(key)
        if existing is None:
            return _error("unavailable")
        return _replay(db, existing, digest)
    _log(operation, "pending")

    account_id = ObjectId(intent["account_id"])
    try:
        with account_fence(db, account_id) as fence:
            # Owner state is checked, written and reread under one fence.
            return _apply_fenced(db, operation, intent, actor, integration, account_id, fence)
    except CityGrantConflict:
        # Another writer holds the fence. Fencing happens before any write.
        current = db[OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending" and current.get("phase") == "allocated":
            return _outcome_for(_reject(db, current, "stale_state"))
        return _outcome_for(current) if current else _error("unavailable")


def _apply_fenced(db, operation, intent, actor, integration, account_id, fence) -> Outcome:
    city = intent["city"]
    target = db.users.find_one({"_id": account_id})
    if target is None:
        # Conceal existence from the integration.
        return _outcome_for(_reject(db, operation, "denied"))
    inputs = _owner_inputs(db, target)
    if _digest(inputs) != intent["expected_state"]:
        return _outcome_for(_reject(db, operation, "stale_state"))
    if not _eligible(inputs):
        return _outcome_for(_reject(db, operation, "unsupported_scope"))
    expires_at = parse_valid_until(intent["valid_until"])
    now = utcnow()
    if not (now < expires_at <= now + MAX_VALIDITY):
        return _outcome_for(_reject(db, operation, "unsupported_scope"))
    city_was_enabled = city in inputs["enabled_cities"]
    if not city_was_enabled and not intent["allow_city_enablement"]:
        return _outcome_for(_reject(db, operation, "unsupported_scope"))

    def applying():
        nonlocal operation
        if not fence_held(db, account_id, fence):
            raise CityGrantConflict("account fence expired before the write")
        stored = db[OPERATIONS].find_one_and_update(
            {"_id": operation["_id"], "status": "pending", "phase": "allocated"},
            {"$set": {"phase": "applying", "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        if stored is None:
            raise CityGrantConflict("operation was moved by another process")
        operation = stored

    from mielenosoitukset_fi.utils.audit_trail import audit_scope

    try:
        with audit_scope("integration", integration, actor={"kind": "user", "user_id": str(actor._id)},
                         operation_id=str(operation["_id"])):
            result = sync_city_scope_grant(
                db, account_id, [city], CITY_ADMIN_V1_PERMISSIONS, actor,
                allow_city_enablement=intent["allow_city_enablement"], additive_only=True,
                before_write=applying, fenced=True, expires_at=expires_at,
            )
    except CityGrantConflict:
        if operation.get("phase") != "applying":
            return _outcome_for(_reject(db, operation, "stale_state"))
        return _outcome_for(_transition(db, operation, {"pending"}, "reconciliation_required"))
    except PyMongoError:
        logger.exception("city_admin_v1 operation=%s write failed", operation["_id"])
        if operation.get("phase") != "applying":
            return _outcome_for(_reject(db, operation, "stale_state"))
        try:
            return _outcome_for(_transition(db, operation, {"pending"}, "reconciliation_required"))
        except PyMongoError:
            return _error("unavailable")

    verified = verify_effective_grant(db, account_id, city, intent["valid_until"])
    enabled = city in result.enabled_cities
    if (not verified["ok"] or (enabled and not intent["allow_city_enablement"])
            or not fence_held(db, account_id, fence)):
        return _outcome_for(_transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {
        **_receipt_identity(operation), "status": "confirmed",
        "checked_at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "effective": verified["effective"], "resulted_in_city_enablement": enabled,
    }
    return _outcome_for(_transition(db, operation, {"pending"}, "confirmed", receipt))


def read_operation_receipt(db, *, integration: str, actor_id, idempotency_key: str) -> Outcome:
    """Receipt of the originating scoped request, revalidating visibility."""
    actor = _actor_authorized(db, actor_id)
    if actor is None:
        return _error("denied")
    if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY.match(idempotency_key):
        return _error("unknown_operation")
    operation = db[OPERATIONS].find_one({
        "integration": integration, "actor_id": str(actor._id), "idempotency_key": idempotency_key,
    })
    if operation is None:
        return _error("unknown_operation")
    if operation["status"] == "pending" and operation["updated_at"] <= utcnow() - APPLY_LEASE:
        operation = _transition(db, operation, {"pending"}, "reconciliation_required")
    if operation["status"] == "rejected":
        return Outcome(200, operation["receipt"])
    return _outcome_for(operation)
