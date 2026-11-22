"""Background job and command: do old admin request log rows hold posted form values?

    python -m mielenosoitukset_fi.scripts.diagnose_admin_log_forms

Read-only. Before 20.11.2026 an admin request row stored the raw request object
(``admin_logs.request``). That object has ``form`` only when something parsed the
posted form before the logging hook ran. The organization history backfill needs
to know whether field values survive in those rows, so this job counts, for six
sample days before the fix, the rows, the POST rows, the rows with a stored
``form`` and the organization edit rows with a stored ``form``, plus the *names*
of the form fields seen (never values). Each sample is one day on the timestamp
index and is cut off after 20 seconds, so it does not load the database
(a full scan of the logs would: they are most of the database).

The result is stored once in ``maintenance_markers`` and shown as an
``admin_log_form_check`` entry in the admin log. ``ADMIN_LOG_FORM_CHECK_DISABLED=1``
stops it.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from pymongo.errors import PyMongoError

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger

MARKER_ID = "admin_log_form_check_2026_11"
SAMPLE_DAYS = [datetime(2026, m, 15) for m in (1, 3, 5, 7, 9, 11)]  # all before the 20.11.2026 fix
ORG_EDIT = r"^/admin/organization/(edit|create)"
MAX_MS = 20000


def _is_disabled():
    return os.environ.get("ADMIN_LOG_FORM_CHECK_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def check_day(db, day):
    window = {"timestamp": {"$gte": day, "$lt": day + timedelta(days=1)}}
    post = {**window, "request.method": "POST"}
    with_form = {**post, "request.form": {"$type": "object"}}
    org_form = {**with_form, "request.path": {"$regex": ORG_EDIT}}
    out = {"day": day.date().isoformat()}
    try:
        out["rows"] = db.admin_logs.count_documents(window, maxTimeMS=MAX_MS)
        out["post_rows"] = db.admin_logs.count_documents(post, maxTimeMS=MAX_MS)
        out["post_with_form"] = db.admin_logs.count_documents(with_form, maxTimeMS=MAX_MS)
        out["org_edit_with_form"] = db.admin_logs.count_documents(org_form, maxTimeMS=MAX_MS)
        keys = set()
        for row in db.admin_logs.find(org_form, {"request.form": 1}).limit(20).max_time_ms(MAX_MS):
            keys.update(str(k)[:40] for k in (row.get("request") or {}).get("form", {}))
        out["org_form_field_names"] = sorted(keys)[:60]
    except PyMongoError as exc:
        out["error"] = type(exc).__name__
    return out


def main(db=None):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if _is_disabled():
        return {"disabled": True}
    if db.maintenance_markers.find_one({"_id": MARKER_ID}):
        return {"already_done": True}
    result = {"samples": [check_day(db, day) for day in SAMPLE_DAYS]}
    finished_at = datetime.now(timezone.utc)
    db.maintenance_markers.update_one(
        {"_id": MARKER_ID}, {"$setOnInsert": {**result, "finished_at": finished_at}}, upsert=True)
    db.admin_logs.insert_one({
        "event": "admin_log_form_check",
        "message": "Counted old admin request rows that hold posted form values (counts and field names only).",
        "details": result,
        "timestamp": finished_at.replace(tzinfo=None),
        "actor": {"username": "system"},
    })
    logger.info("Admin log form check: %s", result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), default=str))
