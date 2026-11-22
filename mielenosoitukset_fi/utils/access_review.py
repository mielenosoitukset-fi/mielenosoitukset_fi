"""Review of site admin accounts twice a year (decided in Hallinto, enforced here).

Review rounds end on 1.1. and 1.7. (Helsinki midnight); the first is 1.1.2027.
Every account with elevated rights gets ``access_review`` on its user document
with the next round's deadline. A Hallinto decision confirms it (order kind
``access_review``, see ``admin/flask_orders``) and moves the deadline one round
on. An account past its deadline is a plain user on every read, like an expired
role bundle: no cron, nothing is deleted, and a later confirmation brings the
rights back. Confirming is only possible in the 45 days before a deadline (or
when never confirmed), so rounds cannot be skipped ahead. Re-running ``stamp``
picks up accounts elevated since.

Nothing here applies until ``ACCESS_REVIEW_ENFORCE`` is on. Olivia's account
and the ids in ``ACCESS_REVIEW_PROTECTED`` never lapse, so the site cannot be
locked out.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from bson import ObjectId
from flask import current_app, has_app_context

from mielenosoitukset_fi.utils.time_utils import utcnow

FIELD = "access_review"
HELSINKI = ZoneInfo("Europe/Helsinki")
# 1.1.2027 00:00 Helsinki, as naive UTC (the storage contract): the first round.
DEADLINE = datetime(2026, 12, 31, 22, 0, 0)
WINDOW_DAYS = 45  # Confirming opens this long before a deadline.
MIN_NOTICE_DAYS = 30  # A round closer than this is skipped for newly stamped accounts.
REMINDER_DAYS = (30, 14, 7, 3, 1)
SITE_ROLES = {"admin": "Ylläpitäjä", "translator": "Kääntäjä", "city_admin": "Kaupunkiylläpitäjä", "org_admin": "Organisaation ylläpitäjä",
              "global_admin": "Pääkäyttäjä", "superuser": "Superuser", "god": "God"}
TRANSLATION = {"TRANSLATE_DEMO", "REVIEW_DEMO_TRANSLATIONS", "TRANSLATE_UI", "REVIEW_UI_TRANSLATIONS"}


def _config(key, default=None):
    if has_app_context():
        return current_app.config.get(key, default)
    return os.environ.get(key, default)


def enforced() -> bool:
    value = _config("ACCESS_REVIEW_ENFORCE", False)
    return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "on", "yes"}


def protected_ids() -> set[str]:
    raw = _config("ACCESS_REVIEW_PROTECTED", "") or ""
    ids = set(raw if isinstance(raw, (list, tuple, set)) else str(raw).replace(",", " ").split())
    olivia = _config("OVERRIDE_OLIVIA_USER_ID")
    if olivia:
        ids.add(str(olivia))
    return {str(i) for i in ids if ObjectId.is_valid(str(i))}


def _to_naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def next_round(after: datetime) -> datetime:
    """The first 1.1. or 1.7. (Helsinki midnight, naive UTC) strictly after ``after``."""
    local = after.replace(tzinfo=timezone.utc).astimezone(HELSINKI)
    year = local.year
    for candidate in (datetime(year, 1, 1), datetime(year, 7, 1), datetime(year + 1, 1, 1)):
        moment = _to_naive_utc(candidate.replace(tzinfo=HELSINKI))
        if moment > after:
            return moment
    raise AssertionError("unreachable")


def _deadline(review: dict) -> datetime:
    return review.get("deadline") or DEADLINE


def is_open(review: dict, now: datetime) -> bool:
    """Waiting for a decision: never confirmed, or the deadline is near."""
    return not review.get("confirmed_at") or _deadline(review) - now <= timedelta(days=WINDOW_DAYS)


def lapsed_doc(doc: dict, now: Optional[datetime] = None) -> bool:
    """True when the stored account is stamped and past its deadline."""
    review = (doc or {}).get(FIELD) or {}
    if not review.get("required") or not enforced():
        return False
    if str(doc.get("_id")) in protected_ids():
        return False
    return (now or utcnow()) >= _deadline(review)


def holds(db, doc: dict) -> list[str]:
    """Plain Finnish labels for every source of site-wide or scoped admin rights."""
    from mielenosoitukset_fi.utils.duty_shifts import is_moderator
    from mielenosoitukset_fi.utils.scope_grants import live_grant_filter
    from mielenosoitukset_fi.utils.system_teams import user_team_access

    labels = []
    role = doc.get("role")
    if role in SITE_ROLES:
        labels.append(SITE_ROLES[role])
    if doc.get("global_admin") is True and role not in {"global_admin", "god", "superuser"}:
        labels.append("Pääkäyttäjälippu")
    perms = set(doc.get("global_permissions") or [])
    if perms - TRANSLATION - {p for p in perms if str(p).startswith("API_")}:
        labels.append("Yleisoikeudet (koko sivusto)")
    elif perms & TRANSLATION:
        labels.append("Kääntäjä")
    if any(str(p).startswith("API_") for p in perms):
        labels.append("API-käyttö")
    uid = doc["_id"]
    grants = list(db.admin_scope_grants.find({"user_id": {"$in": [uid, str(uid)]}, **live_grant_filter()}))
    if grants:
        labels.append("Kaupunki- tai aluekohtaiset oikeudet")
    if is_moderator(db, uid):
        labels.append("Kansallinen moderaattori")
    team = user_team_access(db, uid)
    if "national" in team:
        labels.append("Kansallinen tiimi (koko sivusto)")
    if "board" in team:
        labels.append("Hallituksen tiimi")
    return list(dict.fromkeys(labels))


def stamp(db, apply: bool = False, now: Optional[datetime] = None) -> dict:
    """Mark every active account with admin rights. Dry run unless ``apply``."""
    now = now or utcnow()
    due = next_round(now)
    if due - now < timedelta(days=MIN_NOTICE_DAYS):
        due = next_round(due)
    rows, stamped = [], 0
    for doc in db.users.find({"active": {"$ne": False}}):
        labels = holds(db, doc)
        if not labels:
            continue
        existing = doc.get(FIELD) or {}
        rows.append({"id": str(doc["_id"]), "username": doc.get("username"), "holds": labels,
                     "state": ("open" if not existing.get("required") or is_open(existing, now) else "confirmed")})
        if apply and not existing.get("required"):
            db.users.update_one({"_id": doc["_id"], f"{FIELD}.required": {"$ne": True}},
                                {"$set": {FIELD: {"required": True, "deadline": due, "stamped_at": now,
                                                  "holds": labels}}})
            stamped += 1
    return {"applied": apply, "holders": len(rows), "newly_stamped": stamped, "accounts": rows}


def hallinto_list(db, now: Optional[datetime] = None) -> list[dict]:
    """Stamped accounts for Hallinto's review list. No email addresses."""
    now = now or utcnow()
    protected = protected_ids()
    items = []
    for doc in db.users.find({f"{FIELD}.required": True}).sort("username", 1):
        review = doc[FIELD]
        state = ("protected" if str(doc["_id"]) in protected and is_open(review, now)
                 else "lapsed" if lapsed_doc(doc, now)
                 else "open" if is_open(review, now) else "confirmed")
        items.append({"id": str(doc["_id"]), "username": doc.get("username"),
                      "name": doc.get("displayname") or "", "holds": review.get("holds") or [],
                      "state": state, "deadline": _deadline(review).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "decision": review.get("decision_id") or ""})
    return items


def confirm(db, accounts: list[str], *, decision_id: str, decision_revision: int, order_id: str, actor_id) -> bool:
    """Record the decision on each stamped account and move its deadline one round on.

    Safe to repeat with the same order: an account already confirmed by this order is left alone.
    """
    now = utcnow()
    for account in accounts:
        doc = db.users.find_one({"_id": ObjectId(account), f"{FIELD}.required": True}) or {}
        review = doc.get(FIELD) or {}
        if not review or review.get("order_id") == order_id:
            continue
        # Never earlier than the round after now, so a lapsed account gets a full round.
        deadline = next_round(max(_deadline(review), now))
        db.users.update_one(
            {"_id": doc["_id"]},
            {"$set": {f"{FIELD}.deadline": deadline, f"{FIELD}.confirmed_at": now, f"{FIELD}.decision_id": decision_id,
                      f"{FIELD}.decision_revision": decision_revision, f"{FIELD}.order_id": order_id,
                      f"{FIELD}.confirmed_by": str(actor_id)}})
    return confirmed_accounts(db, accounts, order_id) == sorted(accounts)


def confirmed_accounts(db, accounts: list[str], order_id: str) -> list[str]:
    ids = [ObjectId(a) for a in accounts]
    return sorted(str(d["_id"]) for d in db.users.find({"_id": {"$in": ids}, f"{FIELD}.order_id": order_id}, {"_id": 1}))


def confirmable(db, accounts: list[str], now: Optional[datetime] = None) -> bool:
    """Every account is stamped and waiting for a decision (a repeat of a finished order is checked elsewhere)."""
    now = now or utcnow()
    docs = list(db.users.find({"_id": {"$in": [ObjectId(a) for a in accounts]}}))
    return len(docs) == len(accounts) and all(
        (d.get(FIELD) or {}).get("required") and is_open(d[FIELD], now) for d in docs)


def send_reminders(db, now: Optional[datetime] = None, dry_run: bool = True) -> dict:
    """Queue one mail per open holder on each reminder day before their deadline.

    One mail per person, deadline and day (fixed queue id), so running daily is safe.
    """
    from mielenosoitukset_fi.emailer.EmailJob import EmailJob

    now = now or utcnow()
    queued = due = 0
    for doc in db.users.find({f"{FIELD}.required": True}):
        review = doc[FIELD]
        left = (_deadline(review) - now).days
        if left not in REMINDER_DAYS or not is_open(review, now) or str(doc["_id"]) in protected_ids():
            continue
        due += 1
        if dry_run or not doc.get("email"):
            continue
        body = (f"Hei {doc.get('username')},\n\nYlläpito-oikeutesi Mielenosoitukset.fi-sivustolla on tarkistettava "
                f"Hallinnossa ennen {_deadline(review).replace(tzinfo=timezone.utc).astimezone(HELSINKI):%-d.%-m.%Y}. "
                f"Jos tarkistusta ei ole tehty, oikeutesi päättyvät silloin.\nAikaa on {left} vrk.\n\n"
                "Käsittely: https://hallinto.mielenosoitukset.fi/\n")
        job = EmailJob("Mielenosoitukset.fi: ylläpito-oikeuksien tarkistus", [doc["email"]], body=body).to_dict()
        db.email_queue.update_one({"_id": f"access-review:{_deadline(review):%Y%m%d}:{left}:{doc['_id']}"},
                                  {"$setOnInsert": job}, upsert=True)
        queued += 1
    return {"due": due, "queued": queued, "dry_run": dry_run}
