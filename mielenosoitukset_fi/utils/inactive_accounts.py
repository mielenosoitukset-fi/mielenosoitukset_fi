"""Inactive accounts: warn twice, then delete (#1174, #596).

Maintainer decision (24.11.2026, Olivia): an ordinary account with no login for
24 months is warned, given 30 days to log in, warned again 7 days before the
end, and then deleted like any other account (``hard_delete``: sign-in data and
personal records go, abuse evidence stays pseudonymised).

Timeline, counted from the first warning (``inactivity_notice.first_at``):

* day 0: first warning mail, "log in within 30 days or the account is deleted";
* day 23: final warning mail (7 days left);
* day 30 and the final warning at least a day old: deletion.

A login after the first warning cancels the process. The notice is cleared and
the 24 months start again.

Exempt, never warned and never deleted by this job:

* any account with more than ordinary rights (role other than ``user``,
  ``global_admin``, ``global_permissions``, which includes org_admins);
* an owner or admin of an organization (so a sole owner is never orphaned);
* an account with an open case (a case it submitted that is not closed, or an
  open security case about it);
* an account without an email address (it cannot be warned);
* an account already closed for deletion review (``soft_deleted``).

Does nothing unless ``INACTIVE_ACCOUNT_DELETION_ENABLED`` is set; ``dry_run``
only counts and sends nothing.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

DAY = timedelta(days=1)
INACTIVE_DAYS = 730          # 24 months without a login
GRACE_DAYS = 30              # from the first warning to deletion
FINAL_WARNING_DAYS = 23      # 7 days before the end
NOTICE = "inactivity_notice"
LOGIN_URL = "https://mielenosoitukset.fi/users/auth/login"


def is_enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get("INACTIVE_ACCOUNT_DELETION_ENABLED", "")).strip().lower() in {"1", "true", "yes"}


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def last_activity(doc):
    """Last login, or the time the account was created when it never logged in."""
    value = _aware(doc.get("last_login"))
    if isinstance(value, datetime):
        return value
    _id = doc.get("_id")
    if isinstance(_id, ObjectId):
        return _id.generation_time
    return None


def _privileged(doc):
    return bool(
        doc.get("global_admin")
        or (doc.get("role") or "user") != "user"
        or doc.get("global_permissions")
    )


def exemption(db, doc):
    """Why this account is never deleted for inactivity, or None."""
    if _privileged(doc):
        return "privileged"
    if doc.get("soft_deleted"):
        return "closed_for_review"
    if not (doc.get("email") or "").strip():
        return "no_email"
    ids = [doc["_id"], str(doc["_id"])]
    if db.memberships.find_one({"user_id": {"$in": ids}, "role": {"$in": ["owner", "admin"]}}, {"_id": 1}):
        return "organization_owner"
    if db.cases.find_one({"submitter_id": {"$in": ids}, "meta.closed": {"$ne": True}}, {"_id": 1}):
        return "open_case"
    from mielenosoitukset_fi.utils.security_cases import TERMINAL

    if db.security_cases.find_one(
        {"$or": [{"user_id": {"$in": ids}}, {"target_id": {"$in": ids}}], "status": {"$nin": list(TERMINAL)}},
        {"_id": 1},
    ):
        return "open_security_case"
    return None


def _mail(db, doc, stage, first_at, now):
    from mielenosoitukset_fi.emailer.EmailJob import EmailJob

    deadline = first_at + GRACE_DAYS * DAY
    day = f"{deadline:%-d.%-m.%Y}"
    if stage == "first":
        subject = "Mielenosoitukset.fi: tilisi poistetaan, jos et kirjaudu"
        lead = (f"Et ole kirjautunut Mielenosoitukset.fi-tilillesi yli kahteen vuoteen. "
                f"Poistamme tilin {day}, ellet kirjaudu sisään ennen sitä.")
    else:
        subject = "Mielenosoitukset.fi: tilisi poistetaan viikon kuluttua"
        lead = f"Tilisi poistetaan {day}, ellet kirjaudu sisään ennen sitä. Aikaa on enää 7 päivää."
    body = (f"Hei {doc.get('username') or ''},\n\n{lead}\n\n"
            f"Kirjaudu sisään: {LOGIN_URL}\n\n"
            "Kirjautuminen riittää, muuta ei tarvitse tehdä. Jos et halua pitää tiliä, voit jättää tämän viestin huomiotta. "
            "Tilin poistossa poistamme kirjautumistietosi ja tavalliset henkilötietosi. Väärinkäytösten selvittämiseen "
            "tarvittavat tiedot säilytetään nimettömänä tietosuojaselosteen mukaan: https://mielenosoitukset.fi/privacy\n")
    job = EmailJob(subject, [doc["email"]], body=body).to_dict()
    db.email_queue.update_one(
        {"_id": f"inactive-account:{doc['_id']}:{first_at:%Y%m%d}:{stage}"}, {"$setOnInsert": job}, upsert=True)


def run(db, now=None, dry_run=False, secret=None, seal_key=None):
    """One pass. Returns counts. Safe to run daily and to run again."""
    from mielenosoitukset_fi.utils.account_deletion import hard_delete

    now = _aware(now or utcnow())
    cutoff = now - INACTIVE_DAYS * DAY
    counts = {"cleared": 0, "first_warning": 0, "final_warning": 0, "deleted": 0, "exempt": 0, "failed": 0}

    # A login after the first warning, or a new exemption, cancels the process.
    for doc in db.users.find({f"{NOTICE}.first_at": {"$exists": True}}):
        first_at = _aware(doc[NOTICE]["first_at"])
        active = (last_activity(doc) or first_at) > first_at
        if active or exemption(db, doc):
            counts["cleared"] += 1
            if not dry_run:
                db.users.update_one({"_id": doc["_id"]}, {"$unset": {NOTICE: ""}})

    for doc in db.users.find({"soft_deleted": {"$ne": True}, "role": {"$in": ["user", None]}}):
        seen = last_activity(doc)
        if seen is None or seen >= cutoff:
            continue
        if exemption(db, doc):
            counts["exempt"] += 1
            continue
        notice = doc.get(NOTICE) or {}
        first_at = _aware(notice.get("first_at"))
        if not first_at or first_at < seen:
            counts["first_warning"] += 1
            if not dry_run:
                _mail(db, doc, "first", now, now)
                db.users.update_one({"_id": doc["_id"]}, {"$set": {NOTICE: {"first_at": now}}})
            continue
        final_at = _aware(notice.get("final_at"))
        if not final_at:
            if now >= first_at + FINAL_WARNING_DAYS * DAY:
                counts["final_warning"] += 1
                if not dry_run:
                    _mail(db, doc, "final", first_at, now)
                    db.users.update_one({"_id": doc["_id"]}, {"$set": {f"{NOTICE}.final_at": now}})
            continue
        if now >= first_at + GRACE_DAYS * DAY and now >= final_at + DAY:
            counts["deleted"] += 1
            if dry_run:
                continue
            result = hard_delete(db, doc, secret, seal_key)
            if result["status"] != "ok":
                counts["deleted"] -= 1
                counts["failed"] += 1
    return {"dry_run": dry_run, "counts": counts}
