"""Outbound organization invitations decided in Hallinto and sent by Flask (O6).

A person in Hallinto decides to invite an organization to use the site. Flask
sends one plain Finnish mail to the organization's public address, with a link
to the request form and a one-click opt-out. The caller has already verified
the mTLS workload identity, the action-bound proof (fresh MFA) and Hallinto's
attestation for the exact intent. Flask re-checks the actor, the address, the
opt-out list and the limits here; an attestation never overrides them.

Privacy rules: an address is never stored in plain text beside the invitation
(only its SHA-256), never logged, and never echoed in a receipt.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlsplit

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg

INVITATIONS = "org_invitations"
OPTOUTS = "outreach_optouts"
RATE = "outreach_rate"
SITE_URL = "https://mielenosoitukset.fi"

FIELDS = {"invitation_id", "case_id", "decision_id", "decision_revision", "organization_name",
          "address", "source_url", "authority_attestation"}
OPTIONAL = {"note"}
ADDRESS = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
# Addresses that are role boxes or automatic: never invited.
BLOCKED_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "abuse", "postmaster", "mailer-daemon",
                 "root", "hostmaster", "webmaster", "security", "spam"}

TOKEN_TTL = timedelta(days=30)
PER_ADDRESS = timedelta(days=90)
PER_ORGANIZATION = timedelta(days=30)
SITE_PER_DAY = 20
VERIFIER_PER_DAY = 3
CHECK_PER_MINUTE = 60


def _plain(text: Any, low: int, high: int) -> bool:
    return (isinstance(text, str) and low <= len(text) <= high and text == text.strip()
            and not any(ord(c) < 0x20 or ord(c) == 0x7F or c in "  �" for c in text))


def address_hash(address: str) -> str:
    return hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()


def organization_key(name: str) -> str:
    return " ".join(name.casefold().split())


def valid_address(address: Any) -> bool:
    return bool(isinstance(address, str) and len(address) <= 254 and ADDRESS.match(address))


def validate_invitation(request: Any) -> Optional[dict]:
    """Canonical intent (attestation excluded), or None for an unsupported shape."""
    if not isinstance(request, dict) or FIELDS - set(request) or set(request) - FIELDS - OPTIONAL:
        return None
    revision = request["decision_revision"]
    attestation = request["authority_attestation"]
    source = request["source_url"]
    if not (isinstance(request["invitation_id"], str) and cg.IDEMPOTENCY_KEY.match(request["invitation_id"])
            and cg._non_empty_id(request["case_id"]) and cg._non_empty_id(request["decision_id"])
            and type(revision) is int and revision >= 1
            and _plain(request["organization_name"], 2, 120)
            and valid_address(request["address"])
            and _plain(source, 10, 500)
            and ("note" not in request or _plain(request["note"], 1, 300))
            and isinstance(attestation, str) and 1 <= len(attestation) <= 16384):
        return None
    parts = urlsplit(source)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or " " in source:
        return None
    return {key: request[key] for key in sorted((FIELDS | OPTIONAL) - {"authority_attestation"}) if key in request}


def invitation_digest(request: Any) -> Optional[str]:
    intent = validate_invitation(request)
    return cg._digest(intent) if intent is not None else None


def ensure_indexes(db) -> None:
    db[INVITATIONS].create_index([("address_hash", 1), ("created_at", -1)], name="org_invitation_address")
    db[INVITATIONS].create_index([("token_hash", 1)], unique=True, name="org_invitation_token")
    db[INVITATIONS].create_index([("optout_token_hash", 1)], unique=True, name="org_invitation_optout")
    db[OPTOUTS].create_index([("address_hash", 1)], unique=True, name="outreach_optout_address")
    db[RATE].create_index([("name", 1), ("bucket", 1)], unique=True, name="outreach_rate_bucket")
    db[RATE].create_index([("expires_at", 1)], expireAfterSeconds=0, name="outreach_rate_expiry")


def _paused(db) -> bool:
    panic = db.panic.find_one({"name": "global"})
    return bool(panic and panic.get("panic"))


def _under_rate(db, name: str, bucket: str, limit: int, ttl: timedelta) -> bool:
    """Count one use of ``name`` in ``bucket``; False when the bucket is full."""
    try:
        row = db[RATE].find_one_and_update(
            {"name": name, "bucket": bucket},
            {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": utcnow() + ttl}},
            upsert=True, return_document=True,
        )
    except DuplicateKeyError:
        row = db[RATE].find_one_and_update({"name": name, "bucket": bucket}, {"$inc": {"count": 1}},
                                           return_document=True)
    return bool(row and row["count"] <= limit)


def check_address(db, address: str) -> Optional[str]:
    """Reason an address may not be invited (without account lookups), or None.

    ``suppressed`` (opt-out), ``invalid_address`` (role or automatic box).
    """
    if not valid_address(address) or address.split("@", 1)[0] in BLOCKED_LOCAL:
        return "invalid_address"
    if db[OPTOUTS].find_one({"address_hash": address_hash(address)}, {"_id": 1}):
        return "suppressed"
    return None


def _account_uses(db, address: str) -> bool:
    return db.users.count_documents({"$or": [{"email": address}, {"email_canonical": address}]}, limit=1) > 0


def _limit_reason(db, intent: dict, actor_id: str, now) -> Optional[str]:
    h = address_hash(intent["address"])
    if db[INVITATIONS].count_documents({"address_hash": h, "created_at": {"$gt": now - PER_ADDRESS}}, limit=1):
        return "limit"
    if db[INVITATIONS].count_documents(
            {"organization_key": organization_key(intent["organization_name"]),
             "created_at": {"$gt": now - PER_ORGANIZATION}}, limit=1):
        return "limit"
    day = now.strftime("%Y-%m-%d")
    if not _under_rate(db, "site", day, SITE_PER_DAY, timedelta(days=2)):
        return "limit"
    if not _under_rate(db, f"verifier:{actor_id}", day, VERIFIER_PER_DAY, timedelta(days=2)):
        return "limit"
    return None


def read_check(db, address: Any) -> dict:
    """Read-only answer for Hallinto before it drafts an invitation.

    Never reveals whether an account uses the address.
    """
    now = utcnow()
    if not _under_rate(db, "check", now.strftime("%Y-%m-%dT%H:%M"), CHECK_PER_MINUTE, timedelta(minutes=5)):
        return {"sendable": False, "reason": "rate_limited"}
    reason = check_address(db, address) if isinstance(address, str) else "invalid_address"
    if reason is None and db[INVITATIONS].count_documents(
            {"address_hash": address_hash(address), "created_at": {"$gt": now - PER_ADDRESS}}, limit=1):
        reason = "limit"
    return {"sendable": reason is None, "reason": reason}


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


def _links(token: str, optout_token: str) -> tuple[str, str]:
    return (f"{SITE_URL}/organisaatiot/hae?kutsu={token}", f"{SITE_URL}/organisaatiot/ei-kutsuja?t={optout_token}")


def _queue_mail(intent: dict, token: str, optout_token: str) -> None:
    form_url, optout_url = _links(token, optout_token)
    _email_sender().queue_email(
        template_name="org_invitation.html",
        subject="Kutsu käyttämään Mielenosoitukset.fi-palvelua",
        recipients=[intent["address"]],
        context={"organization_name": intent["organization_name"], "form_url": form_url,
                 "optout_url": optout_url, "source_url": intent["source_url"], "note": intent.get("note", "")},
        extra_headers={"List-Unsubscribe": f"<{optout_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )


# --------------------------------------------------------------------------- #
# Send
# --------------------------------------------------------------------------- #

def apply_invitation(db, *, integration: str, actor_id, idempotency_key: str, request: Any) -> cg.Outcome:
    """Send one invitation, idempotently under its own id. Never resends on a retry."""
    if not isinstance(integration, str) or not integration:
        return cg._error("denied")
    intent = validate_invitation(request)
    if intent is None or intent["invitation_id"] != idempotency_key or not cg.IDEMPOTENCY_KEY.match(idempotency_key):
        return cg._error("unsupported_scope")
    actor = cg._actor_authorized(db, actor_id)
    if actor is None:
        return cg._error("denied")

    digest = cg._digest(intent)
    h = address_hash(intent["address"])
    now = utcnow()
    operation = {
        "_id": ObjectId(), "integration": integration, "actor_id": str(actor._id),
        "idempotency_key": idempotency_key, "intent_digest": digest,
        # The address is kept out of the journal: only its hash.
        "intent": {**{k: v for k, v in intent.items() if k != "address"}, "address_sha256": h},
        "account_id": f"outreach:{h}", "status": "pending", "phase": "allocated",
        "created_at": now, "updated_at": now, "transitions": [{"status": "pending", "at": now}],
    }
    key = {"integration": integration, "actor_id": str(actor._id), "idempotency_key": idempotency_key}
    try:
        db[cg.OPERATIONS].insert_one(operation)
    except DuplicateKeyError:
        existing = db[cg.OPERATIONS].find_one(key)
        if existing is None:
            return cg._error("unavailable")
        # The stored intent has no address: compare by the digest of the full intent.
        return cg._replay(db, existing, digest)
    cg._log(operation, "pending")

    try:
        with cg.account_fence(db, operation["account_id"]):
            return _send_fenced(db, operation, intent, actor, h)
    except cg.CityGrantConflict:
        current = db[cg.OPERATIONS].find_one({"_id": operation["_id"]})
        if current and current["status"] == "pending":
            return cg._outcome_for(cg._reject(db, current, "stale_state"))
        return cg._outcome_for(current) if current else cg._error("unavailable")


def _send_fenced(db, operation: dict, intent: dict, actor, h: str) -> cg.Outcome:
    now = utcnow()
    status, token, expires = None, None, None
    if _paused(db):
        status = "paused"
    else:
        status = check_address(db, intent["address"]) or ("account_exists" if _account_uses(db, intent["address"])
                                                         else _limit_reason(db, intent, str(actor._id), now))
    if status is None:
        token, optout = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = now + TOKEN_TTL
        db[INVITATIONS].insert_one({
            "_id": ObjectId(), "invitation_id": intent["invitation_id"], "case_id": intent["case_id"],
            "address_hash": h, "organization_key": organization_key(intent["organization_name"]),
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "optout_token_hash": hashlib.sha256(optout.encode()).hexdigest(),
            "operation_id": str(operation["_id"]), "verifier_id": str(actor._id),
            "status": "sent", "created_at": now, "expires_at": expires,
        })
        try:
            _queue_mail(intent, token, optout)
        except Exception:  # noqa: BLE001
            # Left pending: a retry reports reconciliation_required and never resends.
            logger.exception("org invitation operation=%s mail not queued", operation["_id"])
            return cg._error("unavailable")
        status = "sent"
    receipt = {
        **cg._receipt_identity(operation), "status": "confirmed", "checked_at": now.strftime(cg.INSTANT),
        "effective": {"invitation_id": intent["invitation_id"], "status": status,
                      "expires_at": expires.strftime(cg.INSTANT) if expires else None, "address_sha256": h},
    }
    return cg._outcome_for(cg._transition(db, operation, {"pending"}, "confirmed", receipt))


# --------------------------------------------------------------------------- #
# Opt-out (public page)
# --------------------------------------------------------------------------- #

def optout_target(db, token: Any) -> Optional[dict]:
    """The invitation an opt-out token belongs to, or None."""
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    return db[INVITATIONS].find_one({"optout_token_hash": hashlib.sha256(token.encode()).hexdigest()})


def record_optout(db, token: Any) -> bool:
    """Permanently stop invitations to the token's address. Idempotent."""
    row = optout_target(db, token)
    if row is None:
        return False
    try:
        db[OPTOUTS].insert_one({"_id": ObjectId(), "address_hash": row["address_hash"], "created_at": utcnow()})
    except DuplicateKeyError:
        pass
    db[INVITATIONS].update_many({"address_hash": row["address_hash"], "status": "sent"},
                                {"$set": {"status": "opted_out"}})
    logger.info("org invitation opt-out recorded invitation=%s", row.get("invitation_id"))
    return True
