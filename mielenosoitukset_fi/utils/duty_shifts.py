"""National moderators whose rights exist only during a duty shift.

The hallintomalli lets OT appoint national moderators. Their national
moderation rights are active only during a duty shift. Nothing here relies on
a scheduled job: the signed-in account's rights are computed from the stored
shift windows on every request (see ``User.apply_active_duty``), so a right
ends 30 minutes after its shift ends (``END_GRACE``), or at once when the shift
is cancelled or its appointment is frozen or revoked.

Collections:

``national_moderators``
    One appointment per document: ``user_id``, ``permissions``,
    ``valid_from``/``valid_until`` (UTC, end exclusive), ``frozen_at``,
    ``revoked_at`` and ``source`` (the decision it rests on).
``moderator_shifts``
    ``grant_id``, ``user_id``, ``starts_at``/``ends_at`` (UTC, end
    exclusive), ``approved_at``/``approved_by``, ``cancelled_at`` and
    ``source``.

Shifts (Olivia, 21 Nov 2026): the moderator books their own shifts within
the appointment, and an OT member approves them. Only an approved shift gives
rights. The appointment may allow booking without approval
(``self_booking``). The appointment lists the accounts that may approve its
shifts (``approvers``, the OT members' accounts sent by Hallinto); nobody
approves their own shift.
``moderator_duty_uses``
    Append-only record of each state-changing request made during a shift.

Appointments and shifts change only through the functions below, which take
an explicit actor. The Hallinto route that calls them follows the city-admin
contract and is not part of this module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from bson import ObjectId

GRANTS = "national_moderators"
SHIFTS = "moderator_shifts"
USES = "moderator_duty_uses"

# national_moderator_v1 (Olivia, 21 Nov 2026): approve or reject submissions,
# fix their text, create edit links and work the moderation cases, anywhere in
# the country. No deletion, no user, organization or city management.
NATIONAL_MODERATOR_V1 = frozenset(
    {
        "LIST_DEMOS",
        "VIEW_DEMO",
        "EDIT_DEMO",
        "ACCEPT_DEMO",
        "LIST_CASES",
        "VIEW_CASE",
        "EDIT_CASE",
        "GENERATE_EDIT_LINK",
    }
)

# Rights continue this long after a shift ends so work started near the end
# can be finished (Olivia, 21 Nov 2026). A cancelled shift gets no grace.
END_GRACE = timedelta(minutes=30)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class DutyError(ValueError):
    """A refused appointment or shift change. ``code`` is machine-readable."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now(now: Optional[datetime] = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DutyError(f"invalid_{field}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _naive(value: datetime) -> datetime:
    """Mongo returns naive UTC datetimes; compare like with like."""
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _oid(value: Any, code: str) -> ObjectId:
    try:
        return value if isinstance(value, ObjectId) else ObjectId(str(value))
    except Exception as exc:  # bson.errors.InvalidId
        raise DutyError(code) from exc


def _actor_ref(actor: dict) -> dict:
    if not isinstance(actor, dict) or not actor.get("id"):
        raise DutyError("invalid_actor")
    return {k: str(actor[k]) for k in ("id", "username", "via") if actor.get(k)}


def _source(source: dict) -> dict:
    """The decision an appointment or shift rests on. Required."""
    if not isinstance(source, dict) or not str(source.get("decision") or "").strip():
        raise DutyError("source_required")
    return {k: str(v)[:300] for k, v in source.items() if v not in (None, "")}


def _is_approver(grant: Optional[dict], actor_ref: dict) -> bool:
    return bool(grant) and not grant.get("revoked_at") and actor_ref["id"] in {
        str(a) for a in grant.get("approvers") or ()}


def _usable_grant(grant: Optional[dict], now: datetime) -> bool:
    if not grant or grant.get("revoked_at") or grant.get("frozen_at"):
        return False
    start, end = grant.get("valid_from"), grant.get("valid_until")
    current = _naive(now)
    if start is not None and _naive(_utc(start, "valid_from")) > current:
        return False
    if end is not None and _naive(_utc(end, "valid_until")) <= current:
        return False
    return True


def active_duty(db, user_id: Any, now: Optional[datetime] = None) -> Optional[dict]:
    """Return the shift the account is on right now, or None.

    The result names the shift, its appointment and the moderation
    permissions that apply during it. Permissions are limited to
    ``NATIONAL_MODERATOR_V1`` whatever the stored document says.
    """
    current = _naive(_now(now))
    oid = _oid(user_id, "invalid_user")
    shifts = db[SHIFTS].find(
        {
            "user_id": oid,
            "starts_at": {"$lte": current},
            "ends_at": {"$gt": current - END_GRACE},
            "approved_at": {"$ne": None},
            "$or": [{"cancelled_at": {"$exists": False}}, {"cancelled_at": None}],
        }
    ).sort("starts_at", 1)
    for shift in shifts:
        grant = db[GRANTS].find_one({"_id": shift.get("grant_id"), "user_id": oid})
        if not _usable_grant(grant, _now(now)):
            continue
        permissions = sorted(set(grant.get("permissions") or ()) & NATIONAL_MODERATOR_V1)
        if not permissions:
            continue
        return {
            "shift_id": str(shift["_id"]),
            "grant_id": str(grant["_id"]),
            "ends_at": shift["ends_at"],
            "in_grace": _naive(_utc(shift["ends_at"], "ends_at")) <= current,
            "permissions": permissions,
        }
    return None


def appoint(db, *, user_id: Any, valid_from: datetime, valid_until: datetime,
            source: dict, actor: dict, permissions: Iterable[str] = NATIONAL_MODERATOR_V1,
            approvers: Iterable[Any] = (), self_booking: bool = False,
            now: Optional[datetime] = None) -> dict:
    """Record a national moderator appointment. Grants nothing until a shift."""
    oid = _oid(user_id, "invalid_user")
    actor_ref = _actor_ref(actor)
    if actor_ref["id"] == str(oid):
        raise DutyError("self_appointment")
    start, end = _utc(valid_from, "valid_from"), _utc(valid_until, "valid_until")
    if end <= start or end <= _now(now):
        raise DutyError("invalid_validity")
    perms = sorted(set(permissions))
    if not perms or not set(perms) <= NATIONAL_MODERATOR_V1:
        raise DutyError("invalid_permissions")
    approver_ids = sorted({_oid(a, "invalid_approver") for a in approvers} - {oid})
    if len(approver_ids) > 50:
        raise DutyError("invalid_approver")
    user = db.users.find_one({"_id": oid}, {"active": 1, "banned": 1, "account_type": 1})
    if not user or user.get("banned") or user.get("active") is False:
        raise DutyError("ineligible_user")
    if user.get("account_type") == "organization":
        raise DutyError("ineligible_user")
    doc = {
        "_id": ObjectId(),
        "user_id": oid,
        "kind": "national_moderator_v1",
        "permissions": perms,
        "self_booking": bool(self_booking),
        "approvers": approver_ids,
        "valid_from": start,
        "valid_until": end,
        "source": _source(source),
        "created_by": actor_ref,
        "created_at": _now(now),
    }
    db[GRANTS].insert_one(doc)
    return doc


def add_shift(db, *, grant_id: Any, starts_at: datetime, ends_at: datetime,
              source: dict, actor: dict, now: Optional[datetime] = None) -> dict:
    """Book a duty shift inside the appointment's validity.

    A shift booked by someone else is approved by that booking. One the
    moderator books is approved only if the appointment allows self-booking;
    otherwise it waits for ``approve_shift``.
    """
    gid = _oid(grant_id, "unknown_grant")
    actor_ref = _actor_ref(actor)
    grant = db[GRANTS].find_one({"_id": gid})
    if not grant or grant.get("revoked_at"):
        raise DutyError("unknown_grant")
    own = actor_ref["id"] == str(grant["user_id"])
    if not own and not _is_approver(grant, actor_ref):
        raise DutyError("not_allowed")
    start, end = _utc(starts_at, "starts_at"), _utc(ends_at, "ends_at")
    if end <= start or end <= _now(now):
        raise DutyError("invalid_window")
    if _naive(start) < _naive(_utc(grant["valid_from"], "valid_from")) or _naive(end) > _naive(
            _utc(grant["valid_until"], "valid_until")):
        raise DutyError("outside_appointment")
    doc = {
        "_id": ObjectId(),
        "grant_id": gid,
        "user_id": grant["user_id"],
        "starts_at": start,
        "ends_at": end,
        "source": _source(source),
        "created_by": actor_ref,
        "created_at": _now(now),
    }
    if not own or grant.get("self_booking"):
        doc.update(approved_at=_now(now), approved_by=actor_ref,
                   approval="self_booking" if own else "booked_by_other")
    db[SHIFTS].insert_one(doc)
    return doc


def approve_shift(db, *, shift_id: Any, actor: dict, now: Optional[datetime] = None) -> bool:
    """Approve a booked shift. The moderator cannot approve their own."""
    sid = _oid(shift_id, "unknown_shift")
    actor_ref = _actor_ref(actor)
    shift = db[SHIFTS].find_one({"_id": sid})
    if not shift:
        raise DutyError("unknown_shift")
    if actor_ref["id"] == str(shift["user_id"]):
        raise DutyError("self_approval")
    if not _is_approver(db[GRANTS].find_one({"_id": shift["grant_id"]}), actor_ref):
        raise DutyError("not_allowed")
    result = db[SHIFTS].update_one(
        {"_id": sid, "approved_at": None,
         "$or": [{"cancelled_at": {"$exists": False}}, {"cancelled_at": None}]},
        {"$set": {"approved_at": _now(now), "approved_by": actor_ref, "approval": "approved"}},
    )
    return bool(result.modified_count)


def cancel_shift(db, *, shift_id: Any, reason: str, actor: dict,
                 now: Optional[datetime] = None) -> bool:
    """Cancel or cut short a shift. Rights end on the next request.

    The moderator may cancel their own shift; an approver may cancel any of
    the appointment's shifts.
    """
    if not str(reason or "").strip():
        raise DutyError("reason_required")
    actor_ref = _actor_ref(actor)
    shift = db[SHIFTS].find_one({"_id": _oid(shift_id, "unknown_shift")})
    if not shift:
        raise DutyError("unknown_shift")
    if actor_ref["id"] != str(shift["user_id"]) and not _is_approver(
            db[GRANTS].find_one({"_id": shift["grant_id"]}), actor_ref):
        raise DutyError("not_allowed")
    result = db[SHIFTS].update_one(
        {"_id": _oid(shift_id, "unknown_shift"),
         "$or": [{"cancelled_at": {"$exists": False}}, {"cancelled_at": None}]},
        {"$set": {"cancelled_at": _now(now), "cancel_reason": str(reason)[:300],
                  "cancelled_by": actor_ref}},
    )
    return bool(result.modified_count)


def set_frozen(db, *, grant_id: Any, frozen: bool, reason: str, actor: dict,
               now: Optional[datetime] = None) -> bool:
    """Freeze (or unfreeze) an appointment. A frozen one activates no shift."""
    if not str(reason or "").strip():
        raise DutyError("reason_required")
    gid = _oid(grant_id, "unknown_grant")
    update = (
        {"$set": {"frozen_at": _now(now), "freeze_reason": str(reason)[:300],
                  "frozen_by": _actor_ref(actor)}}
        if frozen else
        {"$unset": {"frozen_at": "", "freeze_reason": "", "frozen_by": ""},
         "$push": {"unfreezes": {"at": _now(now), "reason": str(reason)[:300],
                                 "by": _actor_ref(actor)}}}
    )
    query = {"_id": gid, "revoked_at": None} if frozen else {"_id": gid, "frozen_at": {"$ne": None},
                                                             "revoked_at": None}
    return bool(db[GRANTS].update_one(query, update).modified_count)


def revoke(db, *, grant_id: Any, reason: str, actor: dict,
           now: Optional[datetime] = None) -> bool:
    """Revoke an appointment for good. Its shifts no longer give any right."""
    if not str(reason or "").strip():
        raise DutyError("reason_required")
    result = db[GRANTS].update_one(
        {"_id": _oid(grant_id, "unknown_grant"), "revoked_at": None},
        {"$set": {"revoked_at": _now(now), "revoke_reason": str(reason)[:300],
                  "revoked_by": _actor_ref(actor)}},
    )
    return bool(result.modified_count)


def record_use(db, user, *, method: str, endpoint: Optional[str], path: str,
               status: int, now: Optional[datetime] = None) -> None:
    """Log a state-changing request made with shift rights."""
    duty = getattr(user, "active_duty", None)
    if not duty or method in SAFE_METHODS:
        return
    db[USES].insert_one(
        {
            "user_id": ObjectId(str(user.id)),
            "shift_id": ObjectId(duty["shift_id"]),
            "grant_id": ObjectId(duty["grant_id"]),
            "method": method,
            "endpoint": endpoint,
            "path": path[:300],
            "status": int(status),
            "at": _now(now),
        }
    )


# Shift states shown in the schedule.
REQUESTED, APPROVED, RUNNING, GRACE, ENDED, CANCELLED, INACTIVE = (
    "requested", "approved", "running", "grace", "ended", "cancelled", "inactive")


def shift_state(shift: dict, grant: Optional[dict], now: Optional[datetime] = None) -> str:
    """One state for the schedule, computed the same way as ``active_duty``."""
    current = _naive(_now(now))
    start = _naive(_utc(shift["starts_at"], "starts_at"))
    end = _naive(_utc(shift["ends_at"], "ends_at"))
    if shift.get("cancelled_at"):
        return CANCELLED
    if current >= end + END_GRACE:
        return ENDED
    if not shift.get("approved_at"):
        return REQUESTED
    if not _usable_grant(grant, _now(now)) and current >= start:
        return INACTIVE
    if current < start:
        return APPROVED
    return RUNNING if current < end else GRACE


def is_moderator(db, user_id: Any) -> bool:
    """Whether the account holds a national moderator appointment that is
    not revoked. It lets them open the admin and book shifts; it gives no
    moderation right by itself."""
    return bool(db[GRANTS].find_one(
        {"user_id": _oid(user_id, "invalid_user"), "revoked_at": None}, {"_id": 1}))


def schedule(db, viewer, now: Optional[datetime] = None, limit: int = 300) -> list[dict]:
    """Shifts the viewer may see: their own, those of appointments they
    approve, or all for an account with full permissions. Newest first."""
    vid = _oid(viewer.id, "invalid_user")
    if viewer.has_full_permissions():
        grants = list(db[GRANTS].find({}))
    else:
        grants = list(db[GRANTS].find({"$or": [{"user_id": vid}, {"approvers": vid}]}))
    by_id = {g["_id"]: g for g in grants}
    if not by_id:
        return []
    users = {u["_id"]: u for u in db.users.find(
        {"_id": {"$in": list({g["user_id"] for g in grants})}}, {"username": 1, "displayname": 1})}
    rows = []
    for shift in db[SHIFTS].find({"grant_id": {"$in": list(by_id)}}).sort("starts_at", -1).limit(limit):
        grant = by_id[shift["grant_id"]]
        user = users.get(grant["user_id"]) or {}
        own = shift["user_id"] == vid
        approver = vid in (grant.get("approvers") or ())
        state = shift_state(shift, grant, now)
        rows.append({
            "id": str(shift["_id"]),
            "moderator": user.get("displayname") or user.get("username") or str(grant["user_id"]),
            "starts_at": shift["starts_at"],
            "ends_at": shift["ends_at"],
            "state": state,
            "approval": shift.get("approval"),
            "cancel_reason": shift.get("cancel_reason"),
            "can_approve": approver and not own and state == REQUESTED,
            "can_cancel": (own or approver) and state in (REQUESTED, APPROVED, RUNNING, GRACE),
        })
    return rows


def bookable_grants(db, viewer, now: Optional[datetime] = None) -> list[dict]:
    """Appointments the viewer may book shifts for: their own, or those they
    approve. Frozen, revoked and expired ones are left out."""
    vid = _oid(viewer.id, "invalid_user")
    current = _now(now)
    rows = []
    for grant in db[GRANTS].find({"$or": [{"user_id": vid}, {"approvers": vid}], "revoked_at": None}):
        if grant.get("frozen_at") or _naive(_utc(grant["valid_until"], "valid_until")) <= _naive(current):
            continue
        user = db.users.find_one({"_id": grant["user_id"]}, {"username": 1, "displayname": 1}) or {}
        rows.append({
            "id": str(grant["_id"]),
            "own": grant["user_id"] == vid,
            "moderator": user.get("displayname") or user.get("username") or "",
            "valid_until": grant["valid_until"],
            "needs_approval": grant["user_id"] == vid and not grant.get("self_booking"),
        })
    return rows
