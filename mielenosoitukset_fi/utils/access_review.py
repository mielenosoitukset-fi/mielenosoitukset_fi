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
# role "admin" alone is not listed: organisation admins carry it too (see User.has_site_wide_admin_reach);
# an admin with site-wide permissions is caught by the permission check below.
SITE_ROLES = {"translator": "Kääntäjä", "city_admin": "Kaupunkiylläpitäjä",
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


# Roles an order of kind role_removal can take away (admin/flask_orders.REMOVABLE_ROLES).
REMOVABLE = ("city_admin", "admin", "global_admin", "superuser", "god")


def removable_roles(db, doc: dict) -> list[str]:
    """Machine role names the account holds now that a role_removal order would take away."""
    from mielenosoitukset_fi.utils.scope_grants import live_grant_filter

    uid = doc["_id"]
    roles = []
    if doc.get("role") in REMOVABLE:
        roles.append(doc["role"])
    if doc.get("global_admin") is True and "global_admin" not in roles:
        roles.append("global_admin")
    if "city_admin" not in roles and db.admin_scope_grants.count_documents(
            {"user_id": {"$in": [uid, str(uid)]}, "scope_type": "city", **live_grant_filter()}, limit=1):
        roles.append("city_admin")
    return roles


EVIDENCE_MONTHS = 6


def _iso(moment) -> Optional[str]:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def evidence(db, doc: dict, now: datetime) -> dict:
    """Use of the account in the last six months, for judging whether the rights are still needed.

    Counts demonstration audit entries the person made themselves (not automatic ones).
    No addresses, no content.
    """
    since = now - timedelta(days=30 * EVIDENCE_MONTHS)
    mine = {"user_id": str(doc["_id"]), "automatic": {"$ne": True}}
    last = db.demo_audit_logs.find_one(mine, {"timestamp": 1}, sort=[("timestamp", -1)])
    recent = list(db.demo_audit_logs.find({**mine, "timestamp": {"$gte": since}}, {"demo_id": 1}))
    demo_ids = sorted({r.get("demo_id") for r in recent if r.get("demo_id")})
    cities = set()
    valid = [ObjectId(d) for d in demo_ids if ObjectId.is_valid(d)]
    if valid:
        for d in db.demonstrations.find({"_id": {"$in": valid}}, {"city": 1}):
            if d.get("city"):
                cities.add(str(d["city"]))
    return {"last_login": _iso(doc.get("last_login")), "last_admin_action": _iso((last or {}).get("timestamp")),
            "admin_actions_6m": len(recent), "demos_handled_6m": len(demo_ids), "cities_handled": sorted(cities)}


def hallinto_list(db, now: Optional[datetime] = None) -> list[dict]:
    """Stamped accounts for Hallinto's review list. No email addresses."""
    now = now or utcnow()
    protected = protected_ids()
    items = []
    for doc in db.users.find({f"{FIELD}.required": True}).sort("username", 1):
        review = doc[FIELD]
        current = holds(db, doc)
        # An account that lost all its reviewable rights (role removal, a Flask removal) is
        # no longer open: Hallinto closes its review case.
        state = ("removed" if not current
                 else "protected" if str(doc["_id"]) in protected and is_open(review, now)
                 else "lapsed" if lapsed_doc(doc, now)
                 else "open" if is_open(review, now) else "confirmed")
        items.append({"id": str(doc["_id"]), "username": doc.get("username"),
                      "name": doc.get("displayname") or "", "holds": current or review.get("holds") or [],
                      "roles": removable_roles(db, doc), "contact": contact_view(review), "evidence": evidence(db, doc, now),
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
        (d.get(FIELD) or {}).get("required") and is_open(d[FIELD], now) and holds(db, d) for d in docs)


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


# --- "Ota yhteyttä": ask the holder whether they still need the rights -------------------------

# What a holder can answer. Each situation maps to the coarse reply (keep, drop, none) that older callers know.
# Same set as Hallinto's account review panel.
SITUATIONS = {
    "needs": ("keep", "tarvitsen oikeuksia ja käytän niitä"),
    "keep_idle": ("keep", "haluan pitää oikeudet, mutta en tarvitse niitä aktiivisesti"),
    "partial": ("keep", "tarvitsen vain osaa oikeuksista (kerro mitä)"),
    "give_up": ("drop", "haluan luopua oikeuksista"),
    "inactive": ("drop", "olen tilapäisesti poissa (kerro, mihin asti)"),
    "no_answer": ("none", "ei vastannut"),
    "unreachable": ("none", "ei ole tavoitettavissa"),
}
REPLIES = ("keep", "drop", "none")


class ContactError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def contact_enabled() -> bool:
    value = _config("ACCESS_REVIEW_CONTACT_ENABLED", False)
    return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "on", "yes"}


def contact_view(review: dict) -> Optional[dict]:
    """The contact made in the current round, or None. Older rounds do not count."""
    c = review.get("contact")
    if not c or c.get("round") != _deadline(review):
        return None
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""
    return {"at": fmt(c.get("at")), "by": c.get("by") or "", "reply": c.get("reply") or "",
            "situation": c.get("situation") or "", "roles": list(c.get("roles") or []), "until": c.get("until") or "",
            "reply_at": fmt(c.get("reply_at")), "note": c.get("note") or "", "via": c.get("via") or "",
            "followup_at": fmt(c.get("followup_at"))}


# Replies to the mail go to the Hallinto mailbox (not tuki@: that feeds the shared cases box); the holder can
# also answer with a signed link. Set ACCESS_REVIEW_REPLY_TO to change the address.
DEFAULT_REPLY_TO = "hallinto@mielenosoitukset.fi"


def reply_to() -> str:
    return _config("ACCESS_REVIEW_REPLY_TO") or DEFAULT_REPLY_TO


REPLY_SALT = "access-review-reply-v1"
REPLY_MAX_AGE = 120 * 24 * 3600


def _reply_serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(_config("SECRET_KEY") or "", salt=REPLY_SALT)


def reply_token(doc: dict) -> str:
    """Signed link token for one account and one round. Needs no sign-in; answers only that round."""
    return _reply_serializer().dumps({"u": str(doc["_id"]), "d": f"{_deadline(doc[FIELD]):%Y%m%d}"})


def reply_doc(db, token, now: Optional[datetime] = None) -> Optional[dict]:
    """The account a link token belongs to, if the token is genuine, current and the review still open."""
    from itsdangerous import BadSignature
    if not isinstance(token, str) or not token or len(token) > 512:
        return None
    try:
        data = _reply_serializer().loads(token, max_age=REPLY_MAX_AGE)
        doc = _open_doc(db, data["u"], now or utcnow())
    except (BadSignature, KeyError, TypeError, ContactError):
        return None
    return doc if data.get("d") == f"{_deadline(doc[FIELD]):%Y%m%d}" else None


def reply_url(doc: dict) -> str:
    base = (_config("ACCESS_REVIEW_REPLY_BASE") or "https://mielenosoitukset.fi").rstrip("/")
    return f"{base}/ylla-vastaus/{reply_token(doc)}"


def contact_message(doc: dict, link: Optional[str] = None, followup: bool = False) -> tuple[str, str]:
    review = doc[FIELD]
    deadline = _deadline(review).replace(tzinfo=timezone.utc).astimezone(HELSINKI)
    holds_text = ", ".join(holds_labels(doc)) or "ylläpito-oikeudet"
    body = (f"Hei {doc.get('username')},\n\n"
            f"Mielenosoitukset.fi:n ylläpito-oikeudet tarkistetaan kuuden kuukauden välein. Tililläsi on: {holds_text}.\n\n"
            + ("Aiemmassa viestissä ei ollut vastauslinkkiä, joten tässä se on. Voit myös vastata suoraan tähän viestiin.\n\n"
               if followup else "")
            + "Tarvitsetko oikeuksia edelleen? Vastaa tästä linkistä, se vie vajaan minuutin eikä vaadi kirjautumista:\n"
            + (link or "[henkilökohtainen vastauslinkki]") + "\n\n"
            "Valittavana on:\n"
            + "".join(f"- {text}\n" for key, (_, text) in SITUATIONS.items() if key not in {"no_answer", "unreachable"})
            + "\nVoit myös vastata suoraan tähän viestiin.\n"
            + f"\nJos oikeuksia ei vahvisteta ennen {deadline:%-d.%-m.%Y}, ne päättyvät silloin. "
            "Mitään ei poisteta, ja oikeudet voi palauttaa myöhemmin.\n\n"
            "Ystävällisin terveisin\nMielenosoitukset.fi:n ylläpito\n")
    return "Mielenosoitukset.fi: tarvitsetko vielä ylläpito-oikeuksiasi?", body


def holds_labels(doc: dict) -> list[str]:
    return list((doc.get(FIELD) or {}).get("holds") or [])


def _open_doc(db, account: str, now: datetime) -> dict:
    if not ObjectId.is_valid(account or ""):
        raise ContactError("invalid_request", 400)
    doc = db.users.find_one({"_id": ObjectId(account)})
    review = (doc or {}).get(FIELD) or {}
    if not doc or not review.get("required"):
        raise ContactError("not_found", 404)
    if not is_open(review, now) or not holds(db, doc):
        raise ContactError("not_open")
    return doc


def contact_preview(db, account: str, now: Optional[datetime] = None) -> dict:
    subject, body = contact_message(_open_doc(db, account, now or utcnow()))
    return {"subject": subject, "body": body}


def contact_send(db, account: str, by: str, now: Optional[datetime] = None) -> dict:
    """Queue one mail to the holder and record it. Off unless ACCESS_REVIEW_CONTACT_ENABLED."""
    from mielenosoitukset_fi.emailer.EmailJob import EmailJob

    if not contact_enabled():
        raise ContactError("disabled")
    now = now or utcnow()
    doc = _open_doc(db, account, now)
    review = doc[FIELD]
    if contact_view(review):
        raise ContactError("already_contacted")
    if not doc.get("email"):
        raise ContactError("no_address", 422)
    subject, body = contact_message(doc, reply_url(doc))
    deadline = _deadline(review)
    # Fixed queue id: a repeat can never mail twice for the same round.
    job = EmailJob(subject, [doc["email"]], body=body, extra_headers={"Reply-To": reply_to()}).to_dict()
    db.email_queue.update_one({"_id": f"access-review-contact:{deadline:%Y%m%d}:{doc['_id']}"},
                              {"$setOnInsert": job}, upsert=True)
    db.users.update_one({"_id": doc["_id"]}, {"$set": {f"{FIELD}.contact": {
        "round": deadline, "at": now, "by": by, "reply": "", "reply_at": None, "note": ""}}})
    return contact_view(db.users.find_one({"_id": doc["_id"]})[FIELD])


def contact_followup(db, account: str, by: str, now: Optional[datetime] = None) -> dict:
    """One more mail with the answer link, for a holder who was mailed before the link existed.

    Only after a contact this round and before any answer; once per round (fixed queue id).
    """
    from mielenosoitukset_fi.emailer.EmailJob import EmailJob

    if not contact_enabled():
        raise ContactError("disabled")
    now = now or utcnow()
    doc = _open_doc(db, account, now)
    review = doc[FIELD]
    contact = contact_view(review)
    if not contact:
        raise ContactError("not_contacted")
    if contact["reply"] or review["contact"].get("followup_at"):
        raise ContactError("already_answered" if contact["reply"] else "already_followed_up")
    if not doc.get("email"):
        raise ContactError("no_address", 422)
    subject, body = contact_message(doc, reply_url(doc), followup=True)
    job = EmailJob(subject, [doc["email"]], body=body, extra_headers={"Reply-To": reply_to()}).to_dict()
    db.email_queue.update_one({"_id": f"access-review-contact-link:{_deadline(review):%Y%m%d}:{doc['_id']}"},
                              {"$setOnInsert": job}, upsert=True)
    db.users.update_one({"_id": doc["_id"]}, {"$set": {f"{FIELD}.contact.followup_at": now}})
    return contact_view(db.users.find_one({"_id": doc["_id"]})[FIELD])


def contact_reply(db, account: str, reply: Optional[str], by: str, note: str = "", now: Optional[datetime] = None,
                  situation: str = "", roles: Optional[list] = None, until: str = "", via: str = "hand") -> dict:
    """Record the holder's answer by hand. Works without a sent mail (they may have been asked in person).

    Give a `situation` (preferred) or just a coarse `reply`; the coarse reply follows from the situation.
    "partial" needs the roles kept, "inactive" the date they are away until (YYYY-MM-DD).
    """
    roles = list(roles or [])
    if situation:
        if situation not in SITUATIONS or (reply and reply != SITUATIONS[situation][0]):
            raise ContactError("invalid_request", 400)
        reply = SITUATIONS[situation][0]
    if reply not in REPLIES or not isinstance(note, str) or len(note) > 500 or not isinstance(until, str):
        raise ContactError("invalid_request", 400)
    if (any(not isinstance(r, str) or not r or len(r) > 80 or any(c in r for c in "|,[]") for r in roles) or len(roles) > 10
            or (situation == "partial") != bool(roles)):
        raise ContactError("invalid_request", 400)
    if situation == "inactive":
        try:
            day = datetime.strptime(until, "%Y-%m-%d")
        except ValueError:
            raise ContactError("invalid_request", 400)
        if not utcnow() - timedelta(days=1) <= day <= utcnow() + timedelta(days=731):
            raise ContactError("invalid_request", 400)
    elif until:
        raise ContactError("invalid_request", 400)
    now = now or utcnow()
    doc = _open_doc(db, account, now)
    review = doc[FIELD]
    old = review.get("contact") if contact_view(review) else None
    contact = {"round": _deadline(review), "at": (old or {}).get("at") or now, "by": (old or {}).get("by") or by,
               "reply": reply, "situation": situation, "roles": roles, "until": until,
               "reply_at": now, "note": note.strip(), "via": via, "followup_at": (old or {}).get("followup_at")}
    db.users.update_one({"_id": doc["_id"]}, {"$set": {f"{FIELD}.contact": contact}})
    return contact_view({"deadline": contact["round"], "contact": contact})
