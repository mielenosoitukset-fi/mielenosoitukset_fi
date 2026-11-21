"""Close demonstrations nobody decided before their date (nightly).

Maintainer decision (20 Nov 2026): a demonstration that was neither approved
nor rejected before its date is closed as expired ("Vanhentunut") through the
one decision service, so its case closes with a traced reason and it leaves
every pending count. The submitter gets a short apology, but only when the
date passed within ``EXPIRED_DEMO_NOTIFY_DAYS`` (default 14): the first run
must not email people about events from long ago. Cancelled demonstrations
are left alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict
from zoneinfo import ZoneInfo

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.demonstrations.decisions import apply_demo_decision
from mielenosoitukset_fi.utils.logger import logger

DEFAULT_NOTIFY_DAYS = 14
SYSTEM_ACTOR = {"user_id": None, "username": "system", "role": "system", "automatic": True}


def _setting(name, default):
    from flask import current_app, has_app_context

    value = current_app.config.get(name) if has_app_context() else None
    if value is None:
        from config import Config

        value = getattr(Config, name, default)
    return default if value is None else value


def expire_once(db, email_sender, *, today=None) -> Dict[str, int]:
    today = today or datetime.now(ZoneInfo("Europe/Helsinki")).date()
    try:
        notify_days = max(int(_setting("EXPIRED_DEMO_NOTIFY_DAYS", DEFAULT_NOTIFY_DAYS)), 0)
    except (TypeError, ValueError):
        notify_days = DEFAULT_NOTIFY_DAYS
    notify_from = (today - timedelta(days=notify_days)).isoformat()
    query = {
        "approved": {"$ne": True},
        "rejected": {"$ne": True},
        "cancelled": {"$ne": True},
        # ISO dates compare as strings; malformed dates are skipped by the regex.
        "date": {"$lt": today.isoformat(), "$regex": r"^\d{4}-\d{2}-\d{2}$"},
    }
    counts = {"expired": 0, "notified": 0, "quiet": 0, "failed": 0}
    for demo in db.demonstrations.find(query, {"_id": 1, "date": 1}):
        notify = demo.get("date", "") >= notify_from
        try:
            result = apply_demo_decision(
                db, demo["_id"], "expired", actor=SYSTEM_ACTOR, source="nightly_expiry",
                email_sender=email_sender, notify_submitter=notify,
            )
        except Exception:
            counts["failed"] += 1
            logger.exception("Failed to expire undecided demo %s", demo["_id"])
            continue
        if result.changed:
            counts["expired"] += 1
            counts["notified" if result.notification_queued else "quiet"] += 1
    return counts


def main() -> Dict[str, int]:
    """Background job entry point (see background_jobs/definitions.py)."""
    if not _setting("EXPIRE_UNDECIDED_DEMOS", True):
        return {"expired": 0, "disabled": 1}
    from mielenosoitukset_fi.emailer.EmailSender import EmailSender

    db = DatabaseManager().get_instance().get_db()
    return expire_once(db, EmailSender())
