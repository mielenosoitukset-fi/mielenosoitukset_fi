"""One registry of every volunteer / interest sign-up list shown in admin.

Each list lives in its own collection (their public forms differ in what they
collect and how long they keep it). The admin page reads them all through this
module, so listing, status, export and deletion work the same for every list.
Rows are never rewritten here: the only admin actions are CSV export and
deletion with a reason, and both leave a row in ``volunteer_admin_actions``.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from bson import ObjectId

ACTIONS_COLLECTION = "volunteer_admin_actions"
MAX_REASON = 500
MIN_REASON = 3

_HELSINKI = ZoneInfo("Europe/Helsinki")


def _campaign_status(row: Dict[str, Any]) -> str:
    return "confirmed" if row.get("confirmed") else "pending"


def _status_field(row: Dict[str, Any]) -> str:
    status = row.get("status")
    return status if status in ("pending", "confirmed") else "other"


ROLE_LABELS = {"stickers": "Tarrat", "pride_banner": "Pride-banderolli", "events": "Tapahtumat",
               "communications": "Viestintä"}
WILLING_LABELS = {"yes": "Kyllä", "maybe": "Ehkä", "no": "Ei"}
BAND_LABELS = {"under10": "Alle 10 €", "10to20": "10–20 €", "over20": "Yli 20 €"}


def _join(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(ROLE_LABELS.get(str(v), str(v)) for v in value)
    if value is None:
        return ""
    text = str(value)
    return WILLING_LABELS.get(text) or BAND_LABELS.get(text) or text


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    collection: str
    # (header, row-field) pairs shown in the table and the CSV
    columns: Tuple[Tuple[str, str], ...]
    status_of: Callable[[Dict[str, Any]], str]
    pending_query: Dict[str, Any]
    confirmed_query: Dict[str, Any]
    purge_after: datetime  # UTC instant when the retention job deletes everything


def _sources() -> Dict[str, Source]:
    from mielenosoitukset_fi.scripts.purge_campaign_volunteers import PURGE_AFTER as campaign_purge
    from mielenosoitukset_fi.utils.campaign_opening_interest import PURGE_AFTER as opening_purge
    from mielenosoitukset_fi.utils.pride_banner_volunteers import PURGE_AFTER as pride_purge

    items = (
        Source(
            "kampanja", "Kampanjan vapaaehtoiset", "volunteers",
            (("Nimi", "name"), ("Sähköposti", "email"), ("Kaupunki", "city"), ("Tehtävät", "roles"),
             ("Alue", "area"), ("Huomiot", "notes")),
            _campaign_status, {"confirmed": {"$ne": True}}, {"confirmed": True}, campaign_purge,
        ),
        Source(
            "pride", "Pride-banderollin kantajat", "pride_banner_volunteers",
            (("Nimi", "name"), ("Sähköposti", "email"), ("Alue", "city"), ("Pride", "pride"), ("Huomiot", "note")),
            _status_field, {"status": "pending"}, {"status": "confirmed"}, pride_purge,
        ),
        Source(
            "avajaiset", "Avajaisten ilmoittautumiset", "campaign_opening_interest",
            (("Sähköposti", "email"), ("Kaupunki", "city"), ("Osallistuu", "willing"), ("Hintaluokka", "band")),
            _status_field, {"status": "pending"}, {"status": "confirmed"}, opening_purge,
        ),
    )
    return {s.key: s for s in items}


def sources() -> Dict[str, Source]:
    return _sources()


STATUS_LABELS = {"confirmed": "Vahvistettu", "pending": "Odottaa vahvistusta", "other": "Muu"}


def csv_cell(value: Any) -> str:
    """Stop spreadsheet programs from running a cell that starts like a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _created(doc: Dict[str, Any]) -> Optional[datetime]:
    """Sign-up time: ``created_at`` where kept, else the row id's creation time."""
    value = doc.get("created_at")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    stamp = getattr(doc.get("_id"), "generation_time", None)
    return stamp


def _query(source: Source, status: str) -> Dict[str, Any]:
    if status == "confirmed":
        return dict(source.confirmed_query)
    if status == "pending":
        return dict(source.pending_query)
    return {}


def counts(db) -> List[Dict[str, Any]]:
    out = []
    for source in sources().values():
        coll = db[source.collection]
        out.append({
            "key": source.key, "label": source.label,
            "total": coll.count_documents({}),
            "confirmed": coll.count_documents(source.confirmed_query),
            "pending": coll.count_documents(source.pending_query),
            "purge_after": source.purge_after.astimezone(_HELSINKI),
        })
    return out


def _shorten(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def get_row(db, source: Source, row_id: str) -> Optional[Dict[str, Any]]:
    try:
        oid = ObjectId(row_id)
    except Exception:
        return None
    doc = db[source.collection].find_one({"_id": oid})
    if not doc:
        return None
    return {"id": row_id, "status": source.status_of(doc), "created_at": _created(doc),
            "cells": [_join(doc.get(field)) for _, field in source.columns]}


def rows(db, source: Source, status: str = "", limit: int = 500) -> List[Dict[str, Any]]:
    cursor = db[source.collection].find(_query(source, status)).sort("_id", -1).limit(limit)
    result = []
    for doc in cursor:
        result.append({
            "id": str(doc["_id"]),
            "status": source.status_of(doc),
            "created_at": _created(doc),
            "cells": [_shorten(_join(doc.get(field))) for _, field in source.columns],
        })
    return result


def export_csv(db, source: Source, status: str = "") -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([h for h, _ in source.columns] + ["Tila", "Ilmoittautunut"])
    for doc in db[source.collection].find(_query(source, status)).sort("_id", 1):
        created = _created(doc)
        writer.writerow(
            [csv_cell(_join(doc.get(f))) for _, f in source.columns]
            + [STATUS_LABELS[source.status_of(doc)], created.isoformat() if created else ""]
        )
    return out.getvalue()


def record_action(db, source: Source, action: str, actor_id: Any, *, count: int = 0,
                  reason: str = "", row_id: str = "", snapshot: Optional[Dict[str, Any]] = None) -> None:
    """Write the who/what/why row. Never stores the person's name, address or notes."""
    db[ACTIONS_COLLECTION].insert_one({
        "at": datetime.now(timezone.utc), "action": action, "source": source.key,
        "collection": source.collection, "actor": str(actor_id), "count": count,
        "reason": reason, "row_id": row_id, "snapshot": snapshot or {},
    })


def clean_reason(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    return text[:MAX_REASON] if len(text) >= MIN_REASON else ""


def delete_row(db, source: Source, row_id: str, actor_id: Any, reason: str) -> bool:
    try:
        oid = ObjectId(row_id)
    except Exception:
        return False
    doc = db[source.collection].find_one({"_id": oid})
    if not doc:
        return False
    record_action(db, source, "delete", actor_id, count=1, reason=reason, row_id=row_id,
                  snapshot={"status": source.status_of(doc), "city": str(doc.get("city") or "")[:100]})
    return db[source.collection].delete_one({"_id": oid}).deleted_count == 1


def recent_actions(db, limit: int = 20) -> List[Dict[str, Any]]:
    return list(db[ACTIONS_COLLECTION].find({}).sort("at", -1).limit(limit))
