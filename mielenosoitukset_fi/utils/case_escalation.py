"""Escalation levels for new-demonstration cases (Olivia, 21 Nov 2026).

A case that someone cannot or will not decide goes up one level at a time so
it is never left unprocessed:

* level 0: the first reviewer (the city admin when the city has one, else the
  admin the case came to);
* level 1: the moderator on duty right now (a shift from /admin/vuorot is
  running), skipped when nobody is on duty;
* level 2: the national moderation team (national admin team and national
  moderators);
* level 3: OT (the approvers of the duty-shift appointments, whom Hallinto
  sends), skipped when none are known;
* level 4: Johtoryhmä, the last level. Nothing goes above it.

``meta.superior_needed`` stays the "escalated" flag the rest of the site reads;
``meta.escalation_level`` says how high. Cases flagged before levels existed
have the flag and no level, which counts as level 2 (the national team, as
before). Decision rights are unchanged: full admins decide escalated cases.
"""

from datetime import datetime, timezone

from bson import ObjectId

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

MAX_LEVEL = 4
LEVEL_NAMES = {
    0: "Ensimmäinen tarkistaja",
    1: "Päivystävä moderaattori",
    2: "Kansallinen moderaattoritiimi",
    3: "OT",
    4: "Johtoryhmä",
}
MIN_REASON, MAX_REASON = 5, 500


class EscalationError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def current_level(meta) -> int:
    meta = meta or {}
    level = meta.get("escalation_level")
    if isinstance(level, int) and 0 <= level <= MAX_LEVEL:
        return level
    return 2 if meta.get("superior_needed") else 0


def can_escalate(meta, user) -> bool:
    """Who may push the case up: level 0 any handler, level 1 only the second reviewer."""
    level = current_level(meta)
    if level >= MAX_LEVEL:
        return False
    return level == 0 or bool(getattr(user, "global_admin", False))


def clean_reason(value) -> str:
    reason = " ".join((value or "").split())
    if len(reason) < MIN_REASON:
        raise EscalationError("reason_short")
    if len(reason) > MAX_REASON:
        raise EscalationError("reason_long")
    return reason


def next_target(db, level):
    """(level, users) the case goes to next: the first higher level that has someone.

    Every level with nobody to tell is skipped (no one on duty, no OT member
    known, an empty team), so the chain stays short. Only the last level is
    never skipped, even when no account is found: the case must still move up.
    """
    for candidate in range(level + 1, MAX_LEVEL + 1):
        users = _level_users(db, candidate)
        if users or candidate == MAX_LEVEL:
            return candidate, users
    return MAX_LEVEL, []


def ask_label(db, meta):
    """Button text naming who the case goes to next, or None at the top."""
    level = current_level(meta)
    if level >= MAX_LEVEL:
        return None
    return f"Siirrä eteenpäin: {LEVEL_NAMES[next_target(db, level)[0]]}"


def escalate(db, case_doc, user, reason, *, authentication, second_factor="none"):
    """Raise the case to the next level that has someone, and notify them.

    Returns the new level. The update only applies if the level is still the
    one the caller saw, so two people pressing at once raise it once.
    """
    meta = case_doc.get("meta") or {}
    level = current_level(meta)
    if level >= MAX_LEVEL:
        raise EscalationError("top_level")
    if not can_escalate(meta, user):
        raise EscalationError("not_allowed")
    new_level, recipients = next_target(db, level)
    now = utcnow()
    if level == 0:
        guard = {"$or": [{"meta.superior_needed": {"$ne": True}}, {"meta.escalation_level": 0}]}
    elif level == 2:
        guard = {"meta.superior_needed": True,
                 "$or": [{"meta.escalation_level": 2}, {"meta.escalation_level": {"$exists": False}}]}
    else:
        guard = {"meta.superior_needed": True, "meta.escalation_level": level}
    updated = db.cases.update_one(
        {"_id": case_doc["_id"], **guard},
        {
            "$set": {"meta.superior_needed": True, "meta.escalation_level": new_level, "updated_at": now},
            "$push": {
                "case_history": {
                    "timestamp": now,
                    "action": f"Eskaloitu: {LEVEL_NAMES[new_level]}",
                    "user": user.username,
                    "mech_action": "escalate_demo",
                    "metadata": {"reason": reason, "level": new_level, "authentication": authentication,
                                 "second_factor": second_factor},
                    "meta_schema": {"reason": "string", "level": "int", "authentication": "string",
                                    "second_factor": "string"},
                },
                "action_logs": {
                    "user": user.username, "action_type": "escalate_demo", "reason": reason,
                    "level": new_level, "source": authentication, "timestamp": now,
                },
            },
        },
    )
    if not updated.modified_count:
        raise EscalationError("changed")
    try:
        notify_level(db, case_doc, new_level, recipients, reason=reason, from_user=user.username)
    except Exception:
        logger.exception("Could not notify level %s about case %s", new_level, case_doc.get("_id"))
    return new_level


def _ids(values):
    return {ObjectId(str(v)) for v in values if ObjectId.is_valid(str(v))}


def _level_users(db, level):
    """Active accounts that answer at a level (empty when nobody does)."""
    from mielenosoitukset_fi.utils import duty_shifts
    from mielenosoitukset_fi.utils.system_teams import active_account, approved_members, board_ids

    now = datetime.now(timezone.utc)
    ids = set()
    if level == 1:
        current = duty_shifts._naive(now)
        for shift in db[duty_shifts.SHIFTS].find({
            "starts_at": {"$lte": current}, "ends_at": {"$gt": current - duty_shifts.END_GRACE},
            "approved_at": {"$ne": None}, "$or": [{"cancelled_at": {"$exists": False}}, {"cancelled_at": None}],
        }, {"user_id": 1}):
            try:
                if duty_shifts.active_duty(db, shift["user_id"], now):
                    ids.add(shift["user_id"])
            except Exception:
                logger.exception("Duty check failed for shift %s", shift.get("_id"))
    elif level == 2:
        ids |= _ids(doc["user_id"] for doc in db[duty_shifts.GRANTS].find({"revoked_at": None}, {"user_id": 1}))
        ids |= _ids(doc["_id"] for doc in approved_members(db, "national"))
        ids |= _ids(doc["_id"] for doc in db.users.find({"global_admin": True}, {"_id": 1}))
    elif level == 3:
        for grant in db[duty_shifts.GRANTS].find({"revoked_at": None}, {"approvers": 1}):
            ids |= _ids(grant.get("approvers") or ())
    elif level == 4:
        ids |= _ids(board_ids(db))
    if not ids:
        return []
    return [doc for doc in db.users.find({"_id": {"$in": list(ids)}}) if active_account(doc)]


def notify_level(db, case_doc, level, users, *, reason="", from_user=""):
    """Inbox notice and one e-mail for each person at the new level.

    Mail follows the same switch as new-case mail and is sent only when the
    notice is first created, so a retry never mails twice.
    """
    from mielenosoitukset_fi.utils.case_notifications import (
        NOTICE_TYPE, SITE_URL, _email_sender, _payload, emails_enabled,
    )

    payload = _payload(db, case_doc)
    payload["escalation_level"] = level
    payload["escalation_name"] = LEVEL_NAMES[level]
    sent = 0
    # Everyone at the level is told, also those the case list would not show:
    # the inbox hides a notice the reader may not open, the mail still reaches them.
    for doc in users:
        result = db.notifications.update_one(
            {"_id": f"escalation:{case_doc['_id']}:{level}:{doc['_id']}"},
            {"$setOnInsert": {
                "user_id": doc["_id"], "type": NOTICE_TYPE, "payload": payload,
                "link": f"/admin/case/{case_doc['_id']}/", "created_at": utcnow(), "read": False,
            }},
            upsert=True,
        )
        email = (doc.get("email") or "").strip()
        if result.upserted_id is not None and email and emails_enabled():
            try:
                _email_sender().queue_email(
                    template_name="admin_case_escalated.html",
                    subject=f"Tapaus #{payload.get('running_num') or ''} eskaloitu sinulle (taso {level})",
                    recipients=[email],
                    context={
                        "running_num": payload.get("running_num") or "", "level": level,
                        "level_name": LEVEL_NAMES[level], "demo_title": payload.get("demo_title") or "",
                        "reason": reason, "from_user": from_user or "Moderaattori",
                        "case_link": f"{SITE_URL}/admin/case/{case_doc['_id']}/",
                    },
                )
            except Exception:
                logger.exception("Could not queue escalation mail for case %s", case_doc.get("_id"))
        sent += 1
    return sent
