"""Vuorot: national moderators book duty shifts and OT members approve them.

One page shows every shift the viewer may see with its state (Olivia,
21 Nov 2026). Appointments, freezing and revocation stay in Hallinto; this
page changes only shifts, through ``utils/duty_shifts.py``.
"""

import hmac
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from mielenosoitukset_fi.utils import duty_shifts
from mielenosoitukset_fi.utils.duty_shifts import DutyError

from .board_compliance import governance_csrf_token
from .utils import _ADMIN_TEMPLATE_FOLDER, mongo

admin_shifts_bp = Blueprint("admin_shifts", __name__, url_prefix="/admin/vuorot")

HELSINKI = ZoneInfo("Europe/Helsinki")

ERRORS = {
    "invalid_window": "Tarkista vuoron alku ja loppu: lopun pitää olla alun jälkeen ja tulevaisuudessa.",
    "outside_appointment": "Vuoro ei mahdu nimityksen voimassaoloon.",
    "unknown_grant": "Nimitystä ei löytynyt tai se on peruttu.",
    "unknown_shift": "Vuoroa ei löytynyt.",
    "not_allowed": "Sinulla ei ole oikeutta tähän vuoroon.",
    "self_approval": "Omaa vuoroa ei voi hyväksyä.",
    "reason_required": "Kirjoita perustelu.",
    "invalid_starts_at": "Tarkista vuoron alku.",
    "invalid_ends_at": "Tarkista vuoron loppu.",
    "invalid_block": "Vuoro on aina kaksi tuntia ja alkaa parillisena kellonaikana.",
    "already_booked": "Tämä vuoro on jo varattu tälle moderaattorille.",
    "too_many_in_a_row": "Enintään 4 vuoroa (8 tuntia) voi varata peräkkäin.",
}
DAYS = ["Maanantai", "Tiistai", "Keskiviikko", "Torstai", "Perjantai", "Lauantai", "Sunnuntai"]
BLOCK_HOURS = list(range(0, 24, 2))


def _local(value: str, field: str) -> datetime:
    """A datetime-local field in Helsinki time, as UTC."""
    try:
        parsed = datetime.fromisoformat(value or "")
    except ValueError as exc:
        raise DutyError(f"invalid_{field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HELSINKI)
    return parsed.astimezone(timezone.utc)


def _week_start(value) -> date:
    """The Monday of the week asked for (``viikko=YYYY-MM-DD``), or this week."""
    try:
        day = date.fromisoformat(value or "")
    except ValueError:
        day = datetime.now(HELSINKI).date()
    return day - timedelta(days=day.weekday())


def _calendar(rows: list, monday: date, can_book: bool) -> list:
    """Seven days of two-hour blocks with the shifts in each."""
    now = datetime.now(timezone.utc)
    by_block: dict = {}
    for row in rows:
        local = datetime.strptime(row["starts_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).astimezone(HELSINKI)
        by_block.setdefault((local.date(), local.hour // 2 * 2), []).append(row)
    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        blocks = []
        for hour in BLOCK_HOURS:
            start = datetime(day.year, day.month, day.day, hour, tzinfo=HELSINKI)
            shifts = [r for r in by_block.get((day, hour), []) if r["state"] != "cancelled"]
            blocks.append({
                "label": f"{hour:02d}–{hour + 2:02d}",
                "slot": start.strftime("%Y-%m-%dT%H:%M"),
                "shifts": shifts,
                "past": start.astimezone(timezone.utc) + duty_shifts.SHIFT_LENGTH <= now,
                "can_book": can_book,
            })
        days.append({"name": DAYS[offset], "date": day, "blocks": blocks,
                     "today": day == datetime.now(HELSINKI).date()})
    return days


def _actor() -> dict:
    return {"id": str(current_user.id), "username": current_user.username, "via": "admin_shifts"}


def _may_view() -> bool:
    return bool(current_user.has_full_permissions() or current_user.is_national_moderator
                or duty_shifts.bookable_grants(mongo, current_user))


@admin_shifts_bp.route("/", methods=["GET", "POST"])
@login_required
def shifts():
    if not _may_view():
        abort(403)
    if request.method == "POST":
        expected = session.get("governance_csrf_token", "")
        if not expected or not hmac.compare_digest(expected, request.form.get("csrf_token", "")):
            abort(403)
        action = request.form.get("action")
        try:
            if action == "book":
                start = _local(request.form.get("slot"), "starts_at")
                duty_shifts.add_shift(
                    mongo, grant_id=request.form.get("grant_id"), starts_at=start,
                    ends_at=start + duty_shifts.SHIFT_LENGTH,
                    source={"decision": "Vuorovaraus", "via": "admin"}, actor=_actor())
                flash("Vuoro varattu.", "success")
            elif action == "approve":
                duty_shifts.approve_shift(mongo, shift_id=request.form.get("shift_id"), actor=_actor())
                flash("Vuoro hyväksytty.", "success")
            elif action == "cancel":
                duty_shifts.cancel_shift(mongo, shift_id=request.form.get("shift_id"),
                                         reason=request.form.get("reason", ""), actor=_actor())
                flash("Vuoro peruttu. Oikeudet päättyivät heti.", "success")
            else:
                abort(400)
        except DutyError as exc:
            flash(ERRORS.get(exc.code, "Muutosta ei voitu tehdä."), "danger")
        week = request.form.get("viikko")
        return redirect(url_for("admin_shifts.shifts", **({"viikko": week} if week else {})), code=303)
    monday = _week_start(request.args.get("viikko"))
    since = datetime(monday.year, monday.month, monday.day, tzinfo=HELSINKI)
    grants = duty_shifts.bookable_grants(mongo, current_user)
    # Everyone who may open the page sees who is on duty when; the list below
    # holds only the shifts one may act on.
    week_rows = duty_shifts.week_overview(mongo, since, since + timedelta(days=7), viewer_id=current_user.id)
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}shifts.html",
        rows=duty_shifts.schedule(mongo, current_user),
        grants=grants,
        days=_calendar(week_rows, monday, bool(grants)),
        week=monday.isoformat(),
        week_end=monday + timedelta(days=6),
        monday=monday,
        previous_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        this_week=_week_start(None).isoformat(),
        max_blocks=duty_shifts.MAX_BLOCKS_IN_A_ROW,
        governance_csrf_token=governance_csrf_token(),
        grace_minutes=int(duty_shifts.END_GRACE.total_seconds() // 60),
    )
