"""Readable change history of one document, built from ``audit_events``.

Every write is recorded by ``utils/audit_trail.py``. This module turns the
events for one document (an organization, a demonstration) into rows an admin
can read: when, who, through which path, and each field's old and new value.
The trail stores the change applied, so the old value of a field is the value
the previous recorded event set; before the trail started it is unknown.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from bson import ObjectId
from flask_babel import gettext as _

from mielenosoitukset_fi.utils.audit_trail import AUDIT_COLLECTION, REDACTED

MAX_EVENTS = 500  # oldest-first window used to work out old values
UNKNOWN = object()
HELSINKI = ZoneInfo("Europe/Helsinki")

# Bookkeeping fields every save touches; listing them on each row is noise.
_NOISE_FIELDS = {"updated_at", "last_modified", "modified_at", "_id"}


def _id_forms(doc_id: Any) -> List[Any]:
    forms: List[Any] = [str(doc_id)]
    if isinstance(doc_id, ObjectId):
        forms.append(doc_id)
    elif ObjectId.is_valid(str(doc_id)):
        forms.append(ObjectId(str(doc_id)))
    return forms


def history_query(collection: str, doc_id: Any) -> Dict[str, Any]:
    ids = _id_forms(doc_id)
    return {
        "collection": collection,
        "$or": [
            {"doc_ids": {"$in": ids}},
            {"outcome.upserted": {"$in": ids}},
            {"outcome.doc_id": {"$in": ids}},
        ],
    }


def _flatten(value: Any, prefix: str = "") -> Dict[str, Any]:
    """Nested dicts to dotted paths, so a nested $set and a dotted one compare."""
    if isinstance(value, dict) and value and not any(str(k).startswith("$") for k in value):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(item, path))
        return out
    return {prefix: value} if prefix else {}


def _field_changes(event: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """field -> {"kind": set|unset|list, "value": ...} for one event."""
    changes: Dict[str, Dict[str, Any]] = {}
    op = event.get("op")
    if op == "insert":
        for doc in event.get("change") or []:
            for field, value in _flatten(doc).items():
                changes[field] = {"kind": "set", "value": value}
        return changes
    for change in event.get("change") or []:
        if not isinstance(change, dict):
            continue
        if not any(str(k).startswith("$") for k in change):
            # Replacement document.
            for field, value in _flatten(change).items():
                changes[field] = {"kind": "set", "value": value}
            continue
        for operator, fields in change.items():
            if not isinstance(fields, dict):
                continue
            if operator in ("$set", "$setOnInsert"):
                for field, value in _flatten(fields).items():
                    changes[field] = {"kind": "set", "value": value}
            elif operator == "$unset":
                for field in fields:
                    changes[field] = {"kind": "unset", "value": None}
            else:
                for field, value in fields.items():
                    changes[field] = {"kind": operator.lstrip("$"), "value": value}
    return changes


def _actor_label(actor: Dict[str, Any], users: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    kind = (actor or {}).get("kind") or "system"
    user_id = actor.get("user_id") if actor else None
    user = users.get(str(user_id)) if user_id else None
    if user:
        return {"kind": "user", "name": user.get("displayname") or user.get("username"),
                "username": user.get("username")}
    labels = {
        "user": _("Poistettu tai tuntematon käyttäjä"),
        "api_token": _("API-avain"),
        "mcp": _("MCP-yhteys"),
        "link": _("Muokkauslinkki"),
        "anonymous": _("Kirjautumaton käyttäjä"),
        "job": _("Taustatyö"),
        "system": _("Järjestelmä"),
    }
    name = labels.get(kind, kind)
    if kind == "job" and actor.get("name"):
        name = f"{name}: {actor['name']}"
    return {"kind": kind, "name": name, "username": None}


def _via_label(via: Dict[str, Any]) -> str:
    via = via or {}
    kind = via.get("kind")
    if kind == "http":
        service = _("Go-rajapinta") if via.get("service") == "go-api" else _("Verkkosivu")
        route = via.get("rule") or via.get("endpoint") or ""
        return f"{service} · {via.get('method') or ''} {route}".strip()
    if kind == "job":
        return f"{_('Taustatyö')} · {via.get('name') or ''}".strip()
    if kind == "legacy_admin_log":
        return _("Vanha ylläpitoloki")
    if kind in ("process", "script"):
        return f"{_('Skripti')} · {via.get('script') or via.get('name') or ''}".strip()
    return kind or "—"


def _op_label(op: str, outcome: Dict[str, Any]) -> Dict[str, str]:
    if op == "insert" or (op == "update" and outcome.get("upserted")):
        return {"key": "create", "label": _("Luotu")}
    if op == "delete":
        return {"key": "delete", "label": _("Poistettu")}
    if str(op).startswith("ddl:"):
        return {"key": "schema", "label": _("Rakennemuutos")}
    return {"key": "update", "label": _("Muokattu")}


def helsinki_time(value: datetime, *, seconds: bool = True) -> str:
    """Stored times are UTC (naive from PyMongo); admins read Helsinki time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(HELSINKI).strftime("%d.%m.%Y %H:%M:%S" if seconds else "%d.%m.%Y %H:%M")


def display_value(value: Any) -> str:
    if value is UNKNOWN:
        return "—"
    if value is None or value == "" or value == [] or value == {}:
        return _("(tyhjä)")
    if value == REDACTED:
        return _("(piilotettu)")
    if value == "[str]":
        return _("(teksti)")
    if isinstance(value, bool):
        return _("kyllä") if value else _("ei")
    if isinstance(value, (list, tuple)):
        return ", ".join(display_value(v) for v in value) or "[]"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {display_value(v)}" for k, v in value.items()) or "{}"
    if isinstance(value, datetime):
        return helsinki_time(value, seconds=False)
    return str(value)


def load_history(db, collection: str, doc_id: Any) -> Dict[str, Any]:
    """Return newest-first history rows (at most MAX_EVENTS) for one document."""
    query = history_query(collection, doc_id)
    audit = db[AUDIT_COLLECTION]
    total = audit.count_documents(query)
    events = list(audit.find(query).sort([("at", -1), ("_id", -1)]).limit(MAX_EVENTS))
    events.reverse()  # oldest first to carry values forward

    user_ids = {
        ObjectId(e["actor"]["user_id"])
        for e in events
        if (e.get("actor") or {}).get("user_id") and ObjectId.is_valid(str(e["actor"]["user_id"]))
    }
    users = {
        str(u["_id"]): u
        for u in db.users.find({"_id": {"$in": list(user_ids)}}, {"username": 1, "displayname": 1})
    } if user_ids else {}

    known: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    first_known_create = False
    for event in events:
        outcome = event.get("outcome") or {}
        op = _op_label(event.get("op", ""), outcome)
        if op["key"] == "create":
            first_known_create = True
        succeeded = bool(outcome.get("ok", True)) and not outcome.get("errors")
        fields = []
        legacy = bool(event.get("legacy"))
        for name in event.get("legacy_fields") or []:
            fields.append({"field": str(name), "kind": "set", "before": None, "after": _("(arvo ei tiedossa)")})
        for field, change in sorted(_field_changes(event).items()):
            if field in _NOISE_FIELDS:
                continue
            before = known.get(field, UNKNOWN) if op["key"] != "create" else UNKNOWN
            if legacy and field in (event.get("legacy_before") or {}):
                before = event["legacy_before"][field]
            after = change["value"] if change["kind"] == "set" else None
            # A failed write changed nothing, so it does not move old values on.
            if succeeded and change["kind"] == "set":
                known[field] = change["value"]
            elif succeeded and change["kind"] == "unset":
                known.pop(field, None)
            if change["kind"] == "unset":
                after_text = _("(poistettu)")
            elif change["kind"] == "set":
                after_text = display_value(after)
            else:
                after_text = f"{change['kind']}: {display_value(change['value'])}"
            fields.append({
                "field": field,
                "kind": change["kind"],
                "before": None if before is UNKNOWN else display_value(before),
                "after": after_text,
            })
        rows.append({
            "id": str(event.get("_id")),
            "at": event.get("at"),
            "at_display": helsinki_time(event["at"]) if isinstance(event.get("at"), datetime) else "—",
            "op": op,
            "ok": succeeded,
            "actor": _actor_label(event.get("actor") or {}, users),
            "via": _via_label(event.get("via") or {}),
            "via_is_actor": (event.get("via") or {}).get("kind") == "job"
            and (event.get("actor") or {}).get("kind") == "job",
            "reason": event.get("reason"),
            "fields": fields,
            "multi": bool(event.get("multi")),
            "legacy": legacy,
        })
    rows.reverse()  # newest first for display
    return {
        "rows": rows,
        "total": total,
        "has_create": first_known_create,
        "truncated": total > MAX_EVENTS,
    }


def first_event_at(db) -> Optional[Any]:
    """When the trail started in this database (for the 'history starts' note)."""
    first = db[AUDIT_COLLECTION].find_one({"legacy": {"$ne": True}}, {"at": 1}, sort=[("at", 1)])
    return first.get("at") if first else None



# Before the audit trail existed, admin_logs recorded who did what to an
# organization, but not the changed field values. scripts/backfill_org_history.py
# copies these events into audit_events (marked ``legacy``) so one history view
# shows both eras. Value: (operation, Finnish label shown to admins).
LEGACY_EVENTS = {
    "organization_created": ("create", "Organisaatio luotu"),
    "organization_created_api": ("create", "Organisaatio luotu rajapinnan kautta"),
    "organization_update": ("update", "Organisaation tietoja muokattu"),
    "organization_verified_changed": ("update", "Vahvistus muutettu"),
    "organization_deleted": ("delete", "Organisaatio poistettu"),
    "organization_suggestion_fields_applied": ("update", "Muutosehdotus hyväksytty"),
    "organization_access_changed": ("update", "Käyttäjän rooli organisaatiossa muutettu"),
    "organization_invite_sent": ("update", "Kutsu lähetetty"),
    "organization_invite_cancelled": ("update", "Kutsu peruttu"),
    "organization_invite_force_accepted": ("update", "Jäsen lisätty ylläpidosta"),
    "organization_invite_role_set": ("update", "Kutsun rooli asetettu"),
    "membership_deleted": ("update", "Jäsen poistettu"),
}


def estimated_creation_row(doc_id: Any) -> Optional[Dict[str, Any]]:
    """A read-only row for the creation time an ObjectId carries.

    Used when no recorded or backfilled creation exists: who created the
    document is then unknown, but its id says when.
    """
    if not isinstance(doc_id, ObjectId):
        return None
    at = doc_id.generation_time
    return {
        "id": f"estimated-{doc_id}",
        "at": at,
        "at_display": helsinki_time(at),
        "op": {"key": "create", "label": _("Luotu")},
        "ok": True,
        "actor": {"kind": "unknown", "name": _("Tekijä ei tiedossa"), "username": None},
        "via": _("Arvio organisaation tunnisteesta"),
        "via_is_actor": False,
        "reason": _("Luontiaika arvioitu; tekijää ei ole kirjattu."),
        "fields": [],
        "multi": False,
        "legacy": False,
        "estimated": True,
    }


def creation_diagnostics(db, doc_id: Any) -> Dict[str, Any]:
    """Which old log sources hold a creator for one organization (read-only).

    Shown to admins when no creation row exists, so "no creator" can be told apart
    from "the backfill missed it": counts of old events for the organization, of
    organization-create requests logged around the id's creation time, and the
    counts the backfill job recorded when it finished.
    """
    from datetime import timedelta

    if not isinstance(doc_id, ObjectId):
        return {}
    at = doc_id.generation_time
    naive = at.replace(tzinfo=None)
    key = str(doc_id)
    events = []
    for row in db.admin_logs.find(
        {"event": {"$in": list(LEGACY_EVENTS)}, "$or": [{"related_id": key}, {"details.org_id": key},
                                                         {"details.organization_id": key}]},
        {"event": 1, "timestamp": 1, "actor.username": 1}).sort("timestamp", 1).limit(10):
        events.append({"event": row.get("event"), "at": helsinki_time(row["timestamp"]) if isinstance(
            row.get("timestamp"), datetime) else None, "actor": (row.get("actor") or {}).get("username")})
    window = timedelta(seconds=60)
    requests = []
    for row in db.admin_logs.find(
        {"timestamp": {"$gte": naive - window, "$lte": naive + window}, "request.method": "POST",
         "request.path": {"$in": ["/admin/organization/create", "/admin/organization/create/"]}},
        {"timestamp": 1, "actor.username": 1, "user.username": 1}).limit(5):
        requests.append({"at": helsinki_time(row["timestamp"]), "actor": (row.get("actor") or {}).get("username")
                         or (row.get("user") or {}).get("username")})
    markers = []
    for marker in db.maintenance_markers.find({"_id": {"$regex": "^org_history_backfill"}}):
        done = marker.get("finished_at")
        counts = {k: v for k, v in marker.items() if isinstance(v, int) and not isinstance(v, bool)}
        markers.append({"id": marker["_id"], "finished": helsinki_time(done) if isinstance(done, datetime) else None,
                        "counts": counts})
    return {"created_at": helsinki_time(at), "events": events, "create_requests": requests,
            "markers": markers}
