from mielenosoitukset_fi.utils.time_utils import utcnow
from datetime import datetime, date
import hmac
import secrets

from bson.objectid import ObjectId
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, current_app
from flask_login import login_required
from pymongo.errors import PyMongoError
from mielenosoitukset_fi.utils.flashing import flash_message

from mielenosoitukset_fi.utils.classes import RecurringDemonstration, Organizer
from mielenosoitukset_fi.utils.variables import CITY_LIST
from mielenosoitukset_fi.utils.wrappers import (
    permission_required, admin_required, global_permission_required,
    has_admin_access, has_global_permission,
)
from mielenosoitukset_fi.utils import volunteer_registry as registry
from mielenosoitukset_fi.utils.campaign_publication import campaign_pages_public, PUBLICATION_ID

from mielenosoitukset_fi.utils.admin.demonstration import collect_tags
from .utils import mongo, _ADMIN_TEMPLATE_FOLDER

from mielenosoitukset_fi.utils.classes import RecurringDemonstration

admin_kampanja_bp = Blueprint(
    "admin_kampanja", __name__, url_prefix="/admin/kampanja"
)

from .utils import AdminActParser, log_admin_action_V2
from flask_login import current_user


@admin_kampanja_bp.before_request
def log_request_info():
    """Log request information before handling it."""
    if request.endpoint == "admin_kampanja.update_publication":
        return  # The action audit below deliberately excludes session/CSRF material.
    log_admin_action_V2(
        AdminActParser().log_request_info(request.__dict__, current_user)
    )

@admin_kampanja_bp.after_request
def private_admin_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@admin_kampanja_bp.route("/")
@login_required
@permission_required("KAMPANJA")
def index():
    can_publish = has_admin_access(current_user) and has_global_permission(current_user, "KAMPANJA")
    token = None
    if can_publish:
        token = session.setdefault("campaign_publication_csrf", secrets.token_urlsafe(32))

    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}kampanja/list.html",
        sources=registry.counts(mongo),
        pages_public=campaign_pages_public(),
        can_publish=can_publish,
        publication_csrf=token,
    )

@admin_kampanja_bp.route("/publication", methods=["POST"])
@login_required
@admin_required
@global_permission_required("KAMPANJA")
def update_publication():
    """Publish or withdraw pages without enabling fundraising payments."""
    expected = session.get("campaign_publication_csrf", "")
    supplied = request.form.get("csrf_token", "")
    if not expected or not supplied or not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        abort(400)
    action = request.form.get("publication")
    if action not in {"publish", "private"}:
        abort(400)
    published = action == "publish"
    try:
        mongo.campaign_settings.update_one(
            {"_id": PUBLICATION_ID},
            {"$set": {"published": published, "updated_at": utcnow(), "updated_by": str(current_user.id)}},
            upsert=True,
        )
    except PyMongoError:
        current_app.logger.exception("Could not save campaign publication state")
        flash_message("Tallennusta ei voitu varmistaa. Tarkista sivujen näkyvyys ja yritä tarvittaessa uudelleen.", "error")
        return redirect(url_for("admin_kampanja.index"), code=303)
    log_admin_action_V2({
        "action": "campaign_publication_changed", "published": published,
        "actor": str(current_user.id), "entity_type": "campaign_settings", "entity_id": PUBLICATION_ID,
    })
    flash_message(
        "Kampanja- ja keräyssivut on julkaistu kaikille." if published
        else "Kampanja- ja keräyssivut näkyvät vain kirjautuneille.",
        "success",
    )
    return redirect(url_for("admin_kampanja.index"), code=303)


import csv, io

# --- Sign-up lists: one page for every volunteer / interest list ---

from flask import Response

CSRF_SESSION_KEY = "campaign_publication_csrf"
STATUS_FILTERS = {"", "confirmed", "pending"}


def _csrf_token():
    return session.setdefault(CSRF_SESSION_KEY, secrets.token_urlsafe(32))


def _csrf_ok(supplied):
    expected = session.get(CSRF_SESSION_KEY, "")
    return bool(expected and supplied) and hmac.compare_digest(
        expected.encode("utf-8"), str(supplied).encode("utf-8"))


def _source_or_404(key):
    source = registry.sources().get(key)
    if source is None:
        abort(404)
    return source


@admin_kampanja_bp.route("/ilmoittautumiset")
@login_required
@admin_required
@permission_required("KAMPANJA")
def signups():
    """List one sign-up list (tab) with the same columns, status and actions for all."""
    all_sources = registry.sources()
    key = request.args.get("lahde", "kampanja")
    source = all_sources.get(key) or abort(404)
    status = request.args.get("tila", "")
    if status not in STATUS_FILTERS:
        abort(400)
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}kampanja/signups.html",
        sources=registry.counts(mongo), source=source, status=status,
        rows=registry.rows(mongo, source, status), status_labels=registry.STATUS_LABELS,
        recent_actions=registry.recent_actions(mongo, source.key), csrf_token=_csrf_token(),
        min_reason=registry.MIN_REASON, max_reason=registry.MAX_REASON,
    )


@admin_kampanja_bp.route("/ilmoittautumiset/<source_key>.csv")
@login_required
@admin_required
@permission_required("KAMPANJA")
def signups_csv(source_key):
    """CSV export of one list. Personal data: every export leaves an action row."""
    source = _source_or_404(source_key)
    status = request.args.get("tila", "")
    if status not in STATUS_FILTERS:
        abort(400)
    body = registry.export_csv(mongo, source, status)
    registry.record_action(mongo, source, "export", current_user.id,
                           count=max(len(list(csv.reader(io.StringIO(body)))) - 1, 0), reason=status or "kaikki")
    return Response(body.encode("utf-8"), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment;filename=ilmoittautumiset-{source.key}.csv"})


@admin_kampanja_bp.route("/ilmoittautumiset/<source_key>/<row_id>/poista")
@login_required
@admin_required
@permission_required("KAMPANJA")
def signup_delete_confirm(source_key, row_id):
    """Confirmation page: shows what is deleted and asks for the reason."""
    source = _source_or_404(source_key)
    row = registry.get_row(mongo, source, row_id) or abort(404)
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}kampanja/signup_delete.html", source=source, row=row,
        status_labels=registry.STATUS_LABELS, csrf_token=_csrf_token(),
        min_reason=registry.MIN_REASON, max_reason=registry.MAX_REASON,
    )


@admin_kampanja_bp.route("/ilmoittautumiset/<source_key>/<row_id>/poista", methods=["POST"])
@login_required
@admin_required
@permission_required("KAMPANJA")
def signup_delete(source_key, row_id):
    """Delete one sign-up. A reason is required and recorded with who did it."""
    source = _source_or_404(source_key)
    if not _csrf_ok(request.form.get("csrf_token")):
        abort(400)
    reason = registry.clean_reason(request.form.get("reason"))
    if not reason:
        flash_message("Kirjoita poiston syy (vähintään %d merkkiä)." % registry.MIN_REASON, "error")
        return redirect(url_for("admin_kampanja.signup_delete_confirm", source_key=source.key, row_id=row_id), code=303)
    elif registry.delete_row(mongo, source, row_id, current_user.id, reason):
        flash_message("Ilmoittautuminen poistettiin. Syy on tallennettu.", "success")
    else:
        flash_message("Ilmoittautumista ei löytynyt.", "error")
    return redirect(url_for("admin_kampanja.signups", lahde=source.key), code=303)
