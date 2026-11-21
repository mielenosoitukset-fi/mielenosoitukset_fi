"""Access recovery for organizations, decided in Hallinto and carried out here (O5).

An organization lost its owner, or wants a new owner or member. Hallinto
decides. Flask never grants access directly: it creates one single-use
invitation, through the existing invitation machinery, to an existing active
account and mails it. Only when that person accepts do they become a member;
for an owner change the earlier owners then become `admin` (never removed).
If nobody accepts, nothing changes.

The caller has already verified the mTLS workload identity, the action-bound
proof (fresh MFA) and Hallinto's attestation for the exact intent. Flask
re-checks the actor, the organization, the person and the state here; an
attestation never overrides them.

Privacy rules: the person's address is never stored in the journal, never
logged and never echoed in a receipt (only its SHA-256). Every refusal is the
same `unsupported_scope`, so a refused call never says why: not whether an
account exists, nor whether it is an admin.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any, Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg
from . import org_outreach

NEEDS = ("owner_change", "add_member", "lost_access")
ROLES = ("owner", "admin", "member")
RANK = {"member": 0, "admin": 1, "owner": 2}
LIFETIME = timedelta(days=14)
SITE_URL = "https://mielenosoitukset.fi"
FIELDS = {"request_id", "case_id", "decision_id", "decision_revision", "need", "organization_id",
          "person_email", "person_role", "expected_state", "authority_attestation"}


def _plain_address(value: Any) -> bool:
    return isinstance(value, str) and value == value.strip().lower() and org_outreach.valid_address(value)


def validate_recovery(request: Any) -> Optional[dict]:
    """Canonical intent (attestation excluded), or None for an unsupported shape."""
    if not isinstance(request, dict) or set(request) != FIELDS:
        return None
    revision, need, role = request["decision_revision"], request["need"], request["person_role"]
    attestation = request["authority_attestation"]
    if not (isinstance(request["request_id"], str) and cg.IDEMPOTENCY_KEY.match(request["request_id"])
            and cg._non_empty_id(request["case_id"]) and cg._non_empty_id(request["decision_id"])
            and type(revision) is int and revision >= 1
            and need in NEEDS
            and isinstance(request["organization_id"], str) and cg.ACCOUNT_ID.match(request["organization_id"])
            and _plain_address(request["person_email"])
            # The need fixes the role, except add_member, which names admin or member.
            and ((need == "add_member" and role in ("admin", "member"))
                 or (need != "add_member" and role == "owner"))
            and cg._non_empty_id(request["expected_state"])
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384):
        return None
    return {key: request[key] for key in sorted(FIELDS - {"authority_attestation"})}


def recovery_digest(request: Any) -> Optional[str]:
    intent = validate_recovery(request)
    return cg._digest(intent) if intent is not None else None


def _email_hash(address: str) -> str:
    return hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

def _memberships(db, organization_id: ObjectId) -> list:
    return list(db.memberships.find({"organization_id": {"$in": [organization_id, str(organization_id)]}}))


def _invitation_view(invitation: Any) -> Any:
    """What an open invitation contributes to the state: never an address."""
    if not isinstance(invitation, dict):
        return {"legacy": hashlib.sha256(str(invitation).lower().encode()).hexdigest()[:16]}
    return {"email": _email_hash(invitation.get("email") or ""), "role": invitation.get("role"),
            "id": invitation.get("invitation_id"), "request": invitation.get("recovery_request_id")}


def read_state(db, organization_id: Any) -> Optional[dict]:
    """The organization's access state; None when it does not exist."""
    if not isinstance(organization_id, str) or not cg.ACCOUNT_ID.match(organization_id):
        return None
    from mielenosoitukset_fi.utils.system_teams import protected_organization

    org = db.organizations.find_one({"_id": ObjectId(organization_id)})
    if not org:
        return None
    rows = _memberships(db, org["_id"])
    inputs = {
        "organization": organization_id,
        "protected": protected_organization(org["_id"]),
        "members": sorted((str(r["user_id"]), r.get("role")) for r in rows),
        "invitations": sorted(json_key(_invitation_view(i)) for i in org.get("invitations") or []),
    }
    owners = sorted(str(r["user_id"]) for r in rows if r.get("role") == "owner")
    return {"organization_id": organization_id, "name": org.get("name") or "", "owners": owners,
            "protected": inputs["protected"], "expected_state": cg._digest(inputs)}


def json_key(value: Any) -> str:
    return cg._digest(value)


def _find_person(db, address: str) -> Optional[dict]:
    from mielenosoitukset_fi.utils.validators import normalize_email

    canonical = normalize_email(address)
    return db.users.find_one({"email_canonical": canonical}) or db.users.find_one(
        {"email": {"$regex": "^" + re.escape(address) + "$", "$options": "i"}})


def _person_ok(db, doc: Optional[dict]) -> bool:
    """A plain, active account: no site-wide rights, no city grant, no team access."""
    from mielenosoitukset_fi.admin import role_bundles
    from mielenosoitukset_fi.utils.system_teams import active_account

    if not doc or not active_account(doc):
        return False
    inputs = role_bundles._inputs(db, doc)
    role, permissions = role_bundles._base_access(doc)
    return (role in {None, "user"} and not permissions and not inputs["global_admin"]
            and not inputs["global_permissions"] and not inputs["protected"]
            and not inputs["team_access"] and not inputs["grants"])


def refusal(db, intent: dict, state: Optional[dict]) -> Optional[str]:
    """An internal reason to refuse (logged by code only), or None when it may go ahead."""
    if state is None:
        return "organization_missing"
    if state["protected"]:
        return "protected_organization"
    person = _find_person(db, intent["person_email"])
    if not _person_ok(db, person):
        return "person_ineligible"
    rows = _memberships(db, ObjectId(intent["organization_id"]))
    mine = next((r for r in rows if str(r["user_id"]) == str(person["_id"])), None)
    if mine and RANK.get(mine.get("role"), 0) >= RANK[intent["person_role"]]:
        return "already_holds_role"
    org = db.organizations.find_one({"_id": ObjectId(intent["organization_id"])}, {"invitations": 1}) or {}
    wanted = {person["email"].lower(), intent["person_email"]}
    for invitation in org.get("invitations") or []:
        email = invitation.get("email") if isinstance(invitation, dict) else invitation
        if isinstance(email, str) and email.lower() in wanted:
            return "invitation_open"
    if (intent["need"] != "lost_access"
            and db[org_outreach.OPTOUTS].find_one({"address_hash": org_outreach.address_hash(intent["person_email"])})):
        return "opted_out"
    return None


def approval_state(db, fields: dict) -> Optional[dict]:
    """For the approval page: the state to sign, whether it may go ahead, and who is replaced."""
    intent = {**fields, "expected_state": "-", "authority_attestation": "-"}
    if validate_recovery(intent) is None:
        return None
    state = read_state(db, fields["organization_id"])
    if state is None:
        return None
    ok = refusal(db, intent, state) is None
    replaced = len(state["owners"]) if fields["need"] != "add_member" else 0
    return {**state, "eligible": ok, "replaced_owners": replaced}


# --------------------------------------------------------------------------- #
# Mail
# --------------------------------------------------------------------------- #

_SENDER = None


def _email_sender():
    global _SENDER
    if _SENDER is None:
        from mielenosoitukset_fi.emailer.EmailSender import EmailSender
        _SENDER = EmailSender()
    return _SENDER


def _queue_mail(address: str, organization: dict) -> None:
    from flask import url_for

    path = url_for("users.user_orgs.accept_invite", organization_id=str(organization["_id"]))
    _email_sender().queue_email(
        template_name="invite_to_organization.html",
        subject=f"Kutsu liittyä organisaatioon {organization.get('name')}",
        recipients=[address],
        context={"invite_url": f"{SITE_URL}{path}",
                 "inviter_name": "Mielenosoitukset.fi", "organization_name": organization.get("name")},
    )


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def apply_recovery(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Create the single-use invitation of one decided recovery, idempotently under its request id."""
    if not isinstance(integration, str) or not integration:
        return cg._error("denied")
    intent = validate_recovery(request)
    if intent is None or intent["request_id"] != idempotency_key or not cg.IDEMPOTENCY_KEY.match(idempotency_key):
        return cg._error("unsupported_scope")
    actor = cg._actor_authorized(db, actor_id)
    if actor is None:
        return cg._error("denied")

    digest = cg._digest(intent)
    now = utcnow()
    stored = {k: v for k, v in intent.items() if k != "person_email"}
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest,
        # The address is kept out of the journal: only its hash.
        "intent": {**stored, "person_email_sha256": _email_hash(intent["person_email"])},
        "account_id": f"org-recovery:{intent['organization_id']}", "status": "pending", "phase": "allocated",
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
        with cg.account_fence(db, operation["account_id"]):
            return _apply_fenced(db, operation, intent, actor)
    except cg.CityGrantConflict:
        current = db[cg.OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending":
            return cg._outcome_for(cg._reject(db, current, "stale_state"))
        return cg._outcome_for(current) if current else cg._error("unavailable")


def _apply_fenced(db, operation: dict, intent: dict, actor) -> cg.Outcome:
    from mielenosoitukset_fi.utils.audit_trail import audit_scope

    state = read_state(db, intent["organization_id"])
    if state is not None and state["expected_state"] != intent["expected_state"]:
        return cg._outcome_for(cg._reject(db, operation, "stale_state"))
    reason = refusal(db, intent, state)
    if reason:
        logger.info("org recovery operation=%s refused (%s)", operation["_id"], reason)
        return cg._outcome_for(cg._reject(db, operation, "unsupported_scope"))

    now = utcnow()
    person = _find_person(db, intent["person_email"])
    org_id = ObjectId(intent["organization_id"])
    organization = db.organizations.find_one({"_id": org_id})
    invitation_id = str(ObjectId())
    replaces = state["owners"] if intent["need"] != "add_member" else []
    expires = now + LIFETIME
    invitation = {
        # The stored address of the account, so the accept step's exact match holds.
        "email": person["email"], "invited_at": now, "expires_at": expires, "role": intent["person_role"],
        # The Hallinto decision is the grant; Flask's role check reads this marker.
        "role_granted_by": f"hallinto:{intent['case_id']}",
        "invitation_id": invitation_id, "recovery_request_id": intent["request_id"],
        "replaces_owners": replaces, "operation_id": str(operation["_id"]),
    }
    try:
        with audit_scope("integration", operation["integration"], actor={"kind": "user", "user_id": str(actor._id)},
                         operation_id=str(operation["_id"])):
            pushed = db.organizations.update_one(
                {"_id": org_id, "invitations": {"$not": {"$elemMatch": {"email": person["email"]}}}},
                {"$push": {"invitations": invitation}})
    except PyMongoError:
        logger.exception("org recovery operation=%s write failed", operation["_id"])
        return cg._outcome_for(cg._reject(db, operation, "stale_state"))
    if not pushed.modified_count:
        return cg._outcome_for(cg._reject(db, operation, "stale_state"))
    try:
        _queue_mail(person["email"], organization)
    except Exception:  # noqa: BLE001
        # Left pending: a retry reports reconciliation_required and never resends.
        logger.exception("org recovery operation=%s mail not queued", operation["_id"])
        return cg._error("unavailable")
    receipt = {
        **cg._receipt_identity(operation), "status": "confirmed", "checked_at": now.strftime(cg.INSTANT),
        "effective": {"organization_id": intent["organization_id"], "invitation_id": invitation_id,
                      "role": intent["person_role"], "expires_at": expires.strftime(cg.INSTANT),
                      "replaces_owners_count": len(replaces),
                      "person_email_sha256": operation["intent"]["person_email_sha256"]},
    }
    return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))


# --------------------------------------------------------------------------- #
# Accept (called by the invitation accept routes)
# --------------------------------------------------------------------------- #

def on_accept(db, organization_id: Any, invitation: Any, user_id: Any) -> None:
    """After an invitation was accepted: finish a recovery. A normal invitation does nothing.

    Raises a member to the invited role when they were already one, and for an
    owner change turns the earlier owners into admins. Nobody is removed.
    """
    if not isinstance(invitation, dict) or not invitation.get("recovery_request_id"):
        return
    try:
        org_id, uid = ObjectId(str(organization_id)), ObjectId(str(user_id))
        role = invitation.get("role")
        if role not in RANK or not invitation.get("role_granted_by"):
            return
        both = {"$in": [org_id, str(org_id)]}
        mine = db.memberships.find_one({"organization_id": both, "user_id": {"$in": [uid, str(uid)]}})
        if mine and RANK.get(mine.get("role"), 0) < RANK[role]:
            db.memberships.update_one({"_id": mine["_id"]}, {"$set": {"role": role}})
        if role == "owner":
            old = [ObjectId(o) for o in invitation.get("replaces_owners") or [] if ObjectId.is_valid(str(o))]
            db.memberships.update_many(
                {"organization_id": both, "role": "owner", "user_id": {"$in": [*old, *map(str, old)], "$nin": [uid, str(uid)]}},
                {"$set": {"role": "admin"}})
        operation_id = invitation.get("operation_id")
        if operation_id and ObjectId.is_valid(str(operation_id)):
            db[cg.OPERATIONS].update_one(
                {"_id": ObjectId(str(operation_id))},
                {"$set": {"receipt.effective.accepted_at": utcnow().strftime(cg.INSTANT)}})
    except Exception:  # noqa: BLE001 - acceptance already succeeded; a repair is a Hallinto order
        logger.exception("org recovery accept follow-up failed organization=%s", organization_id)
