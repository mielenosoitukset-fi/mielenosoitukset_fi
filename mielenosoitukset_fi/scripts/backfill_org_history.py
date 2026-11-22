"""Background job and command: copy old organization admin-log events into the history store.

    python -m mielenosoitukset_fi.scripts.backfill_org_history --dry-run

The organization history page reads ``audit_events``, which started on
20.11.2026. Before that, ``admin_logs`` recorded who created, edited, verified
or deleted an organization (and role, invite and membership changes), but not
the changed values. The maintainer asked (22.11.2026) for those events to be
written into the history store too, so one view covers both eras.

What is written, per old ``admin_logs`` event: one ``audit_events`` row marked
``legacy: true`` with the original time, the admin's user id (no email, name,
IP or request data), an action label, and the only values the old log held:
``verified`` before and after, and the *names* of fields applied from a
suggestion. Nothing is guessed.

Safe to repeat: every row has the deterministic ``_id``
``legacy-admin-log:<admin_logs id>`` and is written with ``$setOnInsert``.
The job runs once to completion: a marker in ``maintenance_markers`` records
the counts, an ``admin_logs`` entry shows the result in the admin log view, and
one summary row in ``audit_events`` (collection ``audit_events``, never shown in
an organization's history) records that the backfill ran. Only events older than
the live trail are copied, so nothing is duplicated. Set
``ORG_HISTORY_BACKFILL_DISABLED=1`` to stop it; ``--dry-run`` only counts.
"""
import argparse
import json
import os
from datetime import datetime, timezone

from bson import ObjectId

from mielenosoitukset_fi.admin.audit_history import LEGACY_EVENTS
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.audit_trail import AUDIT_COLLECTION, SCHEMA_VERSION
from mielenosoitukset_fi.utils.logger import logger

MARKER_ID = "org_history_backfill_2026_11"
ID_PREFIX = "legacy-admin-log:"


def is_disabled():
    return os.environ.get("ORG_HISTORY_BACKFILL_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def _utc(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def trail_started_at(db):
    first = db[AUDIT_COLLECTION].find_one({"legacy": {"$ne": True}}, {"at": 1}, sort=[("at", 1)])
    return _utc(first["at"]) if first else None


def _org_id(entry):
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    for candidate in (details.get("org_id"), details.get("organization_id"), entry.get("related_id")):
        if candidate and ObjectId.is_valid(str(candidate)):
            return ObjectId(str(candidate))
    return None


def build_event(entry, org_id, db_name):
    """One audit_events row for one admin_logs event; None when it has no usable time."""
    at = _utc(entry.get("timestamp"))
    if not isinstance(at, datetime):
        return None
    op_key, label = LEGACY_EVENTS[entry["event"]]
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    actor_id = (entry.get("actor") or {}).get("id") or entry.get("user_id")
    event = {
        "_id": f"{ID_PREFIX}{entry['_id']}",
        "v": SCHEMA_VERSION,
        "at": at,
        "db": db_name,
        "service": "python",
        "collection": "organizations",
        "op": {"create": "insert", "delete": "delete"}.get(op_key, "update"),
        "doc_ids": [org_id],
        "change": [],
        "actor": {"kind": "user", "user_id": str(actor_id)} if actor_id else {"kind": "system"},
        "via": {"kind": "legacy_admin_log", "event": entry["event"]},
        "outcome": {"ok": True},
        "reason": label,
        "legacy": True,
        "source": {"collection": "admin_logs", "id": str(entry["_id"])},
    }
    if entry["event"] == "organization_verified_changed":
        event["change"] = [{"$set": {"verified": bool(details.get("new"))}}]
        event["legacy_before"] = {"verified": bool(details.get("previous"))}
    elif entry["event"] == "organization_suggestion_fields_applied":
        event["legacy_fields"] = [str(name)[:60] for name in (details.get("fields") or [])[:30]]
    return event


def backfill(db, dry_run=False):
    """Copy old events; returns counts so a run on real data shows what it matched."""
    started = trail_started_at(db)
    query = {"event": {"$in": list(LEGACY_EVENTS)}}
    if started is not None:
        query["timestamp"] = {"$lt": started.replace(tzinfo=None)}
    counts = {"dry_run": dry_run, "trail_started_at": started, "events_seen": 0,
              "no_org_id": 0, "no_time": 0, "written": 0, "already_there": 0, "by_event": {}}
    cursor = db.admin_logs.find(query, {"event": 1, "timestamp": 1, "actor.id": 1, "user_id": 1,
                                        "details": 1, "related_id": 1})
    for entry in cursor:
        counts["events_seen"] += 1
        counts["by_event"][entry["event"]] = counts["by_event"].get(entry["event"], 0) + 1
        org_id = _org_id(entry)
        if org_id is None:
            counts["no_org_id"] += 1
            continue
        event = build_event(entry, org_id, db.name)
        if event is None:
            counts["no_time"] += 1
            continue
        if dry_run:
            continue
        event_id = event.pop("_id")
        result = db[AUDIT_COLLECTION].update_one({"_id": event_id}, {"$setOnInsert": event}, upsert=True)
        counts["written" if result.upserted_id is not None else "already_there"] += 1
    return counts


def main(db=None, dry_run=False):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if dry_run:
        return backfill(db, dry_run=True)
    if is_disabled():
        return {"disabled": True}
    if db.maintenance_markers.find_one({"_id": MARKER_ID}):
        return {"already_done": True}
    result = backfill(db)
    finished_at = datetime.now(timezone.utc)
    db.maintenance_markers.update_one(
        {"_id": MARKER_ID}, {"$setOnInsert": {**result, "finished_at": finished_at}}, upsert=True)
    db.admin_logs.insert_one({
        "event": "org_history_backfill",
        "message": "Copied old organization admin-log events into the change history.",
        "details": result,
        "timestamp": finished_at.replace(tzinfo=None),
        "actor": {"username": "system"},
    })
    db[AUDIT_COLLECTION].insert_one({
        "v": SCHEMA_VERSION, "at": finished_at, "db": db.name, "service": "python",
        "collection": AUDIT_COLLECTION, "op": "legacy_backfill", "doc_ids": [], "change": [],
        "actor": {"kind": "job", "name": "backfill_org_history"},
        "via": {"kind": "job", "name": "backfill_org_history"},
        "outcome": {"ok": True, "n": result["written"]}, "legacy": True,
        "reason": "Organization history backfill from admin_logs",
    })
    logger.info("Organization history backfill: %s", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
