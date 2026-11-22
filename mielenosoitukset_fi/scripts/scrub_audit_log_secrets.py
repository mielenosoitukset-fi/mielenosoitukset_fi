"""Background job and command: second credential cleanup of old admin log rows.

    python -m mielenosoitukset_fi.scripts.scrub_audit_log_secrets --dry-run

The first cleanup (``scrub_admin_log_secrets``, 20.11.2026) removed
``request.environ``, ``request.headers`` and ``user.password_hash`` from old
``admin_logs`` rows. Two places were left:

* ``admin_logs.request.cookies``: the raw request object also held its parsed
  cookies (the session cookie), not only the environ and headers.
* ``super_audit_logs``: ``log_admin_action_V2`` copied the same payload into
  ``payload.request.{environ,headers,cookies}`` and ``payload.user.password_hash``.

This job removes exactly those fields from rows written before the logger fix
(20.11.2026 08:00 UTC) and keeps every row. In ``super_audit_logs`` the User-Agent
is first copied to ``payload.request.user_agent``. Work is done one day at a time
on the timestamp index with short pauses, because the two logs are most of the
database. A finished run leaves a marker in ``maintenance_markers`` with counts
and an ``admin_log_secret_scrub_2`` entry in ``admin_logs``. Later runs return at
once. ``ADMIN_LOG_SCRUB_DISABLED=1`` stops it; ``--dry-run`` counts one sample day only.
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger

MARKER_ID = "admin_log_secret_scrub_2026_11_b"
WRITTEN_BEFORE = datetime(2026, 11, 20, 8, 0)  # naive UTC, as stored
PAUSE_SECONDS = 0.1

TARGETS = (
    # collection, first day that can hold rows, fields to remove
    ("admin_logs", datetime(2024, 11, 1), ("request.cookies",)),
    ("super_audit_logs", datetime(2026, 1, 1),
     ("payload.request.environ", "payload.request.headers", "payload.request.cookies",
      "payload.user.password_hash")),
)


def _disabled():
    return os.environ.get("ADMIN_LOG_SCRUB_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def _scrub_day(db, collection, fields, day):
    end = min(day + timedelta(days=1), WRITTEN_BEFORE)
    query = {"timestamp": {"$gte": day, "$lt": end}, "$or": [{f: {"$exists": True}} for f in fields]}
    if collection == "super_audit_logs":
        db[collection].update_many(
            {**query, "payload.request.environ.HTTP_USER_AGENT": {"$type": "string"}},
            [{"$set": {"payload.request.user_agent": {"$ifNull": [
                "$payload.request.user_agent", "$payload.request.environ.HTTP_USER_AGENT"]}}}])
    return db[collection].update_many(query, {"$unset": {f: "" for f in fields}}).modified_count


def scrub(db, dry_run=False, pause=PAUSE_SECONDS):
    counts = {}
    for collection, first_day, fields in TARGETS:
        changed = 0
        day = first_day
        while day < WRITTEN_BEFORE:
            if dry_run:
                query = {"timestamp": {"$gte": day, "$lt": day + timedelta(days=1)},
                         "$or": [{f: {"$exists": True}} for f in fields]}
                changed += db[collection].count_documents(query, maxTimeMS=20000)
                break  # one sample day only
            changed += _scrub_day(db, collection, fields, day)
            day += timedelta(days=1)
            time.sleep(pause)
        counts[collection] = changed
    return {"dry_run": dry_run, "rows_changed": counts}


def main(db=None, dry_run=False):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if dry_run:
        return scrub(db, dry_run=True)
    if _disabled():
        return {"disabled": True}
    if db.maintenance_markers.find_one({"_id": MARKER_ID}):
        return {"already_done": True}
    result = scrub(db)
    finished_at = datetime.now(timezone.utc)
    db.maintenance_markers.update_one(
        {"_id": MARKER_ID}, {"$setOnInsert": {**result, "finished_at": finished_at}}, upsert=True)
    db.admin_logs.insert_one({
        "event": "admin_log_secret_scrub_2",
        "message": "Removed stored cookies and request credentials from old admin log and super audit rows.",
        "details": result,
        "timestamp": finished_at.replace(tzinfo=None),
        "actor": {"username": "system"},
    })
    logger.info("Audit log credential scrub: %s", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count one sample day only")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
