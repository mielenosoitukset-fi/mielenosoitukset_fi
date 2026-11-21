"""Vuorot: national moderators book duty shifts and OT members approve them.

One page shows every shift the viewer may see with its state (Olivia,
21 Nov 2026). Appointments, freezing and revocation stay in Hallinto; this
page changes only shifts, through ``utils/duty_shifts.py``.
"""

import hmac
from datetime import datetime, timezone
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
}


def _local(value: str, field: str) -> datetime:
    """A datetime-local field in Helsinki time, as UTC."""
    try:
        parsed = datetime.fromisoformat(value or "")
    except ValueError as exc:
        raise DutyError(f"invalid_{field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HELSINKI)
    return parsed.astimezone(timezone.utc)


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
                duty_shifts.add_shift(
                    mongo, grant_id=request.form.get("grant_id"),
                    starts_at=_local(request.form.get("starts_at"), "starts_at"),
                    ends_at=_local(request.form.get("ends_at"), "ends_at"),
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
        return redirect(url_for("admin_shifts.shifts"), code=303)
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}shifts.html",
        rows=duty_shifts.schedule(mongo, current_user),
        grants=duty_shifts.bookable_grants(mongo, current_user),
        governance_csrf_token=governance_csrf_token(),
        grace_minutes=int(duty_shifts.END_GRACE.total_seconds() // 60),
    )
