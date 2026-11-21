"""Role bundles decided in Hallinto and written by Flask (translator, API access, admin).

An account gets one named bundle by an appointment decision: a role and an
exact permission list that Flask owns. This module shares the journal, the
account fence and the trust model with ``city_grants``. The caller has already
verified the mTLS workload identity, the action-bound proof and Hallinto's
attestation for the exact intent. Flask re-checks the actor, the account and
its state here; an attestation never replaces those checks.

* ``apply_role_bundle_v1``: write the bundle, replacing the account's earlier
  bundle, and report exactly what the account now has.
* ``apply_role_bundle_end_v1``: revoke or freeze the bundle an appointment
  wrote, at once and idempotently.

A bundle stores ``role_bundle`` on the user. Reads treat it as gone once
``valid_until`` has passed (``utils/role_bundle_expiry``).
"""

from __future__ import annotations

from typing import Any, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.role_bundle_expiry import bundle_expired, subtract_bundle
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg

TRANSLATOR = "translator_v1"
API_ACCESS = "api_access_v1"
ADMIN = "admin_v1"

# Flask owns these lists; Hallinto confirms only exactly this set in the receipt.
# Nothing here changes rights or runs the system, and nothing implies site-wide admin.
BUNDLES = {
    TRANSLATOR: {
        "role": "user", "label": "Kääntäjä",
        "permissions": sorted({"TRANSLATE_DEMO", "REVIEW_DEMO_TRANSLATIONS", "TRANSLATE_UI", "REVIEW_UI_TRANSLATIONS"}),
    },
    API_ACCESS: {
        "role": "user", "label": "API-käyttö",
        "permissions": sorted({"API_READ", "API_WRITE_DEMOS"}),
    },
    ADMIN: {
        "role": "admin", "label": "Ylläpitäjä",
        "permissions": sorted({
            "CREATE_DEMO", "EDIT_DEMO", "DELETE_DEMO", "VIEW_DEMO", "LIST_DEMOS", "ACCEPT_DEMO",
            "GENERATE_EDIT_LINK", "CREATE_RECURRING_DEMO", "EDIT_RECURRING_DEMO", "DELETE_RECURRING_DEMO",
            "VIEW_RECURRING_DEMO", "LIST_RECURRING_DEMOS", "CREATE_CASE", "EDIT_CASE", "DELETE_CASE",
            "VIEW_CASE", "LIST_CASES", "LIST_USERS", "VIEW_USER", "LIST_ORGANIZATIONS", "VIEW_ORGANIZATION",
            "VIEW_LOGS",
        }),
    },
}

APPLY_FIELDS = {"account_id", "template", "decision_id", "decision_revision", "valid_until",
                "expected_state", "authority_attestation"}
END_FIELDS = {"appointment_id", "kind", "account_id", "template", "decision_id", "decision_revision",
              "authority_attestation"}
END_KINDS = {"revoke": "hallinto_revoked", "freeze": "hallinto_frozen"}


# --------------------------------------------------------------------------- #
# Intents
# --------------------------------------------------------------------------- #

def _common_ok(request: dict, attestation_field="authority_attestation") -> bool:
    revision = request["decision_revision"]
    attestation = request[attestation_field]
    return bool(isinstance(request["account_id"], str) and cg.ACCOUNT_ID.match(request["account_id"])
                and request["template"] in BUNDLES
                and cg._non_empty_id(request["decision_id"])
                and type(revision) is int and revision >= 1
                and isinstance(attestation, str) and 1 <= len(attestation) <= 16384)


def validate_bundle(request: Any) -> Optional[dict]:
    """Canonical intent (attestation excluded, the bundle's permissions added), or None."""
    if not isinstance(request, dict) or set(request) != APPLY_FIELDS:
        return None
    if not (_common_ok(request) and cg._non_empty_id(request["expected_state"])
            and cg.parse_valid_until(request["valid_until"]) is not None):
        return None
    intent = {key: request[key] for key in sorted(APPLY_FIELDS - {"authority_attestation"})}
    intent["permissions"] = list(BUNDLES[request["template"]]["permissions"])
    return intent


def bundle_digest(request: Any) -> Optional[str]:
    intent = validate_bundle(request)
    return cg._digest(intent) if intent is not None else None


def validate_end(request: Any) -> Optional[dict]:
    """Canonical end intent. ``appointment_id`` is the Idempotency-Key of the appointment."""
    if not isinstance(request, dict) or set(request) != END_FIELDS:
        return None
    if not (_common_ok(request) and isinstance(request["appointment_id"], str)
            and cg.IDEMPOTENCY_KEY.match(request["appointment_id"]) and request["kind"] in END_KINDS):
        return None
    return {key: request[key] for key in sorted(END_FIELDS - {"authority_attestation"})}


def end_digest(request: Any) -> Optional[str]:
    intent = validate_end(request)
    return cg._digest(intent) if intent is not None else None


# --------------------------------------------------------------------------- #
# Owner state
# --------------------------------------------------------------------------- #

def _bundle_of(doc: dict) -> Optional[dict]:
    """The bundle still written on the account (live or expired), not an ended one."""
    bundle = doc.get("role_bundle")
    return bundle if bundle and not bundle.get("ended") else None


def _base_access(doc: dict):
    """Role and permissions the account holds apart from any bundle on it."""
    role, permissions = doc.get("role"), list(doc.get("global_permissions") or [])
    bundle = _bundle_of(doc)
    if bundle:
        role, permissions = subtract_bundle(role, permissions, bundle)
    return role, permissions


def _inputs(db, doc: dict) -> dict:
    """Owner fields that affect the command, for the opaque expected_state."""
    from mielenosoitukset_fi.utils.security_cases import protected_account

    user = cg._load_user(doc)
    bundle = _bundle_of(doc)
    end = (bundle or {}).get("valid_until")
    return {
        "account": str(doc["_id"]),
        "role": doc.get("role"),
        "global_admin": bool(doc.get("global_admin")),
        "governance_managed": bool(doc.get("governance_managed")),
        "global_permissions": sorted(set(doc.get("global_permissions") or [])),
        "active": doc.get("active", True), "banned": doc.get("banned", False),
        "confirmed": doc.get("confirmed", False),
        "suspended": bool((doc.get("security_suspension") or {}).get("active")),
        "protected": protected_account(db, doc),
        "team_access": sorted(user.system_team_access),
        "grants": sorted(str(g["_id"]) for g in cg._active_grants(db, doc["_id"])),
        "bundle": ({"template": bundle.get("template"), "operation": bundle.get("operation_id"),
                    "valid_until": end.strftime(cg.INSTANT) if end else None} if bundle else None),
    }


def _eligible(inputs: dict, doc: dict, template: str) -> bool:
    """Plain accounts, or ones holding only an earlier bundle. Never an elevated one."""
    from mielenosoitukset_fi.utils.system_teams import active_account

    if not active_account({"active": inputs["active"], "banned": inputs["banned"],
                           "confirmed": inputs["confirmed"],
                           "security_suspension": {"active": inputs["suspended"]}}):
        return False
    role, permissions = _base_access(doc)
    target = BUNDLES[template]
    # Rights the account already had by the old admin tools may only be ones the
    # bundle gives anyway, so nothing is dropped and nothing is added silently.
    if not (role in {None, "user"} or (role == "admin" and target["role"] == "admin")):
        return False
    return (not inputs["global_admin"] and not inputs["protected"] and not inputs["team_access"]
            and not inputs["grants"] and set(permissions) <= set(target["permissions"]))


def read_bundle_state(db, account_id: str, template: str) -> Optional[dict]:
    """Authoritative state for the approval page; None when the account does not exist."""
    if not isinstance(account_id, str) or not cg.ACCOUNT_ID.match(account_id) or template not in BUNDLES:
        return None
    doc = db.users.find_one({"_id": ObjectId(account_id)})
    if not doc:
        return None
    inputs = _inputs(db, doc)
    return {"account_id": account_id, "expected_state": cg._digest(inputs),
            "eligible": _eligible(inputs, doc, template)}


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

_SENDER = None


def _email_sender():
    global _SENDER
    if _SENDER is None:
        from mielenosoitukset_fi.emailer.EmailSender import EmailSender
        _SENDER = EmailSender()
    return _SENDER


def _recipients(db, account_doc: dict) -> list:
    """The account and the national admin team's active members, once each."""
    from mielenosoitukset_fi.utils.system_teams import approved_members

    emails = []
    for doc in [account_doc, *approved_members(db, "national")]:
        email = (doc.get("email") or "").strip()
        if email and email not in emails:
            emails.append(email)
    return emails


def _notify(db, operation: dict, account_doc: dict, action: str) -> None:
    """Best effort: mail never changes what was written."""
    try:
        recipients = _recipients(db, account_doc)
        if not recipients:
            return
        intent = operation["intent"]
        bundle = BUNDLES[intent["template"]]
        name = account_doc.get("displayname") or account_doc.get("username") or ""
        subjects = {"applied": f"Roolipaketti myönnetty: {bundle['label']}",
                    "revoke": f"Roolipaketti peruttu: {bundle['label']}",
                    "freeze": f"Roolipaketti jäädytetty: {bundle['label']}"}
        _email_sender().queue_email(
            template_name="role_bundle_notification.html", subject=subjects[action], recipients=recipients,
            context={"action": action, "bundle_label": bundle["label"], "account_name": name,
                     "decision_id": intent["decision_id"], "decision_revision": intent["decision_revision"],
                     "valid_until": intent.get("valid_until", ""), "permissions": bundle["permissions"]},
        )
        db[cg.OPERATIONS].update_one({"_id": operation["_id"]}, {"$set": {"notified_at": utcnow()}})
    except Exception:  # noqa: BLE001 - notification must not fail the operation
        logger.exception("role bundle operation=%s notification failed", operation.get("_id"))


# --------------------------------------------------------------------------- #
# Apply and end
# --------------------------------------------------------------------------- #

def _new_operation(db, actor, integration, idempotency_key, intent, digest):
    now = utcnow()
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest, "intent": intent,
        "account_id": intent["account_id"], "status": "pending", "phase": "allocated",
        "created_at": now, "updated_at": now, "transitions": [{"status": "pending", "at": now}],
    }
    key = {"integration": integration, "actor_id": str(actor._id), "idempotency_key": idempotency_key}
    try:
        db[cg.OPERATIONS].insert_one(operation)
    except DuplicateKeyError:
        existing = db[cg.OPERATIONS].find_one(key)
        if existing is None:
            return None, cg._error("unavailable")
        return None, cg._replay(db, existing, digest)
    cg._log(operation, "pending")
    return operation, None


def _precheck(db, integration, idempotency_key, intent, actor_id):
    if not isinstance(integration, str) or not integration:
        return None, cg._error("denied")
    if not isinstance(idempotency_key, str) or not cg.IDEMPOTENCY_KEY.match(idempotency_key):
        return None, cg._error("unsupported_scope")
    if intent is None:
        return None, cg._error("unsupported_scope")
    actor = cg._actor_authorized(db, actor_id)
    if actor is None or str(actor._id) == intent["account_id"]:
        return None, cg._error("denied")
    return actor, None


def _run_fenced(db, operation, intent, actor, integration, fenced):
    account_id = ObjectId(intent["account_id"])
    try:
        with cg.account_fence(db, account_id) as fence:
            return fenced(db, operation, intent, actor, integration, account_id, fence)
    except cg.CityGrantConflict:
        current = db[cg.OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending" and current.get("phase") == "allocated":
            return cg._outcome_for(cg._reject(db, current, "stale_state"))
        return cg._outcome_for(current) if current else cg._error("unavailable")


def _mark_applying(db, operation, account_id, fence):
    if not cg.fence_held(db, account_id, fence):
        raise cg.CityGrantConflict("account fence expired before the write")
    stored = db[cg.OPERATIONS].find_one_and_update(
        {"_id": operation["_id"], "status": "pending", "phase": "allocated"},
        {"$set": {"phase": "applying", "updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if stored is None:
        raise cg.CityGrantConflict("operation was moved by another process")
    return stored


def _write(db, operation, intent, actor, integration, account_id, fence, log_name, write):
    """Run ``write(acting)`` with the operation in its applying phase.

    Returns (operation, outcome); outcome is set when the write failed.
    """
    from mielenosoitukset_fi.utils.audit_trail import audit_scope

    try:
        with audit_scope("integration", integration, actor={"kind": "user", "user_id": str(actor._id)},
                         operation_id=str(operation["_id"])):
            operation = _mark_applying(db, operation, account_id, fence)
            write(actor)
    except cg.CityGrantConflict:
        if operation.get("phase") != "applying":
            return operation, cg._outcome_for(cg._reject(db, operation, "stale_state"))
        return operation, cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    except PyMongoError:
        logger.exception("%s operation=%s write failed", log_name, operation["_id"])
        if operation.get("phase") != "applying":
            return operation, cg._outcome_for(cg._reject(db, operation, "stale_state"))
        try:
            return operation, cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
        except PyMongoError:
            return operation, cg._error("unavailable")
    return operation, None


def apply_role_bundle_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Give one account exactly one bundle, replacing its earlier bundle."""
    intent = validate_bundle(request)
    actor, error = _precheck(db, integration, idempotency_key, intent, actor_id)
    if error is not None:
        return error
    operation, replayed = _new_operation(db, actor, integration, idempotency_key, intent, cg._digest(intent))
    if operation is None:
        return replayed
    return _run_fenced(db, operation, intent, actor, integration, _apply_fenced)


def _apply_fenced(db, operation, intent, actor, integration, account_id, fence) -> cg.Outcome:
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return cg._outcome_for(cg._reject(db, operation, "denied"))  # Conceal existence.
    inputs = _inputs(db, doc)
    if cg._digest(inputs) != intent["expected_state"]:
        return cg._outcome_for(cg._reject(db, operation, "stale_state"))
    template = intent["template"]
    if not _eligible(inputs, doc, template):
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))
    expires_at = cg.parse_valid_until(intent["valid_until"])
    now = utcnow()
    if not (now < expires_at <= now + cg.MAX_VALIDITY):
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))

    bundle = BUNDLES[template]
    replacing = _bundle_of(doc) is not None

    def write(acting):
        document = db.users.find_one({"_id": account_id})
        updates = {
            "role": bundle["role"],
            "global_permissions": list(bundle["permissions"]),
            # Later changes come through governance, not the old admin tools.
            "governance_managed": True,
            "role_bundle": {"template": template, "operation_id": str(operation["_id"]),
                            "role": bundle["role"], "permissions": list(bundle["permissions"]),
                            "valid_until": expires_at, "applied_at": utcnow()},
        }
        if replacing:
            updates["auth_epoch"] = (document.get("auth_epoch") or 0) + 1
        cg.commit_profile_change(db, document, updates, acting, "hallinto_role_bundle")
        if replacing:
            from mielenosoitukset_fi.utils.security_cases import _revoke
            _revoke(db, document, operation["_id"])

    operation, failed = _write(db, operation, intent, actor, integration, account_id, fence,
                               "role_bundle_v1", write)
    if failed is not None:
        return failed

    after = db.users.find_one({"_id": account_id}) or {}
    user = cg._load_user(after) if after else None
    stored = after.get("role_bundle") or {}
    ok = bool(user and stored.get("operation_id") == str(operation["_id"])
              and user.role == bundle["role"] and sorted(user.global_permissions) == bundle["permissions"]
              and not user.global_admin and not user.system_team_access)
    if not ok or not cg.fence_held(db, account_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {
        **cg._receipt_identity(operation), "status": "confirmed",
        "checked_at": utcnow().strftime(cg.INSTANT),
        "effective": {"role": user.role, "city": "", "permissions": sorted(user.global_permissions),
                      "valid_until": intent["valid_until"]},
        "resulted_in_city_enablement": False,
    }
    stored_op = cg._transition(db, operation, {"pending"}, "confirmed", receipt)
    if stored_op.get("status") == "confirmed" and not stored_op.get("notified_at"):
        _notify(db, stored_op, after, "applied")
    return cg._outcome_for(stored_op)


def apply_role_bundle_end_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Revoke or freeze the bundle an appointment wrote. Idempotent."""
    intent = validate_end(request)
    actor, error = _precheck(db, integration, idempotency_key, intent, actor_id)
    if error is not None:
        return error
    operation, replayed = _new_operation(db, actor, integration, idempotency_key, intent, cg._digest(intent))
    if operation is None:
        return replayed
    return _run_fenced(db, operation, intent, actor, integration, _end_fenced)


def _original(db, integration: str, intent: dict) -> Optional[dict]:
    """The confirmed appointment this end refers to, for the same account and bundle."""
    return db[cg.OPERATIONS].find_one({
        "integration": integration, "idempotency_key": intent["appointment_id"],
        "account_id": intent["account_id"], "status": "confirmed",
        "intent.template": intent["template"], "intent.kind": {"$exists": False},
    })


def _remaining(db, account_id: ObjectId) -> dict:
    doc = db.users.find_one({"_id": account_id}) or {}
    user = cg._load_user(doc) if doc else None
    bundle = _bundle_of(doc)
    live = bool(bundle and not bundle_expired(bundle))
    return {"role": user.role if user else None,
            "permissions": sorted(user.global_permissions) if user else [],
            "global_admin": bool(user and user.global_admin),
            "bundle": bundle.get("template") if live else None}


def _end_fenced(db, operation, intent, actor, integration, account_id, fence) -> cg.Outcome:
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return cg._outcome_for(cg._reject(db, operation, "denied"))
    original = _original(db, integration, intent)
    if original is None:
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))
    original_id = str(original["_id"])
    bundle = _bundle_of(doc)
    # Only the bundle this appointment wrote. A later bundle or an ended one stays as it is.
    active = bool(bundle and bundle.get("operation_id") == original_id)

    def write(acting):
        document = db.users.find_one({"_id": account_id})
        current = _bundle_of(document)
        if not current or current.get("operation_id") != original_id:
            return  # Already gone: ending is idempotent.
        role, permissions = subtract_bundle(document.get("role"), document.get("global_permissions"), current)
        updates = {
            "role": role, "global_permissions": permissions,
            "auth_epoch": (document.get("auth_epoch") or 0) + 1,
            "role_bundle": {**current, "ended": True, "ended_by_operation": str(operation["_id"]),
                            "revoked_reason": END_KINDS[intent["kind"]],
                            "frozen": intent["kind"] == "freeze", "ended_at": utcnow()},
        }
        cg.commit_profile_change(db, document, updates, acting, "hallinto_role_bundle_ended")
        from mielenosoitukset_fi.utils.security_cases import _revoke
        _revoke(db, document, operation["_id"])

    operation, failed = _write(db, operation, intent, actor, integration, account_id, fence,
                               "role_bundle_end_v1", write)
    if failed is not None:
        return failed

    after = db.users.find_one({"_id": account_id}) or {}
    held = _bundle_of(after)
    if (held and held.get("operation_id") == original_id) or not cg.fence_held(db, account_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {**cg._receipt_identity(operation), "status": "confirmed",
               "checked_at": utcnow().strftime(cg.INSTANT), "effective": _remaining(db, account_id)}
    stored_op = cg._transition(db, operation, {"pending"}, "confirmed", receipt)
    if active and stored_op.get("status") == "confirmed" and not stored_op.get("notified_at"):
        _notify(db, stored_op, after, intent["kind"])
    return cg._outcome_for(stored_op)
