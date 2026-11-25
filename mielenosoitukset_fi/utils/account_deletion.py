"""Account deletions by admins: soft delete first, Hallinto's OT decides (24 Nov 2026).

When an admin deletes a user account in Flask, nothing is erased at once. The
account is closed (disabled, hidden, every session and token ended) and a review
row is stored. Hallinto reads the row, opens a case, and OT decides: confirm
(Flask then erases the account the way the instant delete did) or reverse (the
account is restored). A second, separate review in the same case asks whether it
was necessary for that admin to do it; it changes nothing by itself.

Users deleting their own account and GDPR erasure requests never come through
here and stay instant.

``ACCOUNT_DELETION_IN_HALLINTO=true`` turns the review on. While it is off the
admin delete stays instant as before, so Flask can be deployed before Hallinto.
"""

from __future__ import annotations

import os
from typing import Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from mielenosoitukset_fi.utils.time_utils import utcnow

COLLECTION = "account_deletion_reviews"
ENV_FLAG = "ACCOUNT_DELETION_IN_HALLINTO"
PENDING, DELETED, RESTORED = "pending", "deleted", "restored"
STATUSES = (PENDING, DELETED, RESTORED)
# The statuses Hallinto may write back. "pending" only moves to the others
# through an order, never through this update, so a status here is a note.
HALLINTO_STATUSES = ("in_review",)
CASE_MAX = 128


class DeletionError(Exception):
    """The deletion cannot be started or finished; ``code`` says why."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def review_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def ensure_indexes(db) -> None:
    """One open review per account, whatever races start it."""
    db[COLLECTION].create_index([("user_id", 1)], unique=True, name="one_open_review",
                                partialFilterExpression={"status": PENDING})


_indexed = False


def pending_review(db, user_id) -> Optional[dict]:
    return db[COLLECTION].find_one({"user_id": ObjectId(str(user_id)), "status": PENDING})


def soft_delete(db, target: dict, actor, reason: str = "") -> dict:
    """Close the account and store the review. Returns the review row.

    The account stays in the database untouched except for the closing marks,
    so reversing is exact. Raises DeletionError when a review is already open.
    """
    from mielenosoitukset_fi.admin import city_grants as cg
    from mielenosoitukset_fi.utils.security_cases import _revoke

    global _indexed
    if not _indexed:
        ensure_indexes(db)
        _indexed = True
    if pending_review(db, target["_id"]) or target.get("soft_deleted"):
        raise DeletionError("already_pending")
    now = utcnow()
    review = {
        "_id": ObjectId(), "user_id": target["_id"], "status": PENDING,
        # Names only, never an email address: Hallinto shows and records these.
        "username": target.get("username"), "displayname": target.get("displayname"),
        "role": target.get("role"),
        "requested_by": str(actor._id), "requested_by_name": getattr(actor, "username", None),
        "requested_at": now, "reason": (reason or "").strip()[:1000], "created_at": now, "updated_at": now,
        "history": [{"status": PENDING, "at": now, "by": str(actor._id)}],
    }
    try:
        db[COLLECTION].insert_one(review)
    except DuplicateKeyError as exc:
        raise DeletionError("already_pending") from exc
    updates = {
        "active": False,
        "soft_deleted": {"at": now, "by": str(actor._id), "review_id": str(review["_id"]),
                         "previous_active": target.get("active", True)},
        "auth_epoch": (target.get("auth_epoch") or 0) + 1,
    }
    try:
        cg.commit_profile_change(db, target, updates, actor, "account_deletion_requested")
    except Exception:
        # Without the closing marks there is nothing to review.
        db[COLLECTION].delete_one({"_id": review["_id"]})
        raise
    _revoke(db, target, review["_id"])
    return review


def restore(db, user_id, actor, order_id: str) -> bool:
    """Reopen a closed account exactly as it was. Idempotent."""
    from mielenosoitukset_fi.admin import city_grants as cg

    doc = db.users.find_one({"_id": ObjectId(str(user_id))})
    if doc is None or not doc.get("soft_deleted"):
        return False
    previous = doc["soft_deleted"].get("previous_active", True)
    updates = {"active": previous, "auth_epoch": (doc.get("auth_epoch") or 0) + 1, "soft_deleted": None}
    cg.commit_profile_change(db, doc, updates, actor, "account_deletion_reversed")
    db.users.update_one({"_id": doc["_id"]}, {"$unset": {"soft_deleted": ""}})
    _finish(db, doc["_id"], RESTORED, order_id)
    return True


def hard_delete(db, user_doc: dict, secret: str, seal_key: Optional[str]) -> dict:
    """Erase the account and its records. The one place that does it, for the
    instant delete (flag off) and for Hallinto's confirmed decision.

    Returns ``{"status": "ok" | "history_pending" | "changed", ...}``.
    """
    from mielenosoitukset_fi.admin.user_erasure import erase_user
    from mielenosoitukset_fi.admin.user_history import PENDING_HISTORY, flush_user_history

    uid = user_doc["_id"]
    if not flush_user_history(db, uid):
        return {"status": "history_pending"}
    deleted = db.users.delete_one({"_id": uid, f"{PENDING_HISTORY}.0": {"$exists": False}})
    if deleted.deleted_count != 1:
        return {"status": "changed"}
    try:
        report = erase_user(db, user_doc, secret, seal_key)
    except Exception:  # The account is gone; erasure is safe to run again.
        from mielenosoitukset_fi.utils.logger import logger

        logger.exception("User %s deleted but erasure of their records failed", uid)
        return {"status": "ok", "erasure": None}
    return {"status": "ok", "erasure": report}


def confirm_deletion(db, user_id, order_id: str, secret: str, seal_key: Optional[str]) -> str:
    """Carry out a confirmed deletion. Returns "deleted", or a rejection code."""
    doc = db.users.find_one({"_id": ObjectId(str(user_id))})
    if doc is None:
        review = db[COLLECTION].find_one({"user_id": ObjectId(str(user_id)), "status": DELETED})
        return "deleted" if review else "stale_state"
    if not doc.get("soft_deleted") or pending_review(db, doc["_id"]) is None:
        return "stale_state"  # Only a closed account with an open review is erased.
    result = hard_delete(db, doc, secret, seal_key)
    if result["status"] != "ok":
        return "unavailable"
    _finish(db, doc["_id"], DELETED, order_id)
    return "deleted"


def _finish(db, user_id, status: str, order_id: str) -> None:
    now = utcnow()
    db[COLLECTION].update_many(
        {"user_id": user_id, "status": PENDING},
        {"$set": {"status": status, "decided_order": order_id, "updated_at": now, "decided_at": now},
         "$push": {"history": {"status": status, "at": now, "order": order_id}}})


# --- What Hallinto reads and writes -------------------------------------


def hallinto_view(row: dict) -> dict:
    stamp = lambda value: value.isoformat() + ("" if value.tzinfo else "Z") if value else None
    return {
        "id": str(row["_id"]), "status": row.get("status"),
        "account": {"id": str(row["user_id"]), "username": row.get("username"),
                    "name": row.get("displayname") or row.get("username"), "role": row.get("role")},
        "requested_by": {"id": row.get("requested_by"), "name": row.get("requested_by_name")},
        "reason": row.get("reason") or "", "case": row.get("hallinto_case"),
        "in_review": bool(row.get("in_review")),
        "requested_at": stamp(row.get("requested_at")), "updated_at": stamp(row.get("updated_at")),
    }


def hallinto_list(db, limit: int = 100) -> list[dict]:
    rows = db[COLLECTION].find({}).sort("requested_at", -1).limit(limit)
    return [hallinto_view(row) for row in rows]


class HallintoConflict(Exception):
    """The review is decided or handled in another case."""


def hallinto_update(db, review_id: ObjectId, case: str, status: str = "in_review", actor: str = "") -> Optional[dict]:
    """Link the Hallinto case and note that the review is being handled. The
    outcome itself is never written here: only an order changes the account."""
    if status not in HALLINTO_STATUSES or not isinstance(case, str) or not 0 < len(case) <= CASE_MAX:
        raise ValueError("invalid")
    row = db[COLLECTION].find_one({"_id": review_id})
    if row is None:
        return None
    if row.get("hallinto_case") not in (None, case):
        raise HallintoConflict()
    if row.get("hallinto_case") == case and row.get("in_review"):
        return row
    now = utcnow()
    return db[COLLECTION].find_one_and_update(
        {"_id": review_id, "$or": [{"hallinto_case": None}, {"hallinto_case": {"$exists": False}}, {"hallinto_case": case}]},
        {"$set": {"hallinto_case": case, "in_review": True, "updated_at": now},
         "$push": {"history": {"status": "in_review", "at": now, "by": actor, "case": case}}},
        return_document=True)
