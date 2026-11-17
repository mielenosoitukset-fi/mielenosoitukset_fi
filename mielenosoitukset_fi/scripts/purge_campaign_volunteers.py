"""Background job and command: erase campaign volunteer signups after the season.

    python -m mielenosoitukset_fi.scripts.purge_campaign_volunteers --dry-run

Maintainer decision (16.11.2026): volunteer signups are deleted after 31.5.2027.
The job only ever deletes, and does nothing before 1.6.2027 00:00 Helsinki time.
"""
import argparse
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger

PURGE_AFTER = datetime(2027, 6, 1, 0, 0, tzinfo=ZoneInfo("Europe/Helsinki")).astimezone(timezone.utc)


def purge(db, now=None, dry_run=False):
    """Delete every volunteer document once the retention period has ended."""
    now = now or datetime.now(timezone.utc)
    if now < PURGE_AFTER:
        return {"due": False, "deleted": 0}
    total = db.volunteers.count_documents({})
    if dry_run:
        return {"due": True, "would_delete": total}
    return {"due": True, "deleted": db.volunteers.delete_many({}).deleted_count}


def main(db=None, dry_run=False):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    result = purge(db, dry_run=dry_run)
    if not dry_run and result.get("deleted"):
        logger.info("Purged campaign volunteer signups: %s", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, delete nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
