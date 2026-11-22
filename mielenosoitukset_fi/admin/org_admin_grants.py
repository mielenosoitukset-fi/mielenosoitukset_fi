"""The ``org_admin`` role follows organisation membership (decision Olivia, 22 Nov 2026).

Whoever is owner or admin of an organisation holds ``org_admin`` for it:
``users.role = "org_admin"`` (only when the account has no other role) and one
``admin_scope_grants`` row per organisation with ``scope_type: "organization"``
and exactly ``org_admin_role.PERMISSIONS``. Losing the membership role ends the
grant; the role returns to ``user`` when no grant is left. Nobody appoints
anybody: ``sync_account`` derives everything from the memberships, on every
membership write and in the ``sync_org_admins`` background job (which also
covers writes made by the Go API).

Hallinto can read the state and end a grant (``revoke`` or ``freeze``). An ended
grant is marked ``blocked`` and is not recreated while the account stays owner or
admin of that organisation; the block goes when the membership role goes. Until
the membership-role rights are phased out (by 1.1.2027, 1.3.2027 at the latest)
an end removes only what the grant gives.

Ending uses the trust model of ``city_grants`` and ``role_bundles``: the caller has
already verified the mTLS workload identity, the action-bound proof and Hallinto's
attestation for the exact intent. Off unless ``ORG_ADMIN_ROLE_ENABLED`` is true
(Flask-side sync) and for the routes ``ORG_ADMIN_HALLINTO_ENABLED``.
"""

from __future__ import annotations

from typing import Any, Optional

from bson import ObjectId

from mielenosoitukset_fi.utils import org_admin_role as role
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.scope_grants import live_grant_filter
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg
from . import role_bundles as rb

END_FIELDS = {"end_id", "kind", "account_id", "organization_id", "template", "decision_id",
              "decision_revision", "authority_attestation"}
END_APPROVAL_FIELDS = END_FIELDS - {"authority_attestation"}
END_KINDS = rb.END_KINDS
SOURCE = "hallinto_org_admin_v1"


def is_org_admin_fields(fields: Any) -> bool:
    return isinstance(fields, dict) and fields.get("template") == role.TEMPLATE


# --------------------------------------------------------------------------- #
# Intents
# --------------------------------------------------------------------------- #

def _id_ok(value) -> bool:
    return isinstance(value, str) and bool(cg.ACCOUNT_ID.match(value))


def _common_ok(request: dict) -> bool:
    revision = request["decision_revision"]
    attestation = request["authority_attestation"]
    return bool(isinstance(request["end_id"], str) and cg.IDEMPOTENCY_KEY.match(request["end_id"])
                and _id_ok(request["account_id"]) and _id_ok(request["organization_id"])
                and request["template"] == role.TEMPLATE
                and cg._non_empty_id(request["decision_id"])
                and type(revision) is int and revision >= 1
                and isinstance(attestation, str) and 1 <= len(attestation) <= 16384)


def validate_end(request: Any) -> Optional[dict]:
    if not isinstance(request, dict) or set(request) != END_FIELDS:
        return None
    if not (_common_ok(request) and request["kind"] in END_KINDS):
        return None
    return {key: request[key] for key in sorted(END_FIELDS - {"authority_attestation"})}


def end_digest(request: Any) -> Optional[str]:
    intent = validate_end(request)
    return cg._digest(intent) if intent is not None else None


# --------------------------------------------------------------------------- #
# Owner state
# --------------------------------------------------------------------------- #

def _org_grants(db, account_id, organization_id=None, *, live=True) -> list:
    query = {"user_id": {"$in": [account_id, str(account_id)]}, "scope_type": role.SCOPE_TYPE,
             "template": role.TEMPLATE}
    if organization_id is not None:
        query["scope_keys"] = str(organization_id)
    if live:
        query = {**query, **live_grant_filter()}
    return list(db.admin_scope_grants.find(query))


def _organizations_of(db, account_id) -> list:
    return sorted({key for g in _org_grants(db, account_id) for key in (g.get("scope_keys") or [])})


def read_account_state(db, account_id: str) -> Optional[dict]:
    if not _id_ok(account_id):
        return None
    doc = db.users.find_one({"_id": ObjectId(account_id)}, {"role": 1})
    if not doc:
        return None
    now = utcnow()
    grants = []
    for grant in _org_grants(db, doc["_id"], live=False):
        if grant.get("revoked_at"):
            status = ("frozen" if grant.get("frozen") else "revoked") if grant.get("blocked") else "ended"
        elif grant.get("expires_at") and grant["expires_at"] <= now:
            status = "expired"
        else:
            status = "live"
        end = grant.get("expires_at")
        grants.append({"organization_id": (grant.get("scope_keys") or [None])[0], "status": status,
                             "valid_until": end.strftime(cg.INSTANT) if end else None})
    return {"account_id": account_id, "role": doc.get("role") or "user", "grants": grants}


def read_organization_admins(db, organization_id: str) -> Optional[dict]:
    if not _id_ok(organization_id) or not db.organizations.find_one({"_id": ObjectId(organization_id)}, {"_id": 1}):
        return None
    rows = []
    for grant in db.admin_scope_grants.find({"scope_type": role.SCOPE_TYPE, "template": role.TEMPLATE,
                                             "scope_keys": organization_id, **live_grant_filter()}):
        end = grant.get("expires_at")
        rows.append({"account_id": str(grant["user_id"]),
                     "valid_until": end.strftime(cg.INSTANT) if end else None})
    rows.sort(key=lambda row: row["account_id"])
    return {"organization_id": organization_id, "org_admins": rows}


# --------------------------------------------------------------------------- #
# Notifications and history
# --------------------------------------------------------------------------- #

def _notify(db, operation: dict, account_doc: dict, organization: dict, action: str) -> None:
    try:
        email = (account_doc.get("email") or "").strip()
        if not email:
            return
        subjects = {"revoke": "Organisaation ylläpitäjän oikeutesi on päättynyt",
                    "freeze": "Organisaation ylläpitäjän oikeutesi on jäädytetty"}
        rb._email_sender().queue_email(
            template_name="org_admin_notification.html", subject=subjects[action], recipients=[email],
            context={"action": action, "organization_name": organization.get("name") or "",
                     "account_name": account_doc.get("displayname") or account_doc.get("username") or "",
                     "decision_id": operation["intent"]["decision_id"]},
        )
        db[cg.OPERATIONS].update_one({"_id": operation["_id"]}, {"$set": {"notified_at": utcnow()}})
    except Exception:  # noqa: BLE001 - notification must not fail the operation
        logger.exception("org_admin operation=%s notification failed", operation.get("_id"))


# --------------------------------------------------------------------------- #
# Apply and end
# --------------------------------------------------------------------------- #

class _System:
    """Actor for changes Flask makes by itself; shown as such in the account history."""
    _id = "system:org_admin_sync"
    username = "järjestelmä"
    role = "system"


def desired_organizations(db, account_id) -> set:
    """Organisations where the account is owner or admin (not the protected system teams)."""
    from mielenosoitukset_fi.utils.system_teams import protected_organization

    wanted = set()
    for row in db.memberships.find({"user_id": {"$in": [account_id, str(account_id)]},
                                    "role": {"$in": sorted(role_ORG_ROLES)}}, {"organization_id": 1}):
        org_id = row.get("organization_id")
        if org_id is None or not ObjectId.is_valid(str(org_id)) or protected_organization(ObjectId(str(org_id))):
            continue
        if db.organizations.find_one({"_id": ObjectId(str(org_id))}, {"_id": 1}):
            wanted.add(str(org_id))
    return wanted


role_ORG_ROLES = ("admin", "owner")


def sync_account(db, account_id) -> dict:
    """Make the account's org_admin grants match its owner/admin memberships.

    Idempotent. Returns ``{"added": [...], "ended": [...], "role": role}``. An
    account with another site role keeps it; it still gets the grants.
    """
    account_id = ObjectId(str(account_id))
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return {"added": [], "ended": [], "role": None}
    wanted = desired_organizations(db, account_id)
    live = {key: g for g in _org_grants(db, account_id) for key in (g.get("scope_keys") or [])}
    blocked = {key: g for g in _org_grants(db, account_id, live=False)
               if g.get("blocked") and not g.get("block_lifted_at") for key in (g.get("scope_keys") or [])}
    added, ended = [], []
    now = utcnow()
    for key, grant in live.items():
        if key not in wanted:
            db.admin_scope_grants.update_one({"_id": grant["_id"], "revoked_at": None}, {"$set": {
                "revoked_at": now, "revoked_reason": "membership_ended", "ended_by": "system"}})
            ended.append(key)
    for key, grant in blocked.items():
        if key not in wanted:  # The membership role is gone: the block has done its job.
            db.admin_scope_grants.update_one({"_id": grant["_id"]}, {"$set": {"block_lifted_at": now}})
    for key in sorted(wanted - set(live) - set(blocked)):
        db.admin_scope_grants.insert_one({
            "user_id": account_id, "scope_type": role.SCOPE_TYPE, "scope_keys": [key],
            "template": role.TEMPLATE, "permissions": list(role.PERMISSIONS),
            "granted_by": "system", "granted_at": now, "expires_at": None, "revoked_at": None,
            "source": SOURCE,
        })
        added.append(key)
    remaining = _org_grants(db, account_id)
    new_role = doc.get("role")
    target = None
    if remaining and doc.get("role") in {None, "user"}:
        target = role.ROLE
    elif not remaining and doc.get("role") == role.ROLE:
        target = "user"
    if target and target != doc.get("role"):
        cg.commit_profile_change(db, doc, {"role": target}, _System, "org_admin_sync")
        new_role = target
    return {"added": added, "ended": ended, "role": new_role}


def sync_all(db) -> dict:
    """Repair pass: every account with an owner/admin membership or an org_admin grant."""
    ids = {ObjectId(str(row["user_id"])) for row in db.memberships.find(
        {"role": {"$in": list(role_ORG_ROLES)}}, {"user_id": 1}) if ObjectId.is_valid(str(row.get("user_id", "")))}
    ids |= {ObjectId(str(row["user_id"])) for row in db.admin_scope_grants.find(
        {"scope_type": role.SCOPE_TYPE, "template": role.TEMPLATE, "revoked_at": None}, {"user_id": 1})
        if ObjectId.is_valid(str(row.get("user_id", "")))}
    ids |= {row["_id"] for row in db.users.find({"role": role.ROLE}, {"_id": 1})}
    totals = {"accounts": 0, "added": 0, "ended": 0, "failed": 0}
    for account_id in sorted(ids):
        try:
            result = sync_account(db, account_id)
        except Exception:  # noqa: BLE001 - one account must not stop the pass
            logger.exception("org_admin sync failed account=%s", account_id)
            totals["failed"] += 1
            continue
        totals["accounts"] += 1
        totals["added"] += len(result["added"])
        totals["ended"] += len(result["ended"])
    return totals


def sync_if_enabled(account_id) -> None:
    """Hook for membership writes; never raises into the caller."""
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("ORG_ADMIN_ROLE_ENABLED") is not True:
            return
        from mielenosoitukset_fi.utils.database import get_database_manager

        sync_account(get_database_manager(), account_id)
    except Exception:  # noqa: BLE001
        logger.exception("org_admin sync hook failed account=%s", account_id)


def apply_org_admin_end_v1(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Revoke or freeze the account's org_admin grant for one organisation. Idempotent."""
    intent = validate_end(request)
    actor, error = rb._precheck(db, integration, idempotency_key, intent, actor_id)
    if error is not None:
        return error
    operation, replayed = rb._new_operation(db, actor, integration, idempotency_key, intent, cg._digest(intent))
    if operation is None:
        return replayed
    return rb._run_fenced(db, operation, intent, actor, integration, _end_fenced)


def _end_fenced(db, operation, intent, actor, integration, account_id, fence) -> cg.Outcome:
    doc = db.users.find_one({"_id": account_id})
    if doc is None:
        return cg._outcome_for(cg._reject(db, operation, "denied"))
    grant = db.admin_scope_grants.find_one({
        "user_id": {"$in": [account_id, str(account_id)]}, "template": role.TEMPLATE,
        "scope_keys": intent["organization_id"],
    }, sort=[("granted_at", -1)])
    if grant is None:
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))
    active = grant.get("revoked_at") is None

    def write(acting):
        current = db.admin_scope_grants.find_one({"_id": grant["_id"]})
        if current.get("revoked_at") is None:
            db.admin_scope_grants.update_one({"_id": grant["_id"], "revoked_at": None}, {"$set": {
                "revoked_at": utcnow(), "revoked_reason": END_KINDS[intent["kind"]],
                "frozen": intent["kind"] == "freeze", "blocked": True,
                "ended_by_operation": str(operation["_id"]),
            }})
        elif not current.get("blocked") and current.get("revoked_reason") == "membership_ended":
            return  # Already ended by the membership; nothing for Hallinto to end.
        document = db.users.find_one({"_id": account_id})
        if document.get("role") == role.ROLE and not _org_grants(db, account_id):
            cg.commit_profile_change(db, document, {"role": "user"}, acting, "hallinto_org_admin_ended")

    operation, failed = rb._write(db, operation, intent, actor, integration, account_id, fence,
                                  "org_admin_end_v1", write)
    if failed is not None:
        return failed

    still = db.admin_scope_grants.find_one({"_id": grant["_id"], "revoked_at": None})
    if still is not None or not cg.fence_held(db, account_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    after = db.users.find_one({"_id": account_id}) or {}
    receipt = {**cg._receipt_identity(operation), "status": "confirmed",
               "checked_at": utcnow().strftime(cg.INSTANT),
               "effective": {"account_id": intent["account_id"], "role": after.get("role") or "user",
                             "organizations": _organizations_of(db, account_id),
                             "permissions_in_organization": []}}
    stored = cg._transition(db, operation, {"pending"}, "confirmed", receipt)
    if active and stored.get("status") == "confirmed" and not stored.get("notified_at"):
        organization = db.organizations.find_one({"_id": ObjectId(intent["organization_id"])}) or {}
        _notify(db, stored, after, organization, intent["kind"])
    return cg._outcome_for(stored)
