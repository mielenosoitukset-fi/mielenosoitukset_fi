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
import re
import time
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from mielenosoitukset_fi.admin.audit_history import LEGACY_EVENTS
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.audit_trail import AUDIT_COLLECTION, SCHEMA_VERSION
from mielenosoitukset_fi.utils.logger import logger

# The first run (22.11.2026) found no old logs: prod's admin_logs started 20.11.2026, the older
# ones lived on the previous database host. They were copied over afterwards, which writes
# IMPORT_DONE_ID; the runs below wait for it and use new markers so they run once on the full data.
IMPORT_DONE_ID = "old_logs_imported_from_lc_db"
MARKER_ID = "org_history_backfill_2026_11_full"
FIELDS_MARKER_ID = "org_history_backfill_2026_11_full_fields"
FIELDS_FIRST_DAY = datetime(2024, 11, 1)  # first row in admin_logs
EDIT_PATH = re.compile(r"^/admin/organization/edit/([0-9a-f]{24})/?$")
CREATE_PATH = re.compile(r"^/admin/organization/create/?$")
CREATE_WINDOW = timedelta(seconds=15)  # the organization is inserted within the request
# Only these names are shown: the old rows hold names of posted fields, never values.
ORG_FIELDS = ("name", "description", "email", "website", "logo_url", "verified",
              "social_media_platform[]", "social_media_url[]")
MATCH_WINDOW = timedelta(seconds=15)
PAUSE = 0.1
EDIT_LABEL = LEGACY_EVENTS["organization_update"][1]
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


def backfill_sent_fields(db, dry_run=False, pause=None):
    """Add the names of the fields an admin sent to old organization edit rows.

    Also gives organizations created in the admin form (no ``organization_created``
    event before 6.1.2026) their creator: the create request row's admin and time are
    matched to the one organization whose ``_id`` time falls within 15 seconds after it.

    A browser request row (``admin_logs.request`` with ``form_keys``) is matched to
    the backfilled edit event of the same organization and admin within 15 seconds
    and gives it ``legacy_fields``; with no match it becomes its own row. Rows
    logged twice (two request hooks) collapse into one because they match the
    same event or the same new row. Values were never logged, so none are shown.
    """
    started = trail_started_at(db)
    end = started.replace(tzinfo=None) if started else datetime.now(timezone.utc).replace(tzinfo=None)
    counts = {"dry_run": dry_run, "request_rows": 0, "matched_events": 0, "new_rows": 0,
              "create_rows": 0, "create_known": 0, "create_unmatched": 0}
    day = FIELDS_FIRST_DAY
    while day < end:
        window = {"timestamp": {"$gte": day, "$lt": min(day + timedelta(days=1), end)},
                  "request.method": "POST"}
        for row in db.admin_logs.find(window, {"timestamp": 1, "actor.user_id": 1, "user._id": 1, "user.id": 1,
                                               "request.path": 1, "request.form_keys": 1}):
            path = str((row.get("request") or {}).get("path") or "")
            at = _utc(row["timestamp"])
            actor = (row.get("actor") or {}).get("user_id") or (row.get("user") or {}).get("_id") \
                or (row.get("user") or {}).get("id")
            actor_doc = {"kind": "user", "user_id": str(actor)} if actor else {"kind": "system"}
            base = {"v": SCHEMA_VERSION, "at": at, "db": db.name, "service": "python",
                    "collection": "organizations", "change": [], "actor": actor_doc,
                    "outcome": {"ok": True}, "legacy": True,
                    "source": {"collection": "admin_logs", "id": str(row["_id"])}}
            if CREATE_PATH.match(path):
                # The request row has no organization id; the organization inserted
                # within seconds is the one it created (its _id carries the time).
                counts["create_rows"] += 1
                if dry_run:
                    continue
                made = list(db.organizations.find(
                    {"_id": {"$gte": ObjectId.from_datetime(at), "$lte": ObjectId.from_datetime(at + CREATE_WINDOW)}},
                    {"_id": 1}).limit(2))
                if len(made) != 1:
                    counts["create_unmatched"] += 1
                    continue
                org_id = made[0]["_id"]
                # Skip when the event backfill already wrote this organization's creation.
                if db[AUDIT_COLLECTION].find_one({"collection": "organizations", "doc_ids": org_id,
                                                  "op": "insert", "legacy": True}):
                    counts["create_known"] += 1
                    continue
                event = {**base, "op": "insert", "doc_ids": [org_id],
                         "via": {"kind": "legacy_admin_log", "event": "organization_create_request"},
                         "reason": LEGACY_EVENTS["organization_created"][1]}
                if db[AUDIT_COLLECTION].update_one({"_id": f"legacy-admin-create:{org_id}"}, {"$setOnInsert": event},
                                                   upsert=True).upserted_id:
                    counts["new_rows"] += 1
                continue
            found = EDIT_PATH.match(path)
            if not found:
                continue
            counts["request_rows"] += 1
            names = [k for k in ((row["request"].get("form_keys")) or []) if k in ORG_FIELDS]
            if dry_run:
                continue
            org_id = ObjectId(found.group(1))
            match = {"collection": "organizations", "doc_ids": org_id, "op": "update",
                     "at": {"$gte": at - MATCH_WINDOW, "$lte": at + MATCH_WINDOW}, "legacy": True,
                     "reason": EDIT_LABEL}
            if actor:
                match["actor.user_id"] = str(actor)
            if names:
                matched = db[AUDIT_COLLECTION].update_many(match, {"$set": {"legacy_fields": names}}).matched_count
            else:
                matched = db[AUDIT_COLLECTION].count_documents(match, limit=1)
            if matched:
                counts["matched_events"] += 1
                continue
            event = {**base, "op": "update", "doc_ids": [org_id], "legacy_fields": names,
                     "via": {"kind": "legacy_admin_log", "event": "organization_edit_request"},
                     "reason": EDIT_LABEL}
            # Second request row of the same edit: same half minute, same org and admin.
            key = f"legacy-admin-form:{org_id}:{actor}:{int(at.timestamp()) // 30}"
            if db[AUDIT_COLLECTION].update_one({"_id": key}, {"$setOnInsert": event}, upsert=True).upserted_id:
                counts["new_rows"] += 1
        day += timedelta(days=1)
        time.sleep(PAUSE if pause is None else pause)
    return counts


def main(db=None, dry_run=False):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if dry_run:
        return backfill(db, dry_run=True)
    if is_disabled():
        return {"disabled": True}
    if not db.maintenance_markers.find_one({"_id": IMPORT_DONE_ID}):
        return {"waiting_for": IMPORT_DONE_ID}
    out = {}
    if not db.maintenance_markers.find_one({"_id": MARKER_ID}):
        out["events"] = _finish(db, MARKER_ID, backfill(db), "legacy_backfill",
                                "Copied old organization admin-log events into the change history.")
    if not db.maintenance_markers.find_one({"_id": FIELDS_MARKER_ID}):
        out["fields"] = _finish(db, FIELDS_MARKER_ID, backfill_sent_fields(db), "legacy_backfill_fields",
                                "Added names of sent fields to old organization edits in the change history.")
    return out or {"already_done": True}


def _finish(db, marker_id, result, op, message):
    finished_at = datetime.now(timezone.utc)
    db.maintenance_markers.update_one(
        {"_id": marker_id}, {"$setOnInsert": {**result, "finished_at": finished_at}}, upsert=True)
    db.admin_logs.insert_one({
        "event": "org_history_backfill", "message": message, "details": result,
        "timestamp": finished_at.replace(tzinfo=None), "actor": {"username": "system"},
    })
    db[AUDIT_COLLECTION].insert_one({
        "v": SCHEMA_VERSION, "at": finished_at, "db": db.name, "service": "python",
        "collection": AUDIT_COLLECTION, "op": op, "doc_ids": [], "change": [],
        "actor": {"kind": "job", "name": "backfill_org_history"},
        "via": {"kind": "job", "name": "backfill_org_history"},
        "outcome": {"ok": True, "n": result.get("written", result.get("matched_events", 0))}, "legacy": True,
        "reason": message,
    })
    logger.info("Organization history backfill (%s): %s", op, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
