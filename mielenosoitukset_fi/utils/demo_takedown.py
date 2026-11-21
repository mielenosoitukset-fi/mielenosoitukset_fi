"""Who may take down (or republish) an already approved demonstration.

Maintainer decision (20 Nov 2026): taking down a public demonstration is a
governance matter. Every rights check goes through ``can_take_down_demo`` so
the Hallinto platform can supply the rule later without touching callers.
Interim rule: global admins with approval rights.
"""

from datetime import timedelta

from bson import ObjectId
from pymongo import ReturnDocument

from mielenosoitukset_fi.utils.time_utils import utcnow


def can_take_down_demo(user, demo=None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "global_admin", False):
        return False
    has_permission = getattr(user, "has_permission", None)
    return bool(has_permission and has_permission("ACCEPT_DEMO"))


# ---------------------------------------------------------------------------
# Second admin check
# ---------------------------------------------------------------------------
# Maintainer plan (20 Nov 2026): a normal take-down waits until a second admin
# confirms it, and the demo stays public meanwhile. An urgent take-down hides
# the demo at once and a second admin confirms it afterwards. Both the rule and
# the confirmation time are configurable. The request lives on the demo as
# ``take_down_review`` so that every page can show it.


REVIEW_FIELD = "take_down_review"
REQUESTED = "requested"  # normal: demo still public, waiting for a 2nd admin
AWAITING = "awaiting_confirmation"  # urgent: demo hidden, 2nd admin confirms after
OPEN_STATES = (REQUESTED, AWAITING)
DEFAULT_CONFIRM_HOURS = 48


class TakeDownReviewError(ValueError):
    """A take-down request cannot move to the asked state."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _setting(name, default):
    from flask import current_app, has_app_context

    value = current_app.config.get(name) if has_app_context() else None
    if value is None:
        from config import Config

        value = getattr(Config, name, default)
    return default if value is None else value


def second_admin_required() -> bool:
    value = _setting("TAKE_DOWN_SECOND_ADMIN", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def confirm_hours() -> int:
    try:
        hours = int(_setting("TAKE_DOWN_CONFIRM_HOURS", DEFAULT_CONFIRM_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_CONFIRM_HOURS
    return hours if hours > 0 else DEFAULT_CONFIRM_HOURS


def _user_ref(user):
    user_id = getattr(user, "id", None) or getattr(user, "_id", None)
    return str(user_id) if user_id else None, getattr(user, "username", None) or "?"


def _oid(demo_id):
    if not ObjectId.is_valid(str(demo_id)):
        raise TakeDownReviewError("not_found", "Demonstration not found")
    return ObjectId(str(demo_id))


def _note(db, demo_oid, action, note, actor, details):
    from mielenosoitukset_fi.demonstrations.audit import log_demo_audit_entry

    now = utcnow()
    db.cases.update_many(
        {"demo_id": {"$in": [demo_oid, str(demo_oid)]}},
        {
            "$push": {
                "action_logs": {
                    "timestamp": now, "admin": actor.get("username"), "actor": actor,
                    "action_type": action, "note": note,
                },
                "case_history": {
                    "timestamp": now, "action": note, "user": actor.get("username"),
                    "metadata": details,
                },
            },
            "$set": {"updated_at": now},
        },
    )
    log_demo_audit_entry(demo_oid, action=action, message=note, details=details, actor=actor)


def request_take_down(db, demo_id, user, reason, *, urgent, apply, actor):
    """Start a take-down. Returns "taken_down" or "requested".

    ``apply(reason)`` runs the decision service (it emails the submitter and
    hides the demo). Without the second-admin rule it is called right away.
    """
    demo_oid = _oid(demo_id)
    reason = (reason or "").strip()
    if not reason:
        raise TakeDownReviewError("reason", "A take-down needs a reason")
    if not second_admin_required():
        apply(reason)
        return "taken_down"

    user_id, username = _user_ref(user)
    now = utcnow()
    review = {
        "status": AWAITING if urgent else REQUESTED,
        "urgent": bool(urgent),
        "reason": reason[:1000],
        "requested_by": user_id,
        "requested_by_name": username,
        "requested_at": now,
    }
    if urgent:
        review["confirm_by"] = now + timedelta(hours=confirm_hours())
    current = db.demonstrations.find_one({"_id": demo_oid}, {"approved": 1, REVIEW_FIELD: 1})
    if not current:
        raise TakeDownReviewError("not_found", "Demonstration not found")
    if (current.get(REVIEW_FIELD) or {}).get("status") in OPEN_STATES:
        raise TakeDownReviewError("open", "A take-down request is already open")
    if not current.get("approved"):
        raise TakeDownReviewError("not_public", "Only an approved demonstration can be taken down")

    claimed = db.demonstrations.find_one_and_update(
        {"_id": demo_oid, "approved": True, f"{REVIEW_FIELD}.status": {"$nin": list(OPEN_STATES)}},
        {"$set": {REVIEW_FIELD: review}},
    )
    if claimed is None:
        raise TakeDownReviewError("open", "A take-down request is already open")
    if urgent:
        try:
            apply(review["reason"])
        except Exception:
            db.demonstrations.update_one({"_id": demo_oid}, {"$unset": {REVIEW_FIELD: ""}})
            raise
        note = f"Kiireellinen poisto julkaisusta. Toisen ylläpitäjän vahvistus tarvitaan. Syy: {review['reason']}"
    else:
        note = f"Poistopyyntö: odottaa toisen ylläpitäjän vahvistusta. Syy: {review['reason']}"
    _note(db, demo_oid, "take_down_requested", note, actor,
          {"urgent": bool(urgent), "reason": review["reason"]})
    return "taken_down" if urgent else "requested"


def confirm_take_down(db, demo_id, user, *, apply, actor):
    """Second admin confirms. A normal request is applied now."""
    demo_oid = _oid(demo_id)
    user_id, username = _user_ref(user)
    now = utcnow()
    before = db.demonstrations.find_one_and_update(
        {
            "_id": demo_oid,
            f"{REVIEW_FIELD}.status": {"$in": list(OPEN_STATES)},
            f"{REVIEW_FIELD}.requested_by": {"$ne": user_id},
        },
        {"$set": {
            f"{REVIEW_FIELD}.status": "confirmed",
            f"{REVIEW_FIELD}.confirmed_by": user_id,
            f"{REVIEW_FIELD}.confirmed_by_name": username,
            f"{REVIEW_FIELD}.confirmed_at": now,
        }},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        current = db.demonstrations.find_one({"_id": demo_oid}, {REVIEW_FIELD: 1})
        review = (current or {}).get(REVIEW_FIELD) or {}
        if review.get("status") in OPEN_STATES and review.get("requested_by") == user_id:
            raise TakeDownReviewError("same_admin", "The second admin must be someone else")
        raise TakeDownReviewError("no_request", "No take-down request is waiting")
    review = before[REVIEW_FIELD]
    if review["status"] == REQUESTED:
        try:
            apply(review["reason"])
        except Exception:
            db.demonstrations.update_one(
                {"_id": demo_oid},
                {"$set": {f"{REVIEW_FIELD}.status": REQUESTED},
                 "$unset": {f"{REVIEW_FIELD}.confirmed_by": "", f"{REVIEW_FIELD}.confirmed_by_name": "",
                            f"{REVIEW_FIELD}.confirmed_at": ""}},
            )
            raise
    _note(db, demo_oid, "take_down_confirmed",
          f"Toinen ylläpitäjä ({username}) vahvisti poiston julkaisusta.", actor,
          {"requested_by": review.get("requested_by_name"), "urgent": review.get("urgent")})
    return "taken_down"


def cancel_take_down_request(db, demo_id, user, *, actor):
    """Withdraw (requester) or decline (another admin) a normal request.

    The demo was never hidden, so nothing else changes. An urgent take-down is
    reversed with "Julkaise uudelleen" (approve) instead.
    """
    demo_oid = _oid(demo_id)
    user_id, username = _user_ref(user)
    before = db.demonstrations.find_one_and_update(
        {"_id": demo_oid, f"{REVIEW_FIELD}.status": REQUESTED},
        {"$unset": {REVIEW_FIELD: ""}},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        raise TakeDownReviewError("no_request", "No take-down request is waiting")
    review = before[REVIEW_FIELD]
    own = review.get("requested_by") == user_id
    note = (f"{username} perui poistopyyntönsä." if own
            else f"{username} hylkäsi käyttäjän {review.get('requested_by_name')} poistopyynnön. Mielenosoitus pysyy julkisena.")
    _note(db, demo_oid, "take_down_request_withdrawn" if own else "take_down_request_declined", note, actor,
          {"requested_by": review.get("requested_by_name"), "reason": review.get("reason")})
    return "public"


def review_view(demo, user):
    """What the case page shows about an open take-down request."""
    review = (demo or {}).get(REVIEW_FIELD) or {}
    if review.get("status") not in OPEN_STATES:
        return None
    user_id, _name = _user_ref(user)
    confirm_by = review.get("confirm_by")
    return {
        **review,
        "own": review.get("requested_by") == user_id,
        "overdue": bool(confirm_by and confirm_by < utcnow()),
    }
