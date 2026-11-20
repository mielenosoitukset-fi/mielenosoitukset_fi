"""Open demonstration suggestions as admin cases (issue #1217).

Suggestions are stored in ``demo_suggestions`` by Flask's ``suggest_change``
or by the Go handler. Both keep writing only that collection; this module
turns each open suggestion into a ``demo_edit_suggestion`` case so it shows up
in the case list next to new demonstrations and support tickets, instead of
only on the separate suggestions page.

The case reuses the suggestion's ``_id``, so delivery is idempotent and safe to
rerun after a crash. The case holds no copy of the IP address, User-Agent or
suggested values; the suggestion page remains the place where changes are
reviewed and applied.
"""
import os

from bson import ObjectId

from mielenosoitukset_fi.utils.classes import Case
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

CASE_TYPE = "demo_edit_suggestion"
ENV_FLAG = "DEMO_SUGGESTION_CASES_ENABLED"
OPEN_STATUSES = ("new", "pending", "", None)
DECIDED_STATUSES = ("applied", "rejected", "closed")


OFF_VALUES = {"0", "false", "no", "off"}


def is_enabled(environ=None):
    """On by default since 20 Nov 2026; set the flag to 0/false/no/off to switch off."""
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_FLAG, "")).strip().lower() not in OFF_VALUES


def _open_without_case():
    return {
        "$and": [
            {"$or": [
                {"status": {"$in": [status for status in OPEN_STATUSES if status is not None]}},
                {"status": None},
                {"status": {"$exists": False}},
            ]},
            {"$or": [{"case_id": {"$exists": False}}, {"case_id": None}]},
            # A broken suggestion is skipped for good, so it cannot block newer ones.
            {"case_skipped": {"$exists": False}},
        ]
    }


def _next_running_num(db):
    """Number from the same database the job writes to."""
    last = db.cases.find_one({"running_num": {"$type": "number"}}, {"running_num": 1}, sort=[("running_num", -1)])
    return (last["running_num"] + 1) if last else Case.STARTING_NUM


def _case_document(db, suggestion):
    reporter_email = suggestion.get("reporter_email")
    case = Case(
        running_num=_next_running_num(db),
        case_type=CASE_TYPE,
        demo_id=suggestion.get("demo_id"),
        submitter={"submitter_email": reporter_email} if reporter_email else {},
        meta={
            "suggestion_id": suggestion["_id"],
            "fields": sorted((suggestion.get("suggested_fields") or {}).keys()),
        },
        # Keep the suggestion's own time so the case list stays in order.
        created_at=suggestion.get("created_at"),
        _id=suggestion["_id"],
        case_history=[{
            "timestamp": utcnow(), "action": "Tapaus avattu muutosehdotuksesta", "user": "system",
            "mech_action": "created", "metadata": {}, "meta_schema": {},
        }],
    )
    return case.to_dict()


def deliver_pending(db, limit=100):
    """Create a case for each open suggestion that has none yet."""
    delivered = 0
    for suggestion in db.demo_suggestions.find(_open_without_case()).sort("created_at", 1).limit(limit):
        case_id = suggestion["_id"]
        if not ObjectId.is_valid(str(suggestion.get("demo_id") or "")):
            db.demo_suggestions.update_one({"_id": case_id}, {"$set": {"case_skipped": "invalid_demo_id"}})
            logger.warning("Demo suggestion %s has no valid demo_id; no case opened", case_id)
            continue
        try:
            if not db.cases.find_one({"_id": case_id}, {"_id": 1}):
                db.cases.update_one({"_id": case_id}, {"$setOnInsert": _case_document(db, suggestion)}, upsert=True)
            db.demo_suggestions.update_one({"_id": case_id}, {"$set": {"case_id": case_id}})
            delivered += 1
        except Exception:
            logger.exception("Failed to open a case for demo suggestion %s", case_id)
    return delivered


def close_decided(db, limit=200):
    """Close open suggestion cases whose suggestion was applied, rejected or closed."""
    closed = 0
    open_cases = db.cases.find(
        {"type": CASE_TYPE, "meta.closed": {"$ne": True}}, {"_id": 1, "meta.suggestion_id": 1}
    ).limit(limit)
    for case in open_cases:
        suggestion_id = (case.get("meta") or {}).get("suggestion_id") or case["_id"]
        suggestion = db.demo_suggestions.find_one({"_id": suggestion_id}, {"status": 1})
        if suggestion and suggestion.get("status") not in DECIDED_STATUSES:
            continue
        reason = "Ehdotus käsitelty" if suggestion else "Ehdotus poistettu"
        now = utcnow()
        result = db.cases.update_one(
            {"_id": case["_id"], "meta.closed": {"$ne": True}},
            {
                "$set": {"meta.closed": True, "meta.closed_reason": reason, "meta.closed_at": now, "updated_at": now},
                "$push": {
                    "case_history": {
                        "timestamp": now, "action": "Tapaus suljettu automaattisesti", "user": "system",
                        "mech_action": "auto_close", "metadata": {"reason": reason},
                        "meta_schema": {"reason": "string"},
                    },
                    "action_logs": {"timestamp": now, "admin": "system", "action_type": "auto_close", "note": reason},
                },
            },
        )
        closed += result.modified_count
    return closed
