"""Background job and command: retention for personal data in logs (#596).

    python -m mielenosoitukset_fi.scripts.purge_personal_logs --dry-run

Maintainer decision (24.11.2026), see issue #596:

- ``login_logs`` (IP, User-Agent, username): deleted after 12 months, except rows
  of a user who is the subject of an open security case.
- ``malicious_reports`` (IP, User-Agent, email): deleted after 12 months.
- ``demo_submission_errors``: the form snapshot is removed after 30 days and the
  whole row is deleted after 90 days.
- ``demo_reminders``: IP and User-Agent are removed after 90 days. The address
  stays until the person unsubscribes, at most 24 months.
- ``reports`` (error reports from the public form, with IP and reporter email):
  deleted after 12 months. Open cases keep their own copy until closed.
- ``password_changes``: the visit rows of reset links (IP, User-Agent, email)
  are deleted after 30 days. Rows of completed password changes stay.

Audit logs and kept records of deleted accounts follow their own retention
(``AUDIT_RETENTION_DAYS``, ``ERASURE_RETENTION_DAYS``). Does nothing unless
``PERSONAL_LOG_RETENTION_ENABLED`` is set; ``--dry-run`` only counts.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.security_cases import TERMINAL

DAY = timedelta(days=1)

# (collection, date field, days, fields to unset; empty means delete the row, extra query)
RULES = (
    ("login_logs", "timestamp", 365, (), None),
    ("malicious_reports", "reported_at", 365, (), None),
    ("reports", "date", 365, (), None),
    ("demo_submission_errors", "created_at", 30, ("form_snapshot",), None),
    ("demo_submission_errors", "created_at", 90, (), None),
    ("demo_reminders", "created_at", 90, ("user_ip", "user_agent"), None),
    ("demo_reminders", "created_at", 730, (), None),
    ("password_changes", "datetime", 30, (), {"visit": True, "changed": {"$ne": True}}),
)


def _protected_user_ids(db):
    """Users who are the subject of an open security case keep their login rows."""
    ids = set()
    for case in db.security_cases.find({"status": {"$nin": list(TERMINAL)}}, {"target_id": 1, "user_id": 1}):
        for key in ("target_id", "user_id"):
            if case.get(key) is not None:
                ids.add(case[key])
    return ids


def purge(db, now=None, dry_run=False):
    """Apply every rule. Returns a count per rule, named collection:days."""
    now = now or datetime.now(timezone.utc)
    protected = _protected_user_ids(db)
    result = {}
    for collection, date_field, days, fields, extra in RULES:
        query = {date_field: {"$lt": now - days * DAY}, **(extra or {})}
        if fields:
            query["$or"] = [{field: {"$exists": True}} for field in fields]
        if collection == "login_logs" and protected:
            query["user_id"] = {"$nin": list(protected)}
        key = f"{collection}:{days}"
        if dry_run:
            result[key] = db[collection].count_documents(query)
        elif fields:
            result[key] = db[collection].update_many(query, {"$unset": {field: "" for field in fields}}).modified_count
        else:
            result[key] = db[collection].delete_many(query).deleted_count
    return {"dry_run": dry_run, "counts": result, "protected_users": len(protected)}


def is_enabled():
    return os.environ.get("PERSONAL_LOG_RETENTION_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def main(db=None, dry_run=False):
    if not dry_run and not is_enabled():
        return {"enabled": False, "changed": 0}
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    result = purge(db, dry_run=dry_run)
    if not dry_run and any(result["counts"].values()):
        logger.info("Personal log retention: %s", result["counts"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
