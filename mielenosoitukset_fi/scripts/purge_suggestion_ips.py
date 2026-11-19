"""Background job and command: remove the submitter's IP address and User-Agent from old suggestions.

    python -m mielenosoitukset_fi.scripts.purge_suggestion_ips --dry-run

Maintainer decision (18.11.2026): the IP address is kept on organisation and
demonstration suggestions, but removed after 90 days. The job only unsets the
IP address and User-Agent fields (also in the copy embedded in the related case);
the suggestions and cases themselves are never deleted.

The scheduled job changes nothing unless `SUGGESTION_IP_PURGE_ENABLED=1` is set
in the environment; `--dry-run` always works and only counts.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger

RETENTION = timedelta(days=90)

# (collection, date field, fields to unset)
TARGETS = (
    ("demo_suggestions", "created_at", ("ip", "user_agent")),
    ("org_edit_suggestions", "created_at", ("meta.ip", "meta.user_agent")),
    ("cases", "suggestion.created_at", ("suggestion.ip", "suggestion.user_agent",
                                        "suggestion.meta.ip", "suggestion.meta.user_agent")),
)


def purge(db, now=None, dry_run=False):
    """Unset IP and User-Agent on documents older than 90 days. Returns per-collection counts."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - RETENTION
    result = {}
    for collection, date_field, fields in TARGETS:
        query = {date_field: {"$lt": cutoff}, "$or": [{field: {"$exists": True}} for field in fields]}
        if dry_run:
            result[collection] = db[collection].count_documents(query)
        else:
            result[collection] = db[collection].update_many(query, {"$unset": {field: "" for field in fields}}).modified_count
    return {"dry_run": dry_run, "cutoff": cutoff.isoformat(), "counts": result}


def is_enabled():
    return os.environ.get("SUGGESTION_IP_PURGE_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def main(db=None, dry_run=False):
    if not dry_run and not is_enabled():
        return {"enabled": False, "changed": 0}
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    result = purge(db, dry_run=dry_run)
    if not dry_run and any(result["counts"].values()):
        logger.info("Removed IP addresses from old suggestions: %s", result["counts"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
