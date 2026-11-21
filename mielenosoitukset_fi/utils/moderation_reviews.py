"""OT review of moderation rejections (Hallinto roadmap R03).

The moderation guide approved on 18 November 2026 makes every rejection or
take-down provisional until OT reviews it. OT confirms it, overturns it or asks
the submitter for more information. The target is two working days and the
limit five; a rejection never confirms itself by time passing. After a
confirmed rejection the submitter may ask for reconsideration, which someone
other than the original decider resolves within 30 days of the request.

Flask keeps the review next to the demonstration it is about, and Hallinto
reads and records it through the issuer (``/hallinto-oidc/moderation-reviews``)
as the signed-in OT member. Hallinto decides who may review; this module
enforces only what holds whoever records it: the original decider never
resolves a reconsideration, and an overturn publishes only the exact rejection
that was reviewed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

COLLECTION = "moderation_reviews"
HELSINKI = ZoneInfo("Europe/Helsinki")
REVIEWED_DECISIONS = ("rejected", "taken_down")
OPEN_STATES = ("open", "needs_info")
STATES = ("open", "needs_info", "confirmed", "overturned", "superseded")
TARGET_WORKING_DAYS = 2
MAX_WORKING_DAYS = 5
RECONSIDERATION_DAYS = 30
MESSAGE_MAX = 1000
LIST_LIMIT = 100

SUBJECTS = {
    "confirmed": "Ilmoituksesi hylkäys on tarkistettu",
    "overturned": "Mielenosoituksesi julkaistiin tarkistuksen jälkeen",
    "needs_info": "Tarvitsemme lisätietoja ilmoituksestasi",
    "delayed": "Ilmoituksesi tarkistus viivästyy",
}


class ReviewError(ValueError):
    """A review step that cannot be recorded; ``code`` is the API error."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def ensure_indexes(db) -> None:
    db[COLLECTION].create_index("decision_id", unique=True)
    db[COLLECTION].create_index([("status", 1), ("created_at", -1)])
    db[COLLECTION].create_index("demo_id")


def add_working_days(start: datetime, days: int) -> datetime:
    """``start`` plus ``days`` Monday-to-Friday days, counted in Helsinki.

    Public holidays are not skipped; the deadline is a target, never an
    automatic outcome, so an early reminder is the safe side.
    """
    aware = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    local = aware.astimezone(HELSINKI)
    added = 0
    while added < days:
        local += timedelta(days=1)
        if local.weekday() < 5:
            added += 1
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _local_midnight(value: datetime) -> datetime:
    """The start of the Helsinki day of a naive UTC instant, as naive UTC."""
    local = value.replace(tzinfo=timezone.utc).astimezone(HELSINKI)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _decider(actor: dict | None) -> dict:
    actor = actor or {}
    recipient = actor.get("link_recipient") or {}
    account = actor.get("user_id") or recipient.get("user_id") or recipient.get("_id")
    return {
        "id": str(account) if account and ObjectId.is_valid(str(account)) else None,
        "name": actor.get("username") or recipient.get("username") or "tuntematon",
    }


def open_review(db, demo: dict, decision_meta: dict) -> bool:
    """Record a new rejection or take-down for OT review. Idempotent."""
    if decision_meta.get("status") not in REVIEWED_DECISIONS:
        return False
    at = decision_meta["at"]
    doc = {
        "demo_id": demo["_id"],
        "decision_id": decision_meta["id"],
        "decision": decision_meta["status"],
        "decided_by": _decider(decision_meta.get("actor")),
        "decided_at": at,
        "source": decision_meta.get("source"),
        "status": "open",
        "stage": "review",
        "due_target": add_working_days(at, TARGET_WORKING_DAYS),
        "due_max": add_working_days(at, MAX_WORKING_DAYS),
        "created_at": at,
        "updated_at": at,
        "history": [],
    }
    try:
        db[COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        return False
    return True


def supersede_reviews(db, demo_id, decision_meta: dict) -> int:
    """A newer moderation decision in Flask ends the open reviews of a demo."""
    result = db[COLLECTION].update_many(
        {"demo_id": demo_id, "status": {"$in": list(OPEN_STATES)},
         "decision_id": {"$ne": decision_meta["id"]}},
        {"$set": {"status": "superseded", "updated_at": decision_meta["at"]},
         "$push": {"history": {"action": "superseded", "at": decision_meta["at"],
                               "decision_id": decision_meta["id"]}}},
    )
    return result.modified_count


def _stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _reason(db, row, demo) -> str | None:
    if row.get("decision") == "taken_down":
        return (demo or {}).get("taken_down_reason")
    latest = None
    for case in db.cases.find({"demo_id": {"$in": [row["demo_id"], str(row["demo_id"])]}},
                              {"case_history": 1}):
        for entry in case.get("case_history") or []:
            if entry.get("mech_action") == "reject_demo" and (
                latest is None or (entry.get("timestamp") or datetime.min) >= (latest.get("timestamp") or datetime.min)
            ):
                latest = entry
    return ((latest or {}).get("metadata") or {}).get("reason")


def view(db, row: dict) -> dict:
    demo = db.demonstrations.find_one(
        {"_id": row["demo_id"]},
        {"title": 1, "date": 1, "city": 1, "taken_down_reason": 1, "moderation_decision.id": 1},
    ) or {}
    reconsideration = row.get("reconsideration") or {}
    return {
        "id": str(row["_id"]),
        "demo": {"id": str(row["demo_id"]), "title": demo.get("title"), "date": demo.get("date"),
                 "city": demo.get("city")},
        "decision": row.get("decision"),
        "reason": _reason(db, row, demo),
        "decided_by": row.get("decided_by") or {},
        "decided_at": _stamp(row.get("decided_at")),
        "status": row.get("status"),
        "stage": row.get("stage"),
        "due_target": _stamp(row.get("due_target")),
        "due_max": _stamp(row.get("due_max")),
        "reconsideration_received": _stamp(reconsideration.get("received_at")),
        "reconsideration_due": _stamp(reconsideration.get("due")),
        "message": row.get("message"),
        "current": (demo.get("moderation_decision") or {}).get("id") == row.get("decision_id"),
        "history": [
            {"action": h.get("action"), "at": _stamp(h.get("at")), "by": h.get("by"),
             "stage": h.get("stage"), "message": h.get("message")}
            for h in row.get("history") or []
        ],
    }


def list_reviews(db) -> list[dict]:
    """Open reviews first (oldest deadline first), then the latest closed ones."""
    open_rows = list(db[COLLECTION].find({"status": {"$in": list(OPEN_STATES)}})
                     .sort("created_at", 1).limit(LIST_LIMIT))
    closed = list(db[COLLECTION].find({"status": {"$nin": list(OPEN_STATES)}})
                  .sort("updated_at", -1).limit(max(LIST_LIMIT - len(open_rows), 20)))
    return [view(db, row) for row in open_rows + closed]


def _submitter_email(db, demo_id):
    submitter = db.submitters.find_one({"demonstration_id": demo_id}, {"submitter_email": 1})
    return (submitter or {}).get("submitter_email")


def _notify(db, email_sender, row, kind, *, message=None, url=None) -> bool:
    recipient = _submitter_email(db, row["demo_id"])
    if not recipient or email_sender is None:
        return False
    demo = db.demonstrations.find_one({"_id": row["demo_id"]}, {"title": 1, "date": 1, "city": 1}) or {}
    email_sender.queue_email(
        template_name=f"demo_review_{kind}.html",
        subject=SUBJECTS[kind],
        recipients=[recipient],
        context={
            "title": demo.get("title", ""), "date": demo.get("date", ""), "city": demo.get("city", ""),
            "message": message or "", "url": url or "",
            "final": row.get("stage") == "reconsideration",
        },
    )
    return True


def record(db, review_id: str, action: str, *, actor: dict, message: str | None,
           received_on: datetime | None = None, email_sender=None, public_url=None,
           apply_decision=None) -> dict:
    """Record one OT step. ``actor`` is ``{"id": account id, "name": username}``.

    ``apply_decision(demo_id, actor)`` publishes the demonstration on an
    overturn; it is the shared moderation service, so the usual side effects
    (cases, links, audit) follow.
    """
    if not ObjectId.is_valid(review_id or ""):
        raise ReviewError("invalid_request", 400)
    message = (message or "").strip()
    if len(message) > MESSAGE_MAX:
        raise ReviewError("invalid_request", 400)
    oid = ObjectId(review_id)
    row = db[COLLECTION].find_one({"_id": oid})
    if not row:
        raise ReviewError("not_found", 404)
    now = utcnow()
    entry = {"action": action, "at": now, "by": actor, "stage": row.get("stage"), "message": message or None}

    if action == "reconsider":
        if row.get("status") != "confirmed" or row.get("stage") != "review":
            raise ReviewError("not_reconsiderable")
        if received_on is None or received_on > now or received_on < _local_midnight(row["decided_at"]):
            raise ReviewError("invalid_request", 400)
        update = {"$set": {"status": "open", "stage": "reconsideration", "updated_at": now,
                           "reconsideration": {"received_at": received_on,
                                               "due": received_on + timedelta(days=RECONSIDERATION_DAYS)}},
                  "$push": {"history": {**entry, "stage": "reconsideration"}}}
        updated = db[COLLECTION].find_one_and_update(
            {"_id": oid, "status": "confirmed", "stage": "review"}, update, return_document=ReturnDocument.AFTER)
        if not updated:
            raise ReviewError("changed")
        return view(db, updated)

    if action not in ("confirm", "overturn", "needs_info"):
        raise ReviewError("invalid_request", 400)
    if not message:
        raise ReviewError("message_required", 400)
    if row.get("status") not in OPEN_STATES:
        raise ReviewError("closed")
    if row.get("stage") == "reconsideration" and actor.get("id") and actor["id"] == (row.get("decided_by") or {}).get("id"):
        # The guide: the original decider never resolves a reconsideration.
        raise ReviewError("conflicted", 403)

    status = {"confirm": "confirmed", "overturn": "overturned", "needs_info": "needs_info"}[action]
    if action == "overturn":
        demo = db.demonstrations.find_one({"_id": row["demo_id"]}, {"moderation_decision.id": 1})
        if not demo or (demo.get("moderation_decision") or {}).get("id") != row["decision_id"]:
            # Never publish a demonstration whose rejection is no longer the one reviewed.
            raise ReviewError("stale")
    updated = db[COLLECTION].find_one_and_update(
        {"_id": oid, "status": row["status"], "stage": row.get("stage")},
        {"$set": {"status": status, "message": message, "updated_at": now}, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise ReviewError("changed")
    if action == "overturn":
        try:
            apply_decision(row["demo_id"], actor)
        except Exception:
            db[COLLECTION].update_one(
                {"_id": oid, "status": "overturned"},
                {"$set": {"status": row["status"], "message": row.get("message"), "updated_at": utcnow()},
                 "$pull": {"history": {"at": now, "action": "overturn"}}},
            )
            raise
    try:
        _notify(db, email_sender, updated, status, message=message,
                url=public_url if action == "overturn" else None)
    except Exception:
        logger.exception("Could not queue the review email for %s", review_id)
    return view(db, updated)


def notify_overdue(db, email_sender, now: datetime | None = None) -> int:
    """Tell the submitter once when the review passes its five working days."""
    now = now or utcnow()
    sent = 0
    for row in db[COLLECTION].find({"status": "open", "stage": "review", "due_max": {"$lt": now},
                                    "delay_notified_at": {"$exists": False}}).limit(200):
        claimed = db[COLLECTION].update_one(
            {"_id": row["_id"], "delay_notified_at": {"$exists": False}},
            {"$set": {"delay_notified_at": now}},
        )
        if claimed.modified_count and _notify(db, email_sender, row, "delayed"):
            sent += 1
    return sent
