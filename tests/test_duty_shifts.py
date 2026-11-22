"""National moderators: rights only during a duty shift, checked per request."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils import duty_shifts
from mielenosoitukset_fi.utils.duty_shifts import DutyError, NATIONAL_MODERATOR_V1
from tests.conftest import _client_for_user

SOURCE = {"decision": "OT 21.11.2026 §3", "hallinto_decision_id": "d-1"}


def _actor(db):
    actor_id = ObjectId()
    return {"id": str(actor_id), "username": "ot-recorder", "via": "test"}


def _moderator(db):
    user_id = ObjectId()
    suffix = str(user_id)[-8:]
    doc = User.create_user(
        username=f"moderator-{suffix}", password="ModPass1!",
        email=f"moderator-{suffix}@example.test", displayname="Moderator",
    )
    doc.update({"_id": user_id, "confirmed": True, "active": True, "role": "user",
                "global_admin": False, "global_permissions": []})
    db.users.insert_one(doc)
    return user_id


def _block(now, ahead=0):
    """A two-hour block: the one containing ``now``, or ``ahead`` blocks later."""
    start = duty_shifts.block_start(now) + timedelta(hours=2 * ahead)
    return start, start + duty_shifts.SHIFT_LENGTH


def _appoint_with_shift(db, user_id, start, end, *, now):
    actor = _actor(db)
    grant = duty_shifts.appoint(
        db, user_id=user_id, valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=30), source=SOURCE, actor=actor,
        approvers=[actor["id"]], now=now,
    )
    shift = duty_shifts.add_shift(
        db, grant_id=grant["_id"], starts_at=start, ends_at=end,
        source=SOURCE, actor=actor, now=now - timedelta(days=1),
    )
    return grant, shift, actor


def test_rights_exist_only_inside_the_shift_plus_grace(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    start, end = _block(now, 1)
    _appoint_with_shift(db, user_id, start, end, now=now)

    assert duty_shifts.active_duty(db, user_id, now) is None
    during = duty_shifts.active_duty(db, user_id, start + timedelta(minutes=1))
    assert set(during["permissions"]) == NATIONAL_MODERATOR_V1
    assert during["in_grace"] is False
    grace = duty_shifts.active_duty(db, user_id, end + timedelta(minutes=29))
    assert grace and grace["in_grace"] is True
    assert duty_shifts.active_duty(db, user_id, end + timedelta(minutes=30)) is None


def test_cancel_freeze_and_revoke_end_rights_at_once(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    start, end = _block(now)
    grant, shift, actor = _appoint_with_shift(db, user_id, start, end, now=now)
    assert duty_shifts.active_duty(db, user_id, now)

    assert duty_shifts.set_frozen(db, grant_id=grant["_id"], frozen=True, reason="tutkinta", actor=actor)
    assert duty_shifts.active_duty(db, user_id, now) is None
    # A freeze cancels the open shifts; lifting it brings none of them back.
    assert db.moderator_shifts.find_one({"_id": shift["_id"]})["cancelled_at"]
    assert duty_shifts.set_frozen(db, grant_id=grant["_id"], frozen=False, reason="selvitetty", actor=actor)
    assert duty_shifts.active_duty(db, user_id, now) is None
    assert duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=start, ends_at=end,
                                 source=SOURCE, actor=actor, now=now)
    assert duty_shifts.active_duty(db, user_id, now)

    shift = db.moderator_shifts.find_one({"grant_id": grant["_id"], "cancelled_at": None})
    assert duty_shifts.cancel_shift(db, shift_id=shift["_id"], reason="sairastui", actor=actor)
    # A cancelled shift gets no grace period either.
    assert duty_shifts.active_duty(db, user_id, now) is None

    duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=start, ends_at=end,
                          source=SOURCE, actor=actor, now=now)
    assert duty_shifts.active_duty(db, user_id, now)
    assert duty_shifts.revoke(db, grant_id=grant["_id"], reason="päättyi", actor=actor)
    assert duty_shifts.active_duty(db, user_id, now) is None
    with pytest.raises(DutyError):
        duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=start, ends_at=end,
                              source=SOURCE, actor=actor, now=now)


def test_writes_are_refused_without_basis_or_by_the_moderator(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    actor = _actor(db)
    kwargs = dict(user_id=user_id, valid_from=now, valid_until=now + timedelta(days=1),
                  approvers=[actor["id"]], now=now)
    with pytest.raises(DutyError, match="source_required"):
        duty_shifts.appoint(db, source={}, actor=actor, **kwargs)
    with pytest.raises(DutyError, match="self_appointment"):
        duty_shifts.appoint(db, source=SOURCE, actor={"id": str(user_id)}, **kwargs)
    with pytest.raises(DutyError, match="invalid_permissions"):
        duty_shifts.appoint(db, source=SOURCE, actor=actor, permissions=["EDIT_USER"], **kwargs)
    grant = duty_shifts.appoint(db, source=SOURCE, actor=actor, **kwargs)
    later, later_end = _block(now + timedelta(days=3))
    with pytest.raises(DutyError, match="outside_appointment"):
        duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=later, ends_at=later_end,
                              source=SOURCE, actor=actor, now=now)


def test_stored_permissions_never_include_shift_rights(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    _appoint_with_shift(db, user_id, *_block(now), now=now)
    user = User.from_db(db.users.find_one({"_id": user_id}))
    with app.app_context():
        user.apply_active_duty()
        assert "ACCEPT_DEMO" in user.global_permissions
        assert user.stored_global_permissions == []
        assert user.to_dict()["global_permissions"] == []
        assert user.has_admin_access()


def test_request_time_enforcement_and_use_log(app, db, seeded_data):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    grant, shift, actor = _appoint_with_shift(db, user_id, *_block(now), now=now)
    client = _client_for_user(app, user_id)
    pending = seeded_data["pending_demo_id"]

    assert client.get(f"/admin/demo/{pending}/decide").status_code == 302
    client.post(f"/api/admin/demo/{pending}/approve")
    use = db.moderator_duty_uses.find_one({"user_id": user_id})
    assert use and use["shift_id"] == shift["_id"] and use["method"] == "POST"

    # No scheduled job: cancelling the stored shift ends access on the next request.
    duty_shifts.cancel_shift(db, shift_id=shift["_id"], reason="vuoro loppui", actor=actor)
    assert client.post(f"/api/admin/demo/{pending}/approve").status_code in (302, 403)
    assert db.users.find_one({"_id": user_id})["global_permissions"] == []


def test_own_booking_waits_for_approval_unless_self_booking(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    me = {"id": str(user_id)}
    actor = _actor(db)
    window = dict(starts_at=_block(now)[0], ends_at=_block(now)[1], source=SOURCE, now=now)
    grant = duty_shifts.appoint(db, user_id=user_id, valid_from=now - timedelta(days=1),
                                valid_until=now + timedelta(days=1), source=SOURCE, actor=actor,
                                approvers=[actor["id"]], now=now)
    booked = duty_shifts.add_shift(db, grant_id=grant["_id"], actor=me, **window)
    assert duty_shifts.active_duty(db, user_id, now) is None
    with pytest.raises(DutyError, match="self_approval"):
        duty_shifts.approve_shift(db, shift_id=booked["_id"], actor=me)
    with pytest.raises(DutyError, match="not_allowed"):
        duty_shifts.approve_shift(db, shift_id=booked["_id"], actor=_actor(db))
    assert duty_shifts.approve_shift(db, shift_id=booked["_id"], actor=actor, now=now)
    assert duty_shifts.active_duty(db, user_id, now)

    other = _moderator(db)
    trusted = duty_shifts.appoint(db, user_id=other, valid_from=now - timedelta(days=1),
                                  valid_until=now + timedelta(days=1), source=SOURCE, actor=actor,
                                  self_booking=True, now=now)
    duty_shifts.add_shift(db, grant_id=trusted["_id"], actor={"id": str(other)}, **window)
    assert duty_shifts.active_duty(db, other, now)


def test_schedule_page_books_approves_and_lists_states(app, db, seeded_data):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    approver_id = _moderator(db)
    grant = duty_shifts.appoint(db, user_id=user_id, valid_from=now - timedelta(days=1),
                                valid_until=now + timedelta(days=30), source=SOURCE,
                                actor=_actor(db), approvers=[approver_id], now=now)
    moderator = _client_for_user(app, user_id)
    page = moderator.get("/admin/vuorot/")
    assert page.status_code == 200
    assert "Viikko" in page.get_data(as_text=True) and "Varaa vuoro" in page.get_data(as_text=True)
    with moderator.session_transaction() as session:
        token = session["governance_csrf_token"]
    start = _block(now, 1)[0]
    from zoneinfo import ZoneInfo
    local = lambda value: value.astimezone(ZoneInfo("Europe/Helsinki")).strftime("%Y-%m-%dT%H:%M")
    response = moderator.post("/admin/vuorot/", data={
        "csrf_token": token, "action": "book", "grant_id": str(grant["_id"]),
        "slot": local(start)})
    assert response.status_code == 303
    shift = db.moderator_shifts.find_one({"user_id": user_id})
    assert shift and not shift.get("approved_at")
    assert "Odottaa hyväksyntää" in moderator.get("/admin/vuorot/").get_data(as_text=True)
    # Without the session token nothing changes.
    assert moderator.post("/admin/vuorot/", data={"action": "approve", "shift_id": str(shift["_id"])}).status_code == 403

    approver = _client_for_user(app, approver_id)
    assert approver.get("/admin/vuorot/").status_code == 200
    with approver.session_transaction() as session:
        token = session["governance_csrf_token"]
    approver.post("/admin/vuorot/", data={"csrf_token": token, "action": "approve", "shift_id": str(shift["_id"])})
    assert db.moderator_shifts.find_one({"_id": shift["_id"]})["approved_at"]
    assert "Hyväksytty" in approver.get("/admin/vuorot/").get_data(as_text=True)

    outsider = _client_for_user(app, _moderator(db))
    assert outsider.get("/admin/vuorot/").status_code == 403


def test_shifts_are_two_hour_blocks_and_at_most_four_in_a_row(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    actor = _actor(db)
    grant = duty_shifts.appoint(db, user_id=user_id, valid_from=now - timedelta(days=1),
                                valid_until=now + timedelta(days=30), source=SOURCE, actor=actor,
                                approvers=[actor["id"]], now=now)
    book = lambda start, end: duty_shifts.add_shift(
        db, grant_id=grant["_id"], starts_at=start, ends_at=end, source=SOURCE, actor=actor, now=now)
    first = _block(now, 10)[0]
    block = lambda n: (first + timedelta(hours=2 * n), first + timedelta(hours=2 * n + 2))

    # Not two hours, or not on an even hour.
    with pytest.raises(DutyError, match="invalid_block"):
        book(first, first + timedelta(hours=3))
    with pytest.raises(DutyError, match="invalid_block"):
        book(first + timedelta(hours=1), first + timedelta(hours=3))

    for n in (0, 1, 3, 4):
        book(*block(n))
    with pytest.raises(DutyError, match="already_booked"):
        book(*block(1))
    # Block 2 would join 0-1 and 3-4 into five in a row.
    with pytest.raises(DutyError, match="too_many_in_a_row"):
        book(*block(2))
    # A gap is fine, and a run of four is the limit.
    book(*block(6))
    book(*block(7))
    book(*block(8))
    book(*block(9))
    with pytest.raises(DutyError, match="too_many_in_a_row"):
        book(*block(10))
    with pytest.raises(DutyError, match="too_many_in_a_row"):
        book(*block(5))
    # Cancelling one frees the slot.
    cancelled = db.moderator_shifts.find_one({"starts_at": {"$gte": block(9)[0].replace(tzinfo=None)}})
    duty_shifts.cancel_shift(db, shift_id=cancelled["_id"], reason="sairastui", actor=actor)
    book(*block(10))


def test_schedule_page_shows_the_requested_week(app, db, seeded_data):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    _appoint_with_shift(db, user_id, *_block(now, 1), now=now)
    moderator = _client_for_user(app, user_id)
    text = moderator.get("/admin/vuorot/").get_data(as_text=True)
    assert "Maanantai" in text and "Sunnuntai" in text and "00–02" in text and "22–24" in text
    far = (now + timedelta(days=60)).date().isoformat()
    assert moderator.get(f"/admin/vuorot/?viikko={far}").status_code == 200
    assert moderator.get("/admin/vuorot/?viikko=not-a-date").status_code == 200


def test_calendar_shows_every_moderators_shifts_to_each_moderator(app, db, seeded_data):
    now = datetime.now(timezone.utc)
    mine, theirs = _moderator(db), _moderator(db)
    db.users.update_one({"_id": theirs}, {"$set": {"displayname": "Toinen Moderaattori"}})
    start = _block(now, 1)[0]
    last = (start + timedelta(hours=4) - timedelta(seconds=1)).astimezone(duty_shifts.HELSINKI)
    if last.isocalendar()[:2] != start.astimezone(duty_shifts.HELSINKI).isocalendar()[:2]:
        start += timedelta(days=1)  # Sunday evening: keep both shifts in one week
    _appoint_with_shift(db, mine, start, start + duty_shifts.SHIFT_LENGTH, now=now)
    _appoint_with_shift(db, theirs, start + timedelta(hours=2), start + timedelta(hours=4), now=now)
    week = start.astimezone(duty_shifts.HELSINKI).date().isoformat()
    text = _client_for_user(app, mine).get(f"/admin/vuorot/?viikko={week}").get_data(as_text=True)
    assert "Toinen Moderaattori" in text and "Minä" in text


def test_a_frozen_appointment_takes_no_bookings_and_loses_open_shifts(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    start, end = _block(now, 3)
    grant, shift, actor = _appoint_with_shift(db, user_id, start, end, now=now)
    later_start, later_end = _block(now, 5)
    other = duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=later_start, ends_at=later_end,
                                  source=SOURCE, actor=actor, now=now)

    assert duty_shifts.set_frozen(db, grant_id=grant["_id"], frozen=True, reason="tutkinta", actor=actor)
    for row in (shift, other):
        assert db.moderator_shifts.find_one({"_id": row["_id"]})["cancelled_at"]

    own = {"id": str(user_id), "username": "moderator", "via": "test"}
    new_start, new_end = _block(now, 7)
    for who in (own, actor):
        with pytest.raises(DutyError) as refused:
            duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=new_start, ends_at=new_end,
                                  source=SOURCE, actor=who, now=now)
        assert refused.value.code == "frozen_appointment"
    assert db.moderator_shifts.count_documents({"grant_id": grant["_id"], "cancelled_at": None}) == 0


def test_revoking_cancels_open_shifts(app, db):
    now = datetime.now(timezone.utc)
    user_id = _moderator(db)
    start, end = _block(now, 3)
    grant, shift, actor = _appoint_with_shift(db, user_id, start, end, now=now)
    assert duty_shifts.revoke(db, grant_id=grant["_id"], reason="päättyi", actor=actor)
    assert db.moderator_shifts.find_one({"_id": shift["_id"]})["cancelled_at"]
