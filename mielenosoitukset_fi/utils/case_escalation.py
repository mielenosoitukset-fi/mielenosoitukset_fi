"""Escalation levels for new-demonstration cases (Olivia, 21 Nov 2026).

A case that someone cannot or will not decide goes up one level at a time so
it is never left unprocessed:

* level 0: the moderator or city admin the case came to;
* level 1: a second reviewer (national moderator, full admin);
* level 2: Johtoryhmä, the last level. Nothing goes above it.

``meta.superior_needed`` stays the "escalated" flag the rest of the site reads;
``meta.escalation_level`` says how high. Cases escalated before levels existed
have the flag and no level, which counts as level 1.
"""

from bson import ObjectId

from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow

MAX_LEVEL = 2
LEVEL_NAMES = {0: "Käsittelijä", 1: "Toinen tarkistaja", 2: "Johtoryhmä"}
# The button names who the case goes to next.
ASK_LABELS = {0: "Pyydä toista tarkistajaa", 1: "Pyydä Johtoryhmää"}
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
    return 1 if meta.get("superior_needed") else 0


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


def escalate(db, case_doc, user, reason, *, authentication, second_factor="none"):
    """Raise the case one level and notify that level. Returns the new level.

    The update only applies if the level is still the one the caller saw, so
    two people pressing at once raise it once.
    """
    meta = case_doc.get("meta") or {}
    level = current_level(meta)
    if level >= MAX_LEVEL:
        raise EscalationError("top_level")
    if not can_escalate(meta, user):
        raise EscalationError("not_allowed")
    new_level = level + 1
    now = utcnow()
    if level == 0:
        guard = {"$or": [{"meta.superior_needed": {"$ne": True}}, {"meta.escalation_level": 0}]}
    else:
        guard = {"meta.superior_needed": True,
                 "$or": [{"meta.escalation_level": 1}, {"meta.escalation_level": {"$exists": False}}]}
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
        notify_level(db, case_doc, new_level, reason=reason, from_user=user.username)
    except Exception:
        logger.exception("Could not notify level %s about case %s", new_level, case_doc.get("_id"))
    return new_level


def _level_users(db, level):
    """Accounts that answer at this level: full admins, or Johtoryhmä at the top."""
    from mielenosoitukset_fi.utils.case_notifications import _candidate_users
    from mielenosoitukset_fi.utils.system_teams import SUPER_ROLES, board_ids

    users = _candidate_users(db)
    if level >= MAX_LEVEL:
        board = board_ids(db)
        return [doc for doc in users if str(doc["_id"]) in board]
    return [doc for doc in users if doc.get("global_admin") or doc.get("role") in SUPER_ROLES]


def notify_level(db, case_doc, level, *, reason="", from_user=""):
    """Inbox notice and one e-mail for each person at the new level who may see the case.

    Mail follows the same switch as new-case mail and is sent only when the
    notice is first created, so a retry never mails twice.
    """
    from mielenosoitukset_fi.utils.case_notifications import (
        NOTICE_TYPE, SITE_URL, _email_sender, _payload, case_recipients, emails_enabled,
    )

    payload = _payload(db, case_doc)
    payload["escalation_level"] = level
    payload["escalation_name"] = LEVEL_NAMES[level]
    sent = 0
    for doc, _user in case_recipients(db, case_doc["_id"], users=_level_users(db, level)):
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
