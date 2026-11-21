"""Organisation change suggestions and the cases that track them.

A public suggestion is stored in ``org_edit_suggestions`` and opens an
``organization_edit_suggestion`` case. The decision (apply or reject) is made
on the organisation's suggestion review page; it closes the case here.
"""

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

CASE_TYPE = "organization_edit_suggestion"
DECIDED_STATES = {"applied", "partially_applied", "rejected"}


def _oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def suggestion_id_for_case(case):
    """Older cases embedded the whole suggestion; newer ones store its id in meta."""
    meta = getattr(case, "meta", None) or {}
    embedded = getattr(case, "suggestion", None) or {}
    return _oid(meta.get("suggestion_id") or embedded.get("_id"))


def suggestion_for_case(db, case):
    suggestion_id = suggestion_id_for_case(case)
    return db.org_edit_suggestions.find_one({"_id": suggestion_id}) if suggestion_id else None


def close_suggestion_cases(db, suggestion_id, reason, actor):
    """Close every open case for this suggestion, with a history entry."""
    sid = _oid(suggestion_id)
    if sid is None:
        return 0
    now = utcnow()
    result = db.cases.update_many(
        {
            "type": CASE_TYPE,
            "meta.closed": {"$ne": True},
            "$or": [{"meta.suggestion_id": str(sid)}, {"suggestion._id": sid}],
        },
        {
            "$set": {"meta.closed": True, "meta.closed_reason": reason, "meta.closed_at": now, "updated_at": now},
            "$push": {
                "case_history": {
                    "timestamp": now,
                    "action": "Tapaus suljettu",
                    "user": actor,
                    "mech_action": "org_suggestion_decided",
                    "metadata": {"reason": reason},
                    "meta_schema": {"reason": "string"},
                },
                "action_logs": {"timestamp": now, "admin": actor, "action_type": "close", "note": reason},
            },
        },
    )
    return result.modified_count
