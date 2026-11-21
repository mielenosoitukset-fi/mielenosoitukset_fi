"""Canonical, idempotent approval decisions for demonstrations."""

from dataclasses import dataclass
from datetime import timedelta, timezone
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from mielenosoitukset_fi.demonstrations.audit import (
    log_demo_audit_entry,
    record_demo_change,
)
from mielenosoitukset_fi.utils.city_assignment import (
    CITY_ASSIGNMENT_FIELD,
    clear_city_assignment,
)
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.time_utils import utcnow


DECISION_STATES = {
    "approved": {"approved": True, "rejected": False, "taken_down": False},
    "rejected": {"approved": False, "rejected": True, "taken_down": False},
    # Maintainer decision (20 Nov 2026): an approved demo can be taken down
    # without deleting it. It leaves every public list (approved is False) and
    # pending queues (rejected is True); the public URL shows a short notice.
    "taken_down": {"approved": False, "rejected": True, "taken_down": True},
    # Maintainer decision (20 Nov 2026): a demo nobody decided before its date
    # is closed as expired by the nightly job. It is not published and leaves
    # the pending queues; the submitter gets an apology (recent ones only).
    "expired": {"approved": False, "rejected": True, "taken_down": False},
}
DECISION_LABELS = {
    "approved": "Hyväksytty",
    "rejected": "Hylätty",
    "taken_down": "Poistettu julkaisusta",
    "expired": "Vanhentunut",
}
DECISION_ACTIONS = {
    "approved": "approve_demo",
    "rejected": "reject_demo",
    "taken_down": "take_down_demo",
    "expired": "expire_demo",
}
DECISION_REASONS = {
    "approved": "demo_approved",
    "rejected": "demo_rejected",
    "taken_down": "demo_taken_down",
    "expired": "demo_expired",
}
DECISION_MESSAGES = {
    "approved": "Mielenosoitus hyväksyttiin",
    "rejected": "Mielenosoitus hylättiin",
    "taken_down": "Mielenosoitus poistettiin julkaisusta",
    "expired": "Mielenosoitusta ei käsitelty ennen sen päivää, joten tapaus suljettiin",
}
SUBMITTER_SUBJECTS = {
    "approved": "Mielenosoituksesi on hyväksytty",
    "rejected": "Mielenosoituksesi on hylätty",
    "taken_down": "Mielenosoituksesi on poistettu sivustolta",
    "expired": "Pahoittelumme: ilmoitustasi ei ehditty käsitellä",
}
TAKE_DOWN_REASON_MAX = 1000
DEFAULT_UNDO_APPROVAL_MINUTES = 30
UNDO_APPROVAL_STATE = {"approved": False, "rejected": False, "taken_down": False}


class PublishingBlocked(ValueError):
    """A precedent from Johtoryhmä says this demonstration is not published."""

    def __init__(self, reason: str):
        super().__init__("Publishing is blocked by a precedent")
        self.reason = reason


class DemoNotPublished(ValueError):
    """Only an approved (public) demonstration can be taken down."""

# A terminal moderation decision closes every bearer-link family. The token
# used for the decision is excluded and consumed by its route after success.
TERMINAL_TOKEN_ACTIONS = ("preview", "approve", "reject", "edit", "case")


@dataclass(frozen=True)
class DemoDecisionResult:
    demo: dict
    decision_id: str
    changed: bool
    cases_updated: int
    tokens_revoked: int
    notification_queued: bool


def _object_id(value) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if not ObjectId.is_valid(str(value)):
        raise ValueError("Invalid demonstration id")
    return ObjectId(str(value))


def _actor_label(actor: dict) -> str:
    return (
        actor.get("username")
        or actor.get("email")
        or actor.get("user_id")
        or "system"
    )


def _close_related_cases(db, demo_id, demo, decision, decision_meta) -> int:
    """Project the authoritative decision without clobbering a newer writer.

    The case snapshot is read before the demo authority check, and its observed
    decision id is the write precondition. A lost compare-and-set must re-read
    both records. No wall-clock ordering or multi-document transaction is assumed.
    """
    action = DECISION_ACTIONS[decision]
    label = DECISION_LABELS[decision]
    reason = DECISION_REASONS[decision]
    decision_id = decision_meta["id"]
    actor_name = _actor_label(decision_meta.get("actor") or {})
    timestamp = decision_meta["at"]
    updated = 0

    case_update = {
        "$set": {
            "status": label,
            "outcome": decision,
            "resolution": decision,
            "updated_at": timestamp,
            "meta.closed": True,
            "meta.closed_at": timestamp,
            "meta.closed_reason": reason,
            "meta.outcome": decision,
            "meta.resolution": decision,
            "meta.decision_id": decision_id,
        },
        "$push": {
            "action_logs": {
                "timestamp": timestamp,
                "admin": actor_name,
                "actor": decision_meta.get("actor"),
                "action_type": action,
                "note": (
                    f"Mielenosoitus {demo.get('title') or 'tuntematon'} "
                    f"({demo_id}) sai päätöksen: {label.lower()}."
                    + (f" Syy: {decision_meta['reason']}" if decision_meta.get("reason") else "")
                ),
            },
            "case_history": {
                "timestamp": timestamp,
                "action": label,
                "user": actor_name,
                "metadata": {
                    "decision": decision,
                    "decision_id": decision_id,
                    "source": decision_meta.get("source"),
                    "actor": decision_meta.get("actor"),
                },
            },
        },
    }
    cases = db.cases.find({"demo_id": {"$in": [demo_id, str(demo_id)]}})
    for case_doc in cases:
        # Read the case before checking the authoritative demo decision. A
        # concurrent writer changing the case after this snapshot makes the
        # compare-and-set fail; never overwrite a newer decision blindly.
        for _ in range(8):
            previous_id = (case_doc.get("meta") or {}).get("decision_id")
            if previous_id == decision_id:
                break
            current = db.demonstrations.find_one(
                {"_id": demo_id}, {"moderation_decision.id": 1}
            )
            if not current or (
                current.get("moderation_decision") or {}
            ).get("id") != decision_id:
                break
            result = db.cases.update_one(
                {"_id": case_doc["_id"], "meta.decision_id": previous_id},
                case_update,
            )
            if result.matched_count:
                updated += result.modified_count
                break
            case_doc = db.cases.find_one({"_id": case_doc["_id"]})
            if not case_doc:
                break
        else:
            # Keep contention bounded. A retry reuses the existing decision id
            # and reconciles effects without duplicating history/notifications.
            raise RuntimeError("Case changed repeatedly during decision; retry")
    return updated


def _revoke_related_tokens(db, demo_id, decision_meta, used_token_id=None) -> int:
    token_filter = {
        "demo_id": str(demo_id),
        "action": {"$in": list(TERMINAL_TOKEN_ACTIONS)},
        "revoked": {"$ne": True},
        "used_at": None,
        "$or": [
            {"created_at": {"$lte": decision_meta["at"]}},
            {"created_at": {"$exists": False}},
        ],
    }
    if used_token_id:
        token_filter["_id"] = {"$ne": _object_id(used_token_id)}

    tokens = list(db.magic_links.find(token_filter, {"_id": 1, "action": 1}))
    if not tokens:
        return 0

    result = db.magic_links.update_many(
        {"_id": {"$in": [token["_id"] for token in tokens]}},
        {
            "$set": {
                "revoked": True,
                "revoked_at": decision_meta["at"],
                "revoked_by": _actor_label(decision_meta.get("actor") or {}),
                "revoked_reason": f"demo_{decision_meta['status']}",
                "decision_id": decision_meta["id"],
            }
        },
    )
    log_demo_audit_entry(
        demo_id,
        action="decision_tokens_revoked",
        message="Päätöksen kanssa ristiriitaiset linkit mitätöitiin",
        details={
            "decision": decision_meta["status"],
            "decision_id": decision_meta["id"],
            "token_ids": [str(token["_id"]) for token in tokens],
            "token_types": sorted({token.get("action") for token in tokens}),
        },
        actor=decision_meta.get("actor"),
    )
    return result.modified_count


def _queue_submitter_notification(
    db,
    demo,
    decision,
    decision_meta,
    email_sender,
    public_url,
) -> bool:
    submitter = db.submitters.find_one({"demonstration_id": demo["_id"]})
    recipient = (submitter or {}).get("submitter_email")
    if not recipient:
        return False
    if decision == "approved" and _approval_email_still_current(db, demo["_id"]):
        # An undone approval sends no email, so the submitter's last message
        # still says "approved". Re-approving must not send it twice.
        return False

    notification_id = f"demo-decision:{decision_meta['id']}"
    try:
        db.demo_decision_notifications.insert_one(
            {
                "_id": notification_id,
                "demo_id": str(demo["_id"]),
                "decision": decision,
                "decision_id": decision_meta["id"],
                "recipient": recipient,
                "created_at": decision_meta["at"],
            }
        )
    except DuplicateKeyError:
        return False

    context = {
        "title": demo.get("title", ""),
        "date": demo.get("date", ""),
        "city": demo.get("city", ""),
        "address": demo.get("address", ""),
    }
    if decision == "approved":
        context["url"] = public_url
    if decision == "taken_down":
        context["reason"] = decision_meta.get("reason") or ""
    from mielenosoitukset_fi.utils.moderation_reviews import notices_enabled

    if decision in ("rejected", "taken_down") and notices_enabled():
        from mielenosoitukset_fi.utils.moderation_reviews import HELSINKI, MAX_WORKING_DAYS, add_working_days

        review_by = add_working_days(decision_meta["at"], MAX_WORKING_DAYS)
        local = review_by.replace(tzinfo=timezone.utc).astimezone(HELSINKI)
        context["review_by"] = f"{local.day}.{local.month}.{local.year}"

    try:
        email_sender.queue_email(
            template_name=f"demo_submitter_{decision}.html",
            subject=SUBMITTER_SUBJECTS[decision],
            recipients=[recipient],
            context=context,
        )
    except Exception:
        db.demo_decision_notifications.delete_one({"_id": notification_id})
        raise

    db.demo_decision_notifications.update_one(
        {"_id": notification_id}, {"$set": {"queued_at": utcnow()}}
    )
    return True


def _approval_email_still_current(db, demo_id) -> bool:
    latest = db.demo_decision_notifications.find_one(
        {"demo_id": str(demo_id), "queued_at": {"$exists": True}},
        sort=[("created_at", -1)],
    )
    return bool(latest) and latest.get("decision") == "approved"


def apply_demo_decision(
    db,
    demo_id,
    decision: str,
    *,
    actor: dict,
    source: str,
    email_sender,
    public_url: str | None = None,
    used_token_id=None,
    reason: str | None = None,
    notify_submitter: bool = True,
) -> DemoDecisionResult:
    """Apply one terminal moderation decision and reconcile its side effects.

    Repeating the same decision reuses its stable decision id, so notification,
    case-history and audit side effects are not duplicated.

    Opposite decisions retain the existing last-transition-wins behavior. A
    changed result/history uses this transition's own snapshot, which may have
    been superseded by the time the caller receives it. Case projection checks
    the currently persisted decision and conditionally retries conflicting writes.
    """
    if decision not in DECISION_STATES:
        raise ValueError("Unsupported demonstration decision")

    demo_oid = _object_id(demo_id)
    if decision == "approved":
        from mielenosoitukset_fi.utils.moderation_blocks import precedent_for

        blocking = precedent_for(db, db.demonstrations.find_one({"_id": demo_oid}, {"organizers": 1}) or {"_id": demo_oid})
        if blocking:
            raise PublishingBlocked(blocking.get("reason") or "")
    target = DECISION_STATES[decision]
    now = utcnow()
    decision_meta = {
        "id": uuid4().hex,
        "status": decision,
        "at": now,
        "actor": actor,
        "source": source,
    }
    transition_filter = {
        "_id": demo_oid,
        "$or": [
            {"approved": {"$ne": target["approved"]}},
            {"rejected": {"$ne": target["rejected"]}},
            {"moderation_decision.status": {"$ne": decision}},
        ],
    }
    extra_fields = {}
    if decision == "expired":
        # Only an undecided demo expires; a decided one keeps its decision.
        transition_filter["approved"] = {"$ne": True}
        transition_filter["rejected"] = {"$ne": True}
        extra_fields = {"expired_at": now}
    if decision == "taken_down":
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A take-down needs a reason")
        reason = reason[:TAKE_DOWN_REASON_MAX]
        decision_meta["reason"] = reason
        extra_fields = {
            "taken_down_reason": reason,
            "taken_down_at": now,
            "taken_down_by": _actor_label(actor or {}),
        }
        # Only a public demo can be taken down; anything else is a rejection.
        transition_filter["approved"] = True
        existing = db.demonstrations.find_one(
            {"_id": demo_oid}, {"approved": 1, "moderation_decision.status": 1}
        )
        if not existing:
            raise LookupError("Demonstration not found")
        already = (existing.get("moderation_decision") or {}).get("status") == "taken_down"
        if not existing.get("approved") and not already:
            raise DemoNotPublished("Only an approved demonstration can be taken down")
    update = {
        "$set": {
            **target,
            **extra_fields,
            "last_modified": now,
            "moderation_decision": decision_meta,
        }
    }
    if decision == "approved":
        # Republishing ends any take-down request or pending confirmation.
        update["$unset"] = {"take_down_review": ""}
    before = db.demonstrations.find_one_and_update(
        transition_filter,
        update,
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        current = db.demonstrations.find_one({"_id": demo_oid})
        if not current:
            raise LookupError("Demonstration not found")
        decision_meta = current.get("moderation_decision") or decision_meta
        # A different decision can commit between the conditional transition
        # and this read. Reconcile the snapshot we actually read, not a mixture
        # of the requested state and another decision's identity.
        decision = decision_meta["status"]
        status_changed = False
    else:
        status_changed = (
            before.get("approved") != target["approved"]
            or before.get("rejected") != target["rejected"]
            or bool(before.get("taken_down")) != target["taken_down"]
        )
        decision_meta["notify_submitter"] = status_changed and notify_submitter
        db.demonstrations.update_one(
            {
                "_id": demo_oid,
                "moderation_decision.id": decision_meta["id"],
            },
            {"$set": {"moderation_decision.notify_submitter": decision_meta["notify_submitter"]}},
        )
        # The atomic transition's before-image defines this decision's own
        # after-image. A fresh read could already belong to an opposite decision
        # and corrupt the returned result and the immutable audit/history entry.
        current = {
            **before,
            **target,
            **extra_fields,
            "last_modified": now,
            "moderation_decision": decision_meta,
        }

    decision_id = decision_meta["id"]

    # A resolved demo no longer belongs to the city admins' queue.
    try:
        clear_city_assignment(db, demo_oid)
    except Exception:
        logger.exception("Failed to clear city assignment for demo %s", demo_oid)

    cases_updated = _close_related_cases(
        db, demo_oid, current, decision, decision_meta
    )
    tokens_revoked = _revoke_related_tokens(
        db, demo_oid, decision_meta, used_token_id=used_token_id
    )

    if status_changed:
        _sync_moderation_reviews(db, current, decision_meta)

    notification_queued = False
    if status_changed:
        record_demo_change(
            demo_oid,
            before,
            current,
            action=DECISION_ACTIONS[decision],
            message=DECISION_MESSAGES[decision],
            extra_details={
                "decision": decision,
                "decision_id": decision_id,
                "source": source,
                **({"reason": decision_meta["reason"]} if decision_meta.get("reason") else {}),
            },
            actor=actor,
        )
    if decision_meta.get("notify_submitter"):
        notification_queued = _queue_submitter_notification(
            db,
            current,
            decision,
            decision_meta,
            email_sender,
            public_url,
        )

    return DemoDecisionResult(
        demo=current,
        decision_id=decision_id,
        changed=status_changed,
        cases_updated=cases_updated,
        tokens_revoked=tokens_revoked,
        notification_queued=notification_queued,
    )


def _sync_moderation_reviews(db, demo, decision_meta) -> None:
    """A rejection or take-down waits for OT review; a newer decision ends the
    earlier open review (moderation guide 18 Nov 2026, roadmap R03)."""
    from mielenosoitukset_fi.utils import moderation_reviews

    try:
        moderation_reviews.supersede_reviews(db, demo["_id"], decision_meta)
        moderation_reviews.open_review(db, demo, decision_meta)
    except Exception:
        logger.exception("Could not update the OT review for demo %s", demo.get("_id"))


def preview_demo_decision(db, demo_id, decision: str) -> dict:
    """Describe what ``apply_demo_decision`` would do, without changing anything.

    The admin confirm dialog shows these effects so that no approval or
    rejection path has side effects the moderator cannot see beforehand.
    """
    if decision not in DECISION_STATES:
        raise ValueError("Unsupported demonstration decision")
    demo_oid = _object_id(demo_id)
    demo = db.demonstrations.find_one(
        {"_id": demo_oid},
        {"title": 1, "approved": 1, "rejected": 1, "cancelled": 1, CITY_ASSIGNMENT_FIELD: 1},
    )
    if not demo:
        raise LookupError("Demonstration not found")

    target = DECISION_STATES[decision]
    status_changes = (
        bool(demo.get("approved")) != target["approved"]
        or bool(demo.get("rejected")) != target["rejected"]
    )
    submitter = db.submitters.find_one(
        {"demonstration_id": demo_oid}, {"submitter_email": 1}
    )
    links = list(
        db.magic_links.find(
            {
                "demo_id": str(demo_oid),
                "action": {"$in": list(TERMINAL_TOKEN_ACTIONS)},
                "revoked": {"$ne": True},
                "used_at": None,
            },
            {"action": 1},
        )
    )
    open_cases = db.cases.count_documents(
        {
            "demo_id": {"$in": [demo_oid, str(demo_oid)]},
            "meta.closed": {"$ne": True},
        }
    )
    return {
        "decision": decision,
        "title": demo.get("title") or "",
        "status_changes": status_changes,
        "cancelled": bool(demo.get("cancelled")),
        "emails_submitter": bool(
            status_changes and (submitter or {}).get("submitter_email")
        ),
        "has_submitter_email": bool((submitter or {}).get("submitter_email")),
        "open_cases": open_cases,
        "links_revoked": len(links),
        "edit_links_revoked": sum(1 for link in links if link.get("action") == "edit"),
        "leaves_city_queue": bool(demo.get(CITY_ASSIGNMENT_FIELD)),
    }


def undo_approval_minutes(value=None) -> int:
    """Configured undo window in minutes (``DEMO_APPROVAL_UNDO_MINUTES``)."""
    if value is None:
        from flask import current_app, has_app_context

        value = current_app.config.get("DEMO_APPROVAL_UNDO_MINUTES") if has_app_context() else None
        if value is None:
            from config import Config

            value = getattr(Config, "DEMO_APPROVAL_UNDO_MINUTES", DEFAULT_UNDO_APPROVAL_MINUTES)
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_UNDO_APPROVAL_MINUTES
    return max(minutes, 0)


def approval_undo_deadline(demo, minutes=None):
    """When the current approval stops being undoable, or None if it is not."""
    if not demo or not demo.get("approved"):
        return None
    decision = demo.get("moderation_decision") or {}
    approved_at = decision.get("at")
    if decision.get("status") != "approved" or approved_at is None:
        return None
    window = undo_approval_minutes(minutes)
    if window <= 0:
        return None
    deadline = approved_at + timedelta(minutes=window)
    return deadline if deadline > utcnow() else None


class UndoWindowClosed(ValueError):
    """The approval is no longer the current decision or its window has passed."""


def undo_demo_approval(db, demo_id, *, actor: dict, source: str, minutes=None) -> DemoDecisionResult:
    """Return a just-approved demo to pending within the undo window.

    Maintainer decision (20 Nov 2026): a mistaken approval can be undone for a
    short while. The demo leaves the public site and goes back to the pending
    queue, its case reopens, and links the approval revoked are restored. No
    email is sent; if the demo is approved again the submitter is not emailed
    twice. After the window, use a take-down instead.
    """
    demo_oid = _object_id(demo_id)
    window = undo_approval_minutes(minutes)
    now = utcnow()
    if window <= 0:
        raise UndoWindowClosed("Undoing approvals is turned off")
    decision_meta = {
        "id": uuid4().hex,
        "status": "pending",
        "at": now,
        "actor": actor,
        "source": source,
        "notify_submitter": False,
    }
    before = db.demonstrations.find_one_and_update(
        {
            "_id": demo_oid,
            "approved": True,
            "moderation_decision.status": "approved",
            "moderation_decision.at": {"$gt": now - timedelta(minutes=window)},
        },
        {"$set": {**UNDO_APPROVAL_STATE, "last_modified": now, "moderation_decision": decision_meta}},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        if not db.demonstrations.find_one({"_id": demo_oid}, {"_id": 1}):
            raise LookupError("Demonstration not found")
        raise UndoWindowClosed("The approval can no longer be undone")

    undone = before.get("moderation_decision") or {}
    undone_id = undone.get("id")
    db.demonstrations.update_one(
        {"_id": demo_oid, "moderation_decision.id": decision_meta["id"]},
        {"$set": {"moderation_decision.undone_decision_id": undone_id}},
    )
    decision_meta["undone_decision_id"] = undone_id
    current = {**before, **UNDO_APPROVAL_STATE, "last_modified": now, "moderation_decision": decision_meta}
    actor_name = _actor_label(actor or {})

    cases = db.cases.update_many(
        {"demo_id": {"$in": [demo_oid, str(demo_oid)]}, "meta.decision_id": undone_id},
        {
            "$set": {"meta.closed": False, "meta.decision_id": decision_meta["id"], "updated_at": now},
            "$unset": {
                "status": "", "outcome": "", "resolution": "",
                "meta.closed_at": "", "meta.closed_reason": "",
                "meta.outcome": "", "meta.resolution": "",
            },
            "$push": {
                "action_logs": {
                    "timestamp": now,
                    "admin": actor_name,
                    "actor": actor,
                    "action_type": "undo_approval",
                    "note": (
                        f"Mielenosoituksen {before.get('title') or 'tuntematon'} ({demo_oid}) "
                        "hyväksyntä peruttiin. Tapaus avattiin uudelleen."
                    ),
                },
                "case_history": {
                    "timestamp": now,
                    "action": "Hyväksyntä peruttu",
                    "user": actor_name,
                    "metadata": {
                        "decision": "pending",
                        "decision_id": decision_meta["id"],
                        "undone_decision_id": undone_id,
                        "source": source,
                        "actor": actor,
                    },
                },
            },
        },
    )

    restored = 0
    if undone_id:
        restored = db.magic_links.update_many(
            {"demo_id": str(demo_oid), "decision_id": undone_id, "revoked": True, "used_at": None},
            {
                "$set": {"revoked": False, "restored_at": now, "restored_by": actor_name},
                "$unset": {"revoked_at": "", "revoked_by": "", "revoked_reason": "", "decision_id": ""},
            },
        ).modified_count

    record_demo_change(
        demo_oid,
        before,
        current,
        action="undo_approval",
        message="Mielenosoituksen hyväksyntä peruttiin",
        extra_details={
            "decision": "pending",
            "decision_id": decision_meta["id"],
            "undone_decision_id": undone_id,
            "source": source,
            "links_restored": restored,
        },
        actor=actor,
    )
    return DemoDecisionResult(
        demo=current,
        decision_id=decision_meta["id"],
        changed=True,
        cases_updated=cases.modified_count,
        tokens_revoked=0,
        notification_queued=False,
    )
