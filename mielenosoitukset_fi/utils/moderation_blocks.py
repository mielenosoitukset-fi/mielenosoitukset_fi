"""Organizer bans and publishing precedents that Hallinto decides and Flask enforces.

Hallinto records the decision of Johtoryhmä and sends it here. A ban names a
party that may not submit demonstrations; a precedent (ennakkopäätös) names a
demonstration or an organization whose demonstrations are not published. Each
record is stored by Hallinto's id, so sending it again changes nothing, and a
lifted one keeps its end day.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from bson import ObjectId

COLLECTION = "moderation_blocks"
KINDS = ("organizer", "demo", "organization")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_ID = re.compile(r"[0-9a-f]{24}\Z")


def normalize(name) -> str:
    """Compare names without case or spacing differences."""
    return " ".join(str(name or "").casefold().split())


def _day(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _DAY.match(value):
        raise ValueError("invalid day")
    return datetime.strptime(value, "%Y-%m-%d")


def sync(db, items, *, now: datetime | None = None) -> int:
    """Store the sent records by id. Returns how many were stored."""
    if not isinstance(items, list) or len(items) > 200:
        raise ValueError("invalid items")
    documents = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid item")
        block_id, kind = item.get("id"), item.get("kind")
        organizer, reason = item.get("organizer"), item.get("reason")
        organization_id = item.get("organization_id") or None
        if (not isinstance(block_id, str) or not 0 < len(block_id) <= 128 or kind not in KINDS
                or not isinstance(organizer, str) or not 0 < len(organizer.strip()) <= 200
                or not isinstance(reason, str) or len(reason) > 2000
                or (organization_id is not None and not (isinstance(organization_id, str) and _ID.match(organization_id)))
                or (kind != "organizer" and organization_id is None)):
            raise ValueError("invalid item")
        starts, ends = _day(item.get("from")), _day(item.get("until"))
        if starts is None or (ends is not None and ends <= starts):
            raise ValueError("invalid days")
        documents.append({
            "_id": block_id, "kind": kind, "name": organizer.strip(), "name_key": normalize(organizer),
            "organization_id": ObjectId(organization_id) if organization_id else None,
            "reason": reason, "from": starts, "until": ends,
            "source": str(item.get("source") or "")[:500],
        })
    for document in documents:
        db[COLLECTION].replace_one({"_id": document["_id"]}, document, upsert=True)
    return len(documents)


def _in_force(day):
    return {"from": {"$lte": day}, "$or": [{"until": None}, {"until": {"$gt": day}}]}


def _today(now: datetime | None = None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return datetime(now.year, now.month, now.day)


def banned_organizer(db, names=(), organization_ids=(), *, now: datetime | None = None):
    """The ban in force on a party that may not submit, or None."""
    keys = [key for key in (normalize(name) for name in names) if key]
    oids = [ObjectId(str(value)) for value in organization_ids if value and ObjectId.is_valid(str(value))]
    conditions = []
    if keys:
        conditions.append({"name_key": {"$in": keys}})
    if oids:
        conditions.append({"organization_id": {"$in": oids}})
    if not conditions:
        return None
    return db[COLLECTION].find_one({"kind": "organizer", "$and": [{"$or": conditions}, _in_force(_today(now))]})


def precedent_for(db, demo: dict, *, now: datetime | None = None):
    """The precedent in force that keeps this demonstration from being published, or None."""
    targets = []
    if isinstance(demo.get("_id"), ObjectId):
        targets.append({"kind": "demo", "organization_id": demo["_id"]})
    oids = []
    for organizer in demo.get("organizers") or []:
        value = organizer.get("organization_id") if isinstance(organizer, dict) else None
        if value and ObjectId.is_valid(str(value)):
            oids.append(ObjectId(str(value)))
    if oids:
        targets.append({"kind": "organization", "organization_id": {"$in": oids}})
    if not targets:
        return None
    return db[COLLECTION].find_one({"$and": [{"$or": targets}, _in_force(_today(now))]})


def demo_block(db, demo: dict, *, now: datetime | None = None):
    """The Johtoryhmä decision in force that already rules out this demonstration.

    Either a publishing precedent that names it or one of its organizations, or
    a ban on one of its organizers. Used to offer that decision as the reject
    reason, so the history points at the decision instead of a free text.
    """
    found = precedent_for(db, demo, now=now)
    if found:
        return found
    organizers = [o for o in (demo.get("organizers") or []) if isinstance(o, dict)]
    return banned_organizer(
        db, [o.get("name") for o in organizers],
        [o.get("organization_id") for o in organizers], now=now,
    )


def decision_ref(block: dict) -> str:
    """The decision's id as Hallinto recorded it (its reference, else the record id)."""
    return (block.get("source") or "").strip() or str(block["_id"])
