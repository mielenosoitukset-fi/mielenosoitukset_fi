"""Action-bound fresh proofs for owner grants (roadmap I02 remainder).

A proof says: this account just passed a fresh strong factor in Flask and
approved exactly this intent for this audience. It is single-use: the
first command that presents it binds it to its Idempotency-Key, and only a
retry of that same command (same key and intent) may present it again
before it expires. Sign-in time is never used as freshness.

Only the token's SHA-256 is stored. Nothing here is reachable from a route
unless ``HALLINTO_CITY_GRANT_API_ENABLED`` is on.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from bson import ObjectId
from pymongo import ReturnDocument

from mielenosoitukset_fi.utils.time_utils import utcnow

PROOFS = "owner_grant_proofs"
PROOF_TTL = timedelta(minutes=5)
# Password alone is not a fresh strong factor.
STRONG_FACTORS = {"passkey", "password_totp"}
ACTION_CITY_ADMIN_V1 = "city_admin_v1"
ACTION_CITY_ADMIN_END_V1 = "city_admin_end_v1"
ACTION_FLASK_ORDER_V1 = "flask_order_v1"
ACTION_ROLE_BUNDLE_V1 = "role_bundle_v1"
ACTION_ROLE_BUNDLE_END_V1 = "role_bundle_end_v1"
ACTION_ELEVATED_V1 = "elevated_v1"
ACTION_MODERATOR_V1 = "national_moderator_v1"
ACTION_MODERATOR_END_V1 = "national_moderator_end_v1"
ACTION_ORG_INVITATION_V1 = "org_invitation_v1"
ACTION_ORG_RECOVERY_V1 = "org_recovery_v1"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_proof_indexes(db) -> None:
    db[PROOFS].create_index("token_hash", unique=True, name="owner_grant_proof_token")
    db[PROOFS].create_index("purge_at", expireAfterSeconds=0, name="owner_grant_proof_purge")


def issue_proof(db, *, actor_id, audience: str, action: str, intent_digest: str, factor: str,
                subject: Optional[str] = None) -> str:
    """Return a new proof token for a fresh strong-factor approval.

    ``subject`` is the account the intent is about; state lookups with this
    proof are limited to it.
    """
    if factor not in STRONG_FACTORS:
        raise ValueError("a grant proof needs a passkey or password and TOTP")
    if not (audience and action and intent_digest):
        raise ValueError("audience, action and intent are required")
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db[PROOFS].insert_one({
        "_id": ObjectId(), "token_hash": _hash(token), "actor_id": str(actor_id),
        "audience": audience, "action": action, "intent_digest": intent_digest, "factor": factor,
        "subject": str(subject) if subject is not None else None, "created_at": now, "expires_at": now + PROOF_TTL, "purge_at": now + PROOF_TTL + timedelta(days=1),
        "used_at": None, "idempotency_key": None,
    })
    return token


def consume_proof(db, token, *, audience: str, action: str, intent_digest: str,
                  idempotency_key: str) -> Optional[str]:
    """Return the approving actor id, or None when the proof does not fit.

    The first use binds the proof to ``idempotency_key``; a retry of that
    command may present it again until it expires.
    """
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    now = utcnow()
    match = {"token_hash": _hash(token), "audience": audience, "action": action,
             "intent_digest": intent_digest, "expires_at": {"$gt": now}}
    proof = db[PROOFS].find_one_and_update(
        {**match, "used_at": None},
        {"$set": {"used_at": now, "idempotency_key": idempotency_key}},
        return_document=ReturnDocument.AFTER,
    )
    if proof is None:
        proof = db[PROOFS].find_one({**match, "idempotency_key": idempotency_key})
    return proof["actor_id"] if proof else None


def proof_actor(db, token, *, audience: str, subject: Optional[str] = None,
                idempotency_key: Optional[str] = None) -> Optional[str]:
    """Actor of an unexpired proof, limited to what it was issued for.

    A receipt lookup needs the proof already bound to that Idempotency-Key;
    a state lookup needs the account the proof was issued for.
    """
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    if subject is None and not idempotency_key:
        return None
    query = {"token_hash": _hash(token), "audience": audience, "expires_at": {"$gt": utcnow()}}
    if subject is not None:
        query["subject"] = str(subject)
    if idempotency_key:
        query["idempotency_key"] = idempotency_key
    proof = db[PROOFS].find_one(query)
    return proof["actor_id"] if proof else None
