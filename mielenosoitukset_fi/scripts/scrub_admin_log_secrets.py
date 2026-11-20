"""Background job and command: remove credentials from old admin request log rows.

    python -m mielenosoitukset_fi.scripts.scrub_admin_log_secrets --dry-run

Before 20.11.2026 the admin request logger stored the raw request object and the
full user document in ``admin_logs``. Those rows hold the session cookie and
Authorization header (``request.environ``, ``request.headers``) and the admin's
``user.password_hash``. The maintainer approved removing exactly those fields
(20.11.2026). The rows themselves, and every other field, are kept; the
User-Agent is first copied to ``request.user_agent`` so the log view still
shows it.

The job runs once: a finished run leaves a marker in ``maintenance_markers``
with the counts before and after, and adds an ``admin_log_secret_scrub`` entry
to ``admin_logs`` so the result is visible in the admin log view. Later runs
return at once. Set ``ADMIN_LOG_SCRUB_DISABLED=1`` to stop it; ``--dry-run``
only counts.

No separate backup copy is written: it would only be a new copy of the
credentials being removed. The weekly full database archive (root-only,
docs/backups.md) still holds the rows as they were until it expires.
"""
import argparse
import json
import os
from datetime import datetime, timezone

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger

MARKER_ID = "admin_log_secret_scrub_2026_11"
# Rows written after the fixed logger was deployed (2cded061, 20.11.2026 07:31 UTC)
# never had these fields; the bound keeps the scan on the timestamp index.
WRITTEN_BEFORE = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
SECRET_FIELDS = ("request.environ", "request.headers", "user.password_hash")


def _query():
    return {
        "timestamp": {"$lt": WRITTEN_BEFORE.replace(tzinfo=None)},
        "$or": [{field: {"$exists": True}} for field in SECRET_FIELDS],
    }


def scrub(db, dry_run=False):
    """Unset the credential fields on old rows. Returns counts before and after."""
    query = _query()
    before = db.admin_logs.count_documents(query)
    if dry_run:
        return {"dry_run": True, "matching_rows": before}
    # Keep the User-Agent the log view falls back to before the environ goes.
    keep_user_agent = [{"$set": {"request.user_agent": {"$ifNull": [
        "$request.user_agent", "$request.environ.HTTP_USER_AGENT"]}}}]
    db.admin_logs.update_many(
        {**query, "request.environ.HTTP_USER_AGENT": {"$type": "string"}}, keep_user_agent)
    changed = db.admin_logs.update_many(
        query, {"$unset": {field: "" for field in SECRET_FIELDS}}).modified_count
    after = db.admin_logs.count_documents(query)
    return {"dry_run": False, "matching_rows": before, "changed": changed, "remaining": after}


def is_disabled():
    return os.environ.get("ADMIN_LOG_SCRUB_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def main(db=None, dry_run=False):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if dry_run:
        return scrub(db, dry_run=True)
    if is_disabled():
        return {"disabled": True}
    if db.maintenance_markers.find_one({"_id": MARKER_ID}):
        return {"already_done": True}
    result = scrub(db)
    finished_at = datetime.now(timezone.utc)
    if result["remaining"] == 0:
        db.maintenance_markers.update_one(
            {"_id": MARKER_ID},
            {"$setOnInsert": {**result, "finished_at": finished_at}},
            upsert=True,
        )
    db.admin_logs.insert_one({
        "event": "admin_log_secret_scrub",
        "message": "Removed stored session cookies, request headers and password hashes from old admin request log rows.",
        "details": result,
        "timestamp": finished_at.replace(tzinfo=None),
        "actor": {"username": "system"},
    })
    logger.info("Admin log credential scrub: %s", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
