"""The organisation right to approve its own demonstrations: Hallinto's side (decision Olivia, 22 Nov 2026).

Hallinto decides, Flask carries out. An organisation's admin or owner opens a
request case (``organization_right_request``, one open case per organisation);
Hallinto reads the open requests from the feed and then grants, refuses or ends
the right. The right is an organisation property
(``organizations.rights.approve_own_demos``, read by ``utils/org_approval_right.py``).
It adds no permission to any account and changes no account role.

Trust model as in ``org_recovery`` and ``org_admin_grants``: the caller has
already verified the mTLS workload identity, the action-bound proof (fresh MFA)
and Hallinto's attestation for the exact intent. Flask re-checks everything
itself; an attestation never overrides a rule. Every refusal to write is the same
``unsupported_scope``. Writes go through the audited client, the operation journal
(account key ``org-right:<organization id>``), the case history and the
organisation history.

Rules Flask keeps whatever Hallinto sends: only a verified organisation holds the
right and losing verified ends it at once; protected system team organisations
never hold it; one live right per organisation (a new grant needs the old one
ended or expired); a grant needs an open request case of that organisation and an
owner.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.utils import org_approval_right as right
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.scope_grants import live_grant_filter
from mielenosoitukset_fi.utils.system_teams import protected_organization
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg
from . import role_bundles as rb

GRANT_FIELDS = {"grant_id", "case_id", "decision_id", "decision_revision", "organization_id", "right",
                "valid_until", "expected_state", "authority_attestation"}
REFUSAL_FIELDS = {"refusal_id", "case_id", "organization_id", "right", "decision_id", "decision_revision",
                  "reason", "authority_attestation"}
END_FIELDS = {"grant_id", "kind", "organization_id", "right", "decision_id", "decision_revision",
              "authority_attestation"}
GRANT_APPROVAL_FIELDS = GRANT_FIELDS - {"expected_state", "authority_attestation"}
REFUSAL_APPROVAL_FIELDS = REFUSAL_FIELDS - {"authority_attestation"}
END_APPROVAL_FIELDS = END_FIELDS - {"authority_attestation"}
END_KINDS = {"revoke": "revoked", "freeze": "frozen"}
NOTE_LIMIT = 300
FEED_LIMIT = 200
OUTCOMES = ("granted", "refused", "withdrawn")


def is_right_fields(fields: Any) -> bool:
    return isinstance(fields, dict) and "right" in fields and fields.get("right") == right.RIGHT


def field_kind(fields: dict) -> Optional[str]:
    """grant | refusal | end, from the exact field set; None when it is none of them."""
    if set(fields) == GRANT_APPROVAL_FIELDS:
        return "grant"
    if set(fields) == REFUSAL_APPROVAL_FIELDS:
        return "refusal"
    if set(fields) == END_APPROVAL_FIELDS:
        return "end"
    return None


# --------------------------------------------------------------------------- #
# Intents
# --------------------------------------------------------------------------- #

def _id_ok(value) -> bool:
    return isinstance(value, str) and bool(cg.ACCOUNT_ID.match(value))


def _key_ok(value) -> bool:
    return isinstance(value, str) and bool(cg.IDEMPOTENCY_KEY.match(value))


def _decision_ok(request: dict) -> bool:
    revision = request["decision_revision"]
    attestation = request["authority_attestation"]
    return bool(cg._non_empty_id(request["decision_id"]) and type(revision) is int and revision >= 1
                and isinstance(attestation, str) and 1 <= len(attestation) <= 16384
                and request["right"] == right.RIGHT and _id_ok(request["organization_id"]))


def _line_ok(value, limit=NOTE_LIMIT) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= limit and value == value.strip()
            and "\n" not in value and "\r" not in value)


def _valid_until(request: dict, now=None) -> Optional[Any]:
    end = cg.parse_valid_until(request["valid_until"])
    now = now or utcnow()
    return end if end is not None and now < end <= now + cg.MAX_VALIDITY else None


def validate_grant(request: Any) -> Optional[dict]:
    if not isinstance(request, dict) or set(request) != GRANT_FIELDS:
        return None
    if not (_decision_ok(request) and _key_ok(request["grant_id"]) and _id_ok(request["case_id"])
            and cg._non_empty_id(request["expected_state"]) and _valid_until(request) is not None):
        return None
    return {key: request[key] for key in sorted(GRANT_FIELDS - {"authority_attestation"})}


def validate_refusal(request: Any) -> Optional[dict]:
    if not isinstance(request, dict) or set(request) != REFUSAL_FIELDS:
        return None
    if not (_decision_ok(request) and _key_ok(request["refusal_id"]) and _id_ok(request["case_id"])
            and _line_ok(request["reason"])):
        return None
    return {key: request[key] for key in sorted(REFUSAL_FIELDS - {"authority_attestation"})}


def validate_end(request: Any) -> Optional[dict]:
    if not isinstance(request, dict) or set(request) != END_FIELDS:
        return None
    if not (_decision_ok(request) and _key_ok(request["grant_id"]) and request["kind"] in END_KINDS):
        return None
    return {key: request[key] for key in sorted(END_FIELDS - {"authority_attestation"})}


def grant_digest(request: Any) -> Optional[str]:
    intent = validate_grant(request)
    return cg._digest(intent) if intent is not None else None


def refusal_digest(request: Any) -> Optional[str]:
    intent = validate_refusal(request)
    return cg._digest(intent) if intent is not None else None


def end_digest(request: Any) -> Optional[str]:
    intent = validate_end(request)
    return cg._digest(intent) if intent is not None else None


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

def _admin_ids(db, organization_id: ObjectId) -> list:
    """Accounts that administer the organisation: live org_admin grants and owners/admins (transition)."""
    ids = {str(g["user_id"]) for g in db.admin_scope_grants.find(
        {"scope_type": "organization", "template": "org_admin_v1", "scope_keys": str(organization_id),
         **live_grant_filter()}, {"user_id": 1})}
    ids |= {str(m["user_id"]) for m in db.memberships.find(
        {"organization_id": {"$in": [organization_id, str(organization_id)]}, "role": {"$in": ["owner", "admin"]}},
        {"user_id": 1})}
    return sorted(ids)


def _owner_exists(db, organization_id: ObjectId) -> bool:
    return db.memberships.count_documents(
        {"organization_id": {"$in": [organization_id, str(organization_id)]}, "role": "owner"}, limit=1) == 1


def expected_state(db, organization: dict) -> str:
    row = right.record(organization) or {}
    return cg._digest({
        "organization": str(organization["_id"]), "verified": organization.get("verified") is True,
        "protected": protected_organization(organization["_id"]),
        "right": {"grant_id": row.get("grant_id"), "status": row.get("status")},
        "admins": _admin_ids(db, organization["_id"]),
    })


def _open_request(db, organization_id: ObjectId) -> Optional[dict]:
    return db.cases.find_one({"type": right.CASE_TYPE, "organization_id": organization_id,
                              "meta.closed": {"$ne": True}}, sort=[("created_at", -1)])


def _iso(value) -> Optional[str]:
    return value.strftime(cg.INSTANT) if value else None


def read_state(db, organization_id: str) -> Optional[dict]:
    """The state read of one organisation; None when it does not exist."""
    if not _id_ok(organization_id):
        return None
    org = db.organizations.find_one({"_id": ObjectId(organization_id)})
    if org is None:
        return None
    row = right.record(org) or {}
    status = right.status_of(org)
    waiting = _open_request(db, org["_id"])
    ended = row.get("ended_at")
    return {"organization_id": organization_id, "verified": org.get("verified") is True, "right": right.RIGHT,
            "status": status, "grant_id": row.get("grant_id"), "valid_until": _iso(row.get("valid_until")),
            "ended_at": _iso(ended), "ended_reason": row.get("ended_reason"),
            "open_request_case_id": str(waiting["_id"]) if waiting else None}


def request_feed(db, status: str = "open") -> Optional[dict]:
    """Hallinto's feed of right requests; None for an unknown status."""
    if status not in ("open", "closed"):
        return None
    closed = status == "closed"
    rows = []
    cursor = db.cases.find({"type": right.CASE_TYPE, "meta.closed": True if closed else {"$ne": True}}
                           ).sort("created_at", -1).limit(FEED_LIMIT)
    for case in cursor:
        meta = case.get("meta") or {}
        org = db.organizations.find_one({"_id": case.get("organization_id")}, {"name": 1, "verified": 1}) or {}
        row = {"case_id": str(case["_id"]), "organization_id": str(case.get("organization_id") or ""),
               "organization_name": org.get("name") or "", "verified": org.get("verified") is True,
               "right": right.RIGHT, "requested_at": _iso(case.get("created_at")),
               "requested_by": str(meta.get("requested_by") or ""), "note": meta.get("note") or ""}
        if closed:
            row["outcome"] = meta.get("outcome") if meta.get("outcome") in OUTCOMES else "withdrawn"
        rows.append(row)
    return {"requests": rows}


def approval_state(db, fields: dict) -> Optional[dict]:
    """For the approval page: what is being decided, the state to sign and whether it may go ahead."""
    kind = field_kind(fields)
    if kind is None or not _id_ok(fields["organization_id"]):
        return None
    org = db.organizations.find_one({"_id": ObjectId(fields["organization_id"])})
    if org is None:
        return None
    names = []
    for account in db.users.find({"_id": {"$in": [ObjectId(a) for a in _admin_ids(db, org["_id"])]}},
                                 {"username": 1, "displayname": 1}):
        names.append(account.get("displayname") or account.get("username") or "?")
    state = {"organization_name": org.get("name") or "", "verified": org.get("verified") is True,
             "org_admins": sorted(names), "kind": kind, "eligible": True, "expected_state": expected_state(db, org),
             "note": ""}
    case = (db.cases.find_one({"_id": ObjectId(fields["case_id"])}) if kind != "end" and _id_ok(fields["case_id"])
            else None)
    state["note"] = ((case or {}).get("meta") or {}).get("note") or ""
    if kind == "grant":
        state["eligible"] = _grant_refusal(db, org, fields, case) is None
    elif kind == "refusal":
        state["eligible"] = _case_open(case, org)
    return state


# --------------------------------------------------------------------------- #
# Mail
# --------------------------------------------------------------------------- #

def _recipients(db, organization_id: ObjectId) -> list:
    ids = [ObjectId(a) for a in _admin_ids(db, organization_id)]
    return sorted({(a.get("email") or "").strip() for a in db.users.find({"_id": {"$in": ids}}, {"email": 1})
                   if (a.get("email") or "").strip()})


_SUBJECTS = {"granted": "Organisaatiollenne on myönnetty oikeus hyväksyä omat mielenosoituksensa",
             "refused": "Organisaationne hyväksymisoikeuspyyntö ei menestynyt",
             "revoked": "Organisaationne hyväksymisoikeus on peruttu",
             "frozen": "Organisaationne hyväksymisoikeus on jäädytetty"}


def _notify(db, operation: dict, organization: dict, event: str, **extra) -> None:
    """Best effort: mail failure never changes the result."""
    try:
        recipients = _recipients(db, organization["_id"])
        if not recipients:
            return
        rb._email_sender().queue_email(
            template_name="org_right_notification.html", subject=_SUBJECTS[event], recipients=recipients,
            context={"event": event, "organization_name": organization.get("name") or "",
                     "decision_id": operation["intent"]["decision_id"], **extra},
        )
        db[cg.OPERATIONS].update_one({"_id": operation["_id"]}, {"$set": {"notified_at": utcnow()}})
    except Exception:  # noqa: BLE001
        logger.exception("org right operation=%s notification failed", operation.get("_id"))


# --------------------------------------------------------------------------- #
# Journal and fence
# --------------------------------------------------------------------------- #

def _fence_key(intent: dict) -> str:
    return f"org-right:{intent['organization_id']}"


def _precheck(db, integration, idempotency_key, intent, key_field, actor_id):
    if not isinstance(integration, str) or not integration:
        return None, cg._error("denied")
    if intent is None or not _key_ok(idempotency_key):
        return None, cg._error("unsupported_scope")
    if key_field is not None and intent[key_field] != idempotency_key:
        return None, cg._error("unsupported_scope")
    actor = cg._actor_authorized(db, actor_id)
    return (actor, None) if actor is not None else (None, cg._error("denied"))


def _new_operation(db, actor, integration, idempotency_key, intent):
    now = utcnow()
    digest = cg._digest(intent)
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest, "intent": intent,
        "account_id": _fence_key(intent), "status": "pending", "phase": "allocated",
        "created_at": now, "updated_at": now, "transitions": [{"status": "pending", "at": now}],
    }
    key = {"integration": integration, "actor_id": str(actor._id), "idempotency_key": idempotency_key}
    try:
        db[cg.OPERATIONS].insert_one(operation)
    except DuplicateKeyError:
        existing = db[cg.OPERATIONS].find_one(key)
        return None, (cg._replay(db, existing, digest) if existing is not None else cg._error("unavailable"))
    cg._log(operation, "pending")
    return operation, None


def _run(db, integration, actor_id, idempotency_key, intent, key_field, fenced: Callable) -> cg.Outcome:
    actor, error = _precheck(db, integration, idempotency_key, intent, key_field, actor_id)
    if error is not None:
        return error
    operation, replayed = _new_operation(db, actor, integration, idempotency_key, intent)
    if operation is None:
        return replayed
    fence_id = operation["account_id"]
    try:
        with cg.account_fence(db, fence_id) as fence:
            return fenced(db, operation, intent, actor, integration, fence_id, fence)
    except cg.CityGrantConflict:
        current = db[cg.OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending" and current.get("phase") == "allocated":
            return cg._outcome_for(cg._reject(db, current, "stale_state"))
        return cg._outcome_for(current) if current else cg._error("unavailable")


def _refuse(db, operation, code="unsupported_scope") -> cg.Outcome:
    return cg._outcome_for(cg._reject(db, operation, code))


def _case_open(case: Optional[dict], organization: dict) -> bool:
    return bool(case and case.get("type") == right.CASE_TYPE and case.get("organization_id") == organization["_id"]
                and (case.get("meta") or {}).get("closed") is not True)


# --------------------------------------------------------------------------- #
# Grant
# --------------------------------------------------------------------------- #

def _grant_refusal(db, organization: dict, intent: dict, case: Optional[dict]) -> Optional[str]:
    """An internal reason to refuse (logged by code only), or None when the grant may go ahead."""
    if protected_organization(organization["_id"]):
        return "protected_organization"
    if organization.get("verified") is not True:
        return "not_verified"
    if right.status_of(organization) == right.LIVE:
        return "right_already_live"
    if not _case_open(case, organization):
        return "case_not_open"
    if not _owner_exists(db, organization["_id"]):
        return "no_owner"
    if _valid_until(intent) is None:
        return "valid_until_out_of_range"
    return None


def apply_grant(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Give the organisation the right (idempotent under the grant id)."""
    return _run(db, integration, actor_id, idempotency_key, validate_grant(request), "grant_id", _grant_fenced)


def _grant_fenced(db, operation, intent, actor, integration, fence_id, fence) -> cg.Outcome:
    org_id = ObjectId(intent["organization_id"])
    org = db.organizations.find_one({"_id": org_id})
    if org is None:
        return _refuse(db, operation)
    if expected_state(db, org) != intent["expected_state"]:
        return _refuse(db, operation, "stale_state")
    case = db.cases.find_one({"_id": ObjectId(intent["case_id"])})
    reason = _grant_refusal(db, org, intent, case)
    if reason:
        logger.info("org right grant operation=%s refused (%s)", operation["_id"], reason)
        return _refuse(db, operation)

    end = cg.parse_valid_until(intent["valid_until"])
    now = utcnow()
    prior = right.record(org)
    new_row = {"grant_id": intent["grant_id"], "case_id": intent["case_id"], "decision_id": intent["decision_id"],
               "decision_revision": intent["decision_revision"], "granted_at": now, "granted_by": str(actor._id),
               "valid_until": end, "status": right.LIVE, "ended_at": None, "ended_reason": None}

    def write(acting):
        guard = {"_id": org_id, "verified": True}
        guard[f"rights.{right.RIGHT}"] = {"$exists": False} if prior is None else prior
        update: dict = {"$set": {f"rights.{right.RIGHT}": new_row}}
        if prior is not None:
            update["$push"] = {f"rights_history.{right.RIGHT}": prior}
        if not db.organizations.update_one(guard, update).matched_count:
            raise cg.CityGrantConflict("the organisation changed before the right was written")
        db.cases.update_one({"_id": ObjectId(intent["case_id"]), "meta.closed": {"$ne": True}}, {
            "$set": {"meta.closed": True, "meta.closed_reason": "Hyväksymisoikeus myönnetty",
                     "meta.outcome": "granted", "meta.closed_at": now, "updated_at": now},
            "$push": {"case_history": {
                "timestamp": now, "action": f"Hyväksymisoikeus myönnetty Hallinnon päätöksellä {intent['decision_id']}",
                "user": "Hallinto", "mech_action": "org_right_granted",
                "metadata": {"decision_id": intent["decision_id"], "valid_until": intent["valid_until"]},
                "meta_schema": {"decision_id": "string", "valid_until": "string"}}}})

    operation, failed = rb._write(db, operation, intent, actor, integration, fence_id, fence, "org_right_v1", write)
    if failed is not None:
        return failed
    after = db.organizations.find_one({"_id": org_id}) or {}
    stored_row = right.record(after) or {}
    if stored_row.get("grant_id") != intent["grant_id"] or not cg.fence_held(db, fence_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {**cg._receipt_identity(operation), "status": "confirmed", "checked_at": now.strftime(cg.INSTANT),
               "effective": {"organization_id": intent["organization_id"], "right": right.RIGHT,
                             "status": right.status_of(after), "valid_until": intent["valid_until"],
                             "verified": after.get("verified") is True}}
    stored = cg._transition(db, operation, {"pending"}, "confirmed", receipt)
    if stored.get("status") == "confirmed" and not stored.get("notified_at"):
        _notify(db, stored, after, "granted", valid_until=intent["valid_until"])
    return cg._outcome_for(stored)


# --------------------------------------------------------------------------- #
# Refusal
# --------------------------------------------------------------------------- #

def apply_refusal(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Close the request case as refused, with the reason for the organisation."""
    return _run(db, integration, actor_id, idempotency_key, validate_refusal(request), "refusal_id", _refusal_fenced)


def _refusal_fenced(db, operation, intent, actor, integration, fence_id, fence) -> cg.Outcome:
    org_id = ObjectId(intent["organization_id"])
    org = db.organizations.find_one({"_id": org_id})
    case = db.cases.find_one({"_id": ObjectId(intent["case_id"])})
    if org is None or not case or case.get("type") != right.CASE_TYPE or case.get("organization_id") != org_id:
        return _refuse(db, operation)
    meta = case.get("meta") or {}
    now = utcnow()
    if meta.get("closed") is True:
        # Already closed: a confirmed no-op that tells what really happened.
        outcome = meta.get("outcome") if meta.get("outcome") in OUTCOMES else "withdrawn"
        receipt = {**cg._receipt_identity(operation), "status": "confirmed", "checked_at": now.strftime(cg.INSTANT),
                   "effective": {"case_id": intent["case_id"], "organization_id": intent["organization_id"],
                                 "outcome": outcome}}
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))

    def write(acting):
        db.cases.update_one({"_id": case["_id"], "meta.closed": {"$ne": True}}, {
            "$set": {"meta.closed": True, "meta.closed_reason": intent["reason"], "meta.outcome": "refused",
                     "meta.closed_at": now, "updated_at": now},
            "$push": {"case_history": {
                "timestamp": now, "action": f"Hyväksymisoikeuspyyntö hylätty Hallinnon päätöksellä {intent['decision_id']}",
                "user": "Hallinto", "mech_action": "org_right_refused",
                "metadata": {"decision_id": intent["decision_id"], "reason": intent["reason"]},
                "meta_schema": {"decision_id": "string", "reason": "string"}}}})

    operation, failed = rb._write(db, operation, intent, actor, integration, fence_id, fence, "org_right_refusal_v1", write)
    if failed is not None:
        return failed
    current = db.cases.find_one({"_id": case["_id"]}) or {}
    if (current.get("meta") or {}).get("closed") is not True or not cg.fence_held(db, fence_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {**cg._receipt_identity(operation), "status": "confirmed", "checked_at": now.strftime(cg.INSTANT),
               "effective": {"case_id": intent["case_id"], "organization_id": intent["organization_id"],
                             "outcome": "refused"}}
    stored = cg._transition(db, operation, {"pending"}, "confirmed", receipt)
    if stored.get("status") == "confirmed" and not stored.get("notified_at"):
        _notify(db, stored, org, "refused", reason=intent["reason"])
    return cg._outcome_for(stored)


# --------------------------------------------------------------------------- #
# End
# --------------------------------------------------------------------------- #

def apply_end(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Revoke or freeze the grant's right. Idempotent; a newer grant is never touched."""
    return _run(db, integration, actor_id, idempotency_key, validate_end(request), None, _end_fenced)


def _end_fenced(db, operation, intent, actor, integration, fence_id, fence) -> cg.Outcome:
    org_id = ObjectId(intent["organization_id"])
    org = db.organizations.find_one({"_id": org_id})
    if org is None:
        return _refuse(db, operation)
    row = right.record(org) or {}
    older = [r for r in ((org.get("rights_history") or {}).get(right.RIGHT) or []) if r.get("grant_id") == intent["grant_id"]]
    if row.get("grant_id") != intent["grant_id"]:
        if not older:
            return _refuse(db, operation)
        # A grant that a newer one replaced: nothing of it is left to end.
        receipt = {**cg._receipt_identity(operation), "status": "confirmed",
                   "checked_at": utcnow().strftime(cg.INSTANT),
                   "effective": {"organization_id": intent["organization_id"], "right": right.RIGHT,
                                 "status": older[-1].get("status") if older[-1].get("status") != right.LIVE else "revoked"}}
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))
    was_live = row.get("status") == right.LIVE
    now = utcnow()

    def write(acting):
        if was_live:
            db.organizations.update_one(
                {"_id": org_id, f"rights.{right.RIGHT}.grant_id": intent["grant_id"],
                 f"rights.{right.RIGHT}.status": right.LIVE},
                {"$set": {f"rights.{right.RIGHT}.status": END_KINDS[intent["kind"]],
                          f"rights.{right.RIGHT}.ended_at": now,
                          f"rights.{right.RIGHT}.ended_reason": right.END_REASONS[END_KINDS[intent["kind"]]]}})

    operation, failed = rb._write(db, operation, intent, actor, integration, fence_id, fence, "org_right_end_v1", write)
    if failed is not None:
        return failed
    after = db.organizations.find_one({"_id": org_id}) or {}
    status = right.status_of(after)
    if status == right.LIVE or not cg.fence_held(db, fence_id, fence):
        return cg._outcome_for(cg._transition(db, operation, {"pending"}, "reconciliation_required"))
    receipt = {**cg._receipt_identity(operation), "status": "confirmed", "checked_at": now.strftime(cg.INSTANT),
               "effective": {"organization_id": intent["organization_id"], "right": right.RIGHT, "status": status}}
    stored = cg._transition(db, operation, {"pending"}, "confirmed", receipt)
    if was_live and stored.get("status") == "confirmed" and not stored.get("notified_at"):
        _notify(db, stored, after, END_KINDS[intent["kind"]])
    return cg._outcome_for(stored)


# --------------------------------------------------------------------------- #
# Requests and giving up (Flask side, by the organisation's own admins)
# --------------------------------------------------------------------------- #

def open_request(db, organization: dict, user, note: str = "") -> tuple:
    """Open the request case. Returns (case id or None, error code or None)."""
    if not right.enabled():
        return None, "disabled"
    if protected_organization(organization["_id"]) or organization.get("verified") is not True:
        return None, "not_verified"
    status = right.status_of(organization)
    if status == right.LIVE:
        return None, "already_live"
    if status == "frozen":
        return None, "frozen"
    note = (note or "").strip()
    if note and not _line_ok(note):
        return None, "bad_note"
    if _open_request(db, organization["_id"]) is not None:
        return None, "already_open"

    from mielenosoitukset_fi.utils.classes import Case

    now = utcnow()
    name = getattr(user, "displayname", None) or getattr(user, "username", "") or ""
    case = Case(case_type=right.CASE_TYPE, organization_id=organization["_id"],
                submitter_id=getattr(user, "_id", None), submitter={"submitter_name": name},
                meta={"right": right.RIGHT, "requested_by": str(getattr(user, "_id", "")), "note": note,
                      "urgency": "normal"},
                case_history=[{"timestamp": now, "action": "Hyväksymisoikeutta pyydetty",
                               "user": getattr(user, "username", "") or "", "mech_action": "org_right_requested",
                               "metadata": {}, "meta_schema": {}}])
    db.cases.insert_one(case.to_dict())
    return str(case._id), None


def give_up(db, organization: dict) -> bool:
    """The organisation gives its live right up. True when it was live and is ended."""
    if right.status_of(organization) != right.LIVE:
        return False
    return right.end_right(db, organization["_id"], "given_up")
