"""Close cases whose subject was already handled elsewhere.

The background job runs this. Opening the case list never writes: an admin
reading a list should not change records as a side effect.
"""

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

DEMO_CASE_TYPES = {"new_demo", "demo_cancellation_request", "demo_cancelled"}


def _oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def resolution_reason(case_type, created_at, demo_doc=None, org_doc=None, suggestion_doc=None):
    """Why an open case no longer needs work, or None while it still does."""
    if case_type == "new_demo" and demo_doc:
        if demo_doc.get("approved") or demo_doc.get("accepted"):
            return "Demo hyväksytty"
        if demo_doc.get("rejected"):
            return "Demo hylätty"
        if demo_doc.get("cancelled"):
            return "Demo peruttu"
    if case_type in {"demo_cancellation_request", "demo_cancelled"} and demo_doc and demo_doc.get("cancelled"):
        return "Peruutus käsitelty"
    if case_type == "demo_edit_suggestion" and suggestion_doc:
        if suggestion_doc.get("status") in {"applied", "rejected", "closed"}:
            return "Ehdotus käsitelty"
    if case_type == "organization_edit_suggestion" and org_doc and created_at:
        updated_at = org_doc.get("updated_at")
        if updated_at and updated_at >= created_at:
            return "Organisaatiomuutokset tallennettu"
    return None


def _reason_for(db, doc):
    case_type = doc.get("type")
    meta = doc.get("meta") or {}
    demo_doc = org_doc = suggestion_doc = None
    if case_type in DEMO_CASE_TYPES and doc.get("demo_id"):
        demo_doc = db.demonstrations.find_one(
            {"_id": _oid(doc["demo_id"])}, {"approved": 1, "accepted": 1, "rejected": 1, "cancelled": 1}
        )
    if case_type == "organization_edit_suggestion" and doc.get("organization_id"):
        org_doc = db.organizations.find_one({"_id": _oid(doc["organization_id"])}, {"updated_at": 1})
    if case_type == "demo_edit_suggestion":
        suggestion_doc = db.demo_suggestions.find_one(
            {"_id": _oid(meta.get("suggestion_id") or doc["_id"])}, {"status": 1}
        )
    return resolution_reason(case_type, doc.get("created_at"), demo_doc, org_doc, suggestion_doc)


def close_resolved_cases(db, actor="background"):
    """Close every open case whose subject is handled. Returns (checked, closed)."""
    checked = closed = 0
    for doc in db.cases.find({"meta.closed": {"$ne": True}}, {"type": 1, "demo_id": 1, "organization_id": 1, "meta": 1, "created_at": 1}):
        checked += 1
        reason = _reason_for(db, doc)
        if not reason:
            continue
        now = utcnow()
        result = db.cases.update_one(
            {"_id": doc["_id"], "meta.closed": {"$ne": True}},
            {
                "$set": {"meta.closed": True, "meta.closed_reason": reason, "meta.closed_at": now, "updated_at": now},
                "$push": {
                    "case_history": {
                        "timestamp": now,
                        "action": "Tapaus suljettu automaattisesti",
                        "user": actor,
                        "mech_action": "auto_close_job",
                        "metadata": {"reason": reason},
                        "meta_schema": {"reason": "string"},
                    },
                    "action_logs": {"timestamp": now, "admin": actor, "action_type": "auto_close", "note": reason},
                },
            },
        )
        closed += result.modified_count
    return checked, closed
