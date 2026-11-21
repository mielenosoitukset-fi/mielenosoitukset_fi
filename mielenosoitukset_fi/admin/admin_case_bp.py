import re
import sys
from functools import wraps
from mielenosoitukset_fi.utils.time_utils import utcnow
from datetime import datetime
import logging
from bson.objectid import ObjectId
from flask import Blueprint, jsonify, redirect, render_template, request, url_for, abort, current_app
from flask_login import current_user, login_required
from flask_babel import _

from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from mielenosoitukset_fi.utils.classes import Demonstration, Organizer
from mielenosoitukset_fi.utils.s3 import upload_image_fileobj
from mielenosoitukset_fi.utils.admin.demonstration import collect_tags
from mielenosoitukset_fi.utils.database import DEMO_FILTER, stringify_object_ids
from mielenosoitukset_fi.utils.flashing import flash_message
from mielenosoitukset_fi.utils.variables import CITY_LIST
from mielenosoitukset_fi.utils.cities import normalize_city_key
from mielenosoitukset_fi.utils import case_reasons
from mielenosoitukset_fi.utils.wrappers import admin_required, permission_required
from .utils import mongo, log_admin_action_V2, AdminActParser, _ADMIN_TEMPLATE_FOLDER
from mielenosoitukset_fi.utils.demo_cancellation import cancel_demo
from mielenosoitukset_fi.utils.case_access import (
    case_scope_query, can_access_case, global_case_permission,
)
from mielenosoitukset_fi.utils import city_admin_applications as applications
from mielenosoitukset_fi.utils import case_notifications

from mielenosoitukset_fi.emailer.EmailSender import EmailSender
from mielenosoitukset_fi.utils.logger import logger

# --- init ---
email_sender = EmailSender()

admin_case_bp = Blueprint("admin_case", __name__, url_prefix="/admin/case")

@admin_case_bp.before_request
def log_request_info():
    """Fail closed if a new protected route omits its authorization contract."""
    view = current_app.view_functions.get(request.endpoint)
    if not getattr(view, "case_permissions", None):
        abort(403)


def case_permission(permission, *, allow_city=False):
    """Declare capability, city boundary and target checks on the route itself."""
    def decorate(function):
        @wraps(function)
        def authorized(*args, **kwargs):
            required = permission.get(request.method) if isinstance(permission, dict) else permission
            if not required:
                abort(403)
            scope = case_scope_query(current_user, required)
            if scope is None or (scope and not allow_city):
                abort(403)
            case_id = kwargs.get('case_id')
            if case_id:
                oid = _safe_objectid(case_id)
                if not oid:
                    abort(404)
                if mongo.cases.find_one({'$and': [scope, {'_id': oid}]}, {'_id': 1}) is None:
                    abort(403)
            log_admin_action_V2(AdminActParser().log_request_info(request.__dict__, current_user))
            return function(*args, **kwargs)
        authorized.case_permissions = permission
        return authorized
    return decorate


from mielenosoitukset_fi.utils.classes import Case


def _safe_objectid(value):
    try:
        return ObjectId(value) if value else None
    except Exception:
        return None


def _demo_is_approved(demo_doc):
    return bool((demo_doc or {}).get("approved") or (demo_doc or {}).get("accepted"))


def _demo_status_payload(demo_doc):
    demo_doc = demo_doc or {}
    approved = _demo_is_approved(demo_doc)
    rejected = bool(demo_doc.get("rejected"))
    cancelled = bool(demo_doc.get("cancelled"))

    if cancelled:
        return {"key": "cancelled", "label": _("Peruttu"), "tone": "warning"}
    if approved:
        return {"key": "approved", "label": _("Hyväksytty"), "tone": "success"}
    if rejected:
        return {"key": "rejected", "label": _("Hylätty"), "tone": "danger"}
    return {"key": "pending", "label": _("Odottaa käsittelyä"), "tone": "info"}


def _case_type_label(case_type):
    labels = {
        "new_demo": _("Uusi mielenosoitus"),
        "demo_error_report": _("Virheilmoitus"),
        "demo_edit_suggestion": _("Muutosehdotus"),
        "organization_edit_suggestion": _("Organisaation muutospyyntö"),
        "demo_cancellation_request": _("Peruutuspyyntö"),
        "demo_cancelled": _("Peruttu mielenosoitus"),
        "support_ticket": _("Tukipyyntö"),
        "city_admin_application": _("Kaupunkiadminhakemus"),
        "account_deletion_request": _("Tilin poistopyyntö"),
    }
    return labels.get(case_type, str(case_type).replace("_", " ").capitalize())


def _push_case_update(case_id, *, set_fields=None, history_entry=None, action_log=None):
    update_doc = {}
    if set_fields:
        update_doc["$set"] = set_fields
    if history_entry or action_log:
        update_doc["$push"] = {}
        if history_entry:
            update_doc["$push"]["case_history"] = history_entry
        if action_log:
            update_doc["$push"]["action_logs"] = action_log
    if update_doc:
        mongo.cases.update_one({"_id": _safe_objectid(case_id)}, update_doc)


def _can_take_down(demo):
    from mielenosoitukset_fi.utils.demo_takedown import can_take_down_demo

    return bool(demo) and can_take_down_demo(current_user, demo)


def _take_down_second_admin():
    from mielenosoitukset_fi.utils.demo_takedown import confirm_hours, second_admin_required

    return {"hours": confirm_hours()} if second_admin_required() else None


def _take_down_review(demo):
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from mielenosoitukset_fi.utils.demo_takedown import REVIEW_FIELD, review_view

    if not demo or not demo.get("_id"):
        return None
    raw = mongo.demonstrations.find_one({"_id": ObjectId(str(demo["_id"]))}, {REVIEW_FIELD: 1})
    view = review_view(raw, current_user)
    if view:
        helsinki = ZoneInfo("Europe/Helsinki")
        for key in ("requested_at", "confirm_by"):
            if view.get(key):
                view[key + "_label"] = (
                    view[key].replace(tzinfo=timezone.utc).astimezone(helsinki).strftime("%d.%m.%Y %H:%M")
                )
    return view


def _undo_deadline(demo):
    """Deadline for the case's "Peru hyväksyntä" button, for approvers only."""
    from mielenosoitukset_fi.demonstrations.decisions import approval_undo_deadline
    from mielenosoitukset_fi.utils.wrappers import has_demo_approval_permission

    if not demo or not demo.get("_id") or not has_demo_approval_permission(current_user, demo):
        return None
    raw = mongo.demonstrations.find_one(
        {"_id": ObjectId(str(demo["_id"]))}, {"approved": 1, "moderation_decision": 1}
    )
    deadline = approval_undo_deadline(raw)
    if deadline is None:
        return None
    from datetime import timezone
    from zoneinfo import ZoneInfo

    # Stored times are naive UTC; admins read Helsinki time.
    return deadline.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Helsinki")).strftime("%H:%M")


def _stringify_demo(demo):
    if not demo:
        return {}
    payload = stringify_object_ids(demo)
    payload["status"] = _demo_status_payload(payload)
    payload["approved"] = _demo_is_approved(payload)
    return payload


def _suggestion_for_case(case_obj, projection=None):
    """The demo suggestion behind a demo_edit_suggestion case (same _id)."""
    suggestion_id = (case_obj.meta or {}).get("suggestion_id") or case_obj._id
    return mongo.demo_suggestions.find_one({"_id": _safe_objectid(suggestion_id)}, projection)


def _can_edit_suggested_demo(demo_doc):
    """Suggestion details (reporter e-mail, comment) follow demo edit rights.

    Before suggestions became cases only EDIT_DEMO holders saw them, so an
    admin with case rights but no edit right for the demo's city sees the
    case without those details.
    """
    if current_user.has_full_permissions() or "EDIT_DEMO" in (current_user.global_permissions or []):
        return True
    if not demo_doc:
        return False
    city_key = demo_doc.get("city_key") or normalize_city_key(demo_doc.get("city") or "")
    return bool(city_key) and city_key in current_user.scoped_city_keys_for("EDIT_DEMO")


def _hide_suggestion_reporter(case_obj):
    case_obj.submitter = {}


def _build_case_row(case_obj, demo_doc=None, org_doc=None):
    submitter = case_obj.submitter or {}
    urgency = (case_obj.meta or {}).get("urgency")
    closed = bool((case_obj.meta or {}).get("closed"))
    escalated = bool((case_obj.meta or {}).get("superior_needed"))

    # Support tickets: map the URGENT keyword flag to the high-urgency badge
    # so the "Kriittiset" filter catches them, not just the escalation chip.
    if case_obj.case_type == "support_ticket" and (case_obj.meta or {}).get("urgent"):
        urgency = urgency or "high"

    if case_obj.case_type == "new_demo":
        title = (demo_doc or {}).get("title") or _("Uusi mielenosoitus")
        description = (demo_doc or {}).get("address") or _("Ei lisätietoja")
        meta_line = " · ".join(part for part in [(demo_doc or {}).get("city"), (demo_doc or {}).get("date")] if part)
    elif case_obj.case_type in {"demo_cancellation_request", "demo_cancelled"}:
        title = _("Peruutuspyyntö")
        description = (demo_doc or {}).get("title") or _("Liittyvää mielenosoitusta ei löytynyt")
        meta_line = " · ".join(
            part
            for part in [
                (demo_doc or {}).get("city"),
                (demo_doc or {}).get("date"),
                (case_obj.meta or {}).get("source"),
            ]
            if part
        )
    elif case_obj.case_type == "demo_error_report":
        title = _("Virheilmoitus")
        description = (case_obj.error_report or {}).get("error") or _("Ei lisätietoja")
        meta_line = (demo_doc or {}).get("title") or _("Demoa ei löytynyt")
    elif case_obj.case_type == "demo_edit_suggestion":
        title = (demo_doc or {}).get("title") or _("Liittyvää mielenosoitusta ei löytynyt")
        fields = (case_obj.meta or {}).get("fields") or []
        description = (
            _("Ehdotetut kentät") + ": " + ", ".join(fields) if fields else _("Ei kenttämuutoksia")
        )
        meta_line = " · ".join(part for part in [(demo_doc or {}).get("city"), (demo_doc or {}).get("date")] if part)
    elif case_obj.case_type == "organization_edit_suggestion":
        title = (org_doc or {}).get("name") or _("Organisaation muutospyyntö")
        description = _("Organisaation tiedoista ehdotetaan muutoksia.")
        meta_line = (submitter.get("submitter_name") or submitter.get("email") or _("Ei ilmoittajaa"))
    elif case_obj.case_type == "support_ticket":
        ticket_subject = (case_obj.suggestion or {}).get("subject") or (case_obj.meta or {}).get("ticket", {}).get("subject") or _("Tukipyyntö")
        description = (case_obj.suggestion or {}).get("message") or _("Ei viestiä")
        meta_line = submitter.get("submitter_email") or submitter.get("email") or _("Ei ilmoittajaa")
        title = _("Tukipyyntö: {subject}").format(subject=ticket_subject[:100]) if ticket_subject else _("Tukipyyntö")
    elif case_obj.case_type == "city_admin_application":
        title = _("Kaupunkiadminhakemus")
        description = ", ".join(applications.city_names((case_obj.meta or {}).get("city_keys")))
        meta_line = submitter.get("submitter_name") or ""
    elif case_obj.case_type == "account_deletion_request":
        title = _("Tilin poistopyyntö")
        description = ((case_obj.meta or {}).get("reason") or _("Ei perustelua"))[:200]
        meta_line = ""
        submitter = {"submitter_name": _account_owner_label(case_obj.submitter_id)}
    else:
        title = _case_type_label(case_obj.case_type)
        description = _("Ei lisätietoja")
        meta_line = ""

    return {
        "id": str(case_obj._id),
        "running_num": case_obj.running_num,
        "type_label": _case_type_label(case_obj.case_type),
        "title": title,
        "description": description,
        "meta_line": meta_line,
        "created_at": case_obj.created_at,
        "submitter_label": submitter.get("submitter_name") or submitter.get("submitter_email") or submitter.get("email") or _("Ei tiedossa"),
        "urgency": urgency,
        "closed": closed,
        "escalated": escalated,
        "status_label": _("Suljettu") if closed else _("Avoin"),
    }


def _account_owner_label(submitter_id):
    """Name the account behind a self-service request; it has no submitter record."""
    user = mongo.users.find_one({"_id": _safe_objectid(submitter_id)}, {"username": 1}) if submitter_id else None
    return (user or {}).get("username") or _("Tilin omistaja")

CASE_LIST_PAGE_SIZE = 50
CASE_LIST_STATES = ("open", "closed", "escalated", "high", "all")
_OPEN = {"meta.closed": {"$ne": True}}
_HIGH = {"$or": [{"meta.urgency": "high"}, {"type": "support_ticket", "meta.urgent": True}]}


def _case_state_clause(state):
    return {
        "open": _OPEN,
        "closed": {"meta.closed": True},
        "escalated": {"meta.superior_needed": True},
        "high": _HIGH,
    }.get(state, {})


def _case_search_clause(text):
    """Match case number, reporter, ticket text, or the linked demo/organisation name."""
    pattern = {"$regex": re.escape(text), "$options": "i"}
    clauses = [
        {"suggestion.subject": pattern},
        {"error_report.error": pattern},
    ]
    if text.lstrip("#").isdigit():
        clauses.append({"running_num": int(text.lstrip("#"))})
    reporter = [{"submitter.submitter_name": pattern}, {"submitter.submitter_email": pattern}, {"submitter.email": pattern}]
    if current_user.has_full_permissions() or "EDIT_DEMO" in (current_user.global_permissions or []):
        clauses.extend(reporter)
    else:
        # Suggestion reporters are hidden from admins without demo edit rights,
        # so searching by them must not reveal which case they are behind.
        clauses.append({"type": {"$ne": "demo_edit_suggestion"}, "$or": reporter})
    demo_ids = mongo.demonstrations.distinct("_id", {"title": pattern})[:500]
    if demo_ids:
        clauses.append({"demo_id": {"$in": demo_ids + [str(value) for value in demo_ids]}})
    org_ids = mongo.organizations.distinct("_id", {"name": pattern})[:200]
    if org_ids:
        clauses.append({"organization_id": {"$in": org_ids + [str(value) for value in org_ids]}})
    return {"$or": clauses}


def _and(*clauses):
    parts = [clause for clause in clauses if clause]
    if not parts:
        return {}
    return parts[0] if len(parts) == 1 else {"$and": parts}


from mielenosoitukset_fi.utils import case_escalation as _esc  # noqa: E402


@admin_case_bp.route("/")
@login_required
@case_permission("LIST_CASES", allow_city=True)
def cases():
    """List cases: open ones first by default, with search, type filter and pages.

    Reading the list never changes cases. Cases whose subject was handled
    elsewhere are closed by the auto_close_cases background job.
    """
    scope = case_scope_query(current_user, "LIST_CASES")
    state = request.args.get("tila", "open")
    if state not in CASE_LIST_STATES:
        state = "open"
    case_type = request.args.get("tyyppi", "")
    query_text = (request.args.get("q") or "").strip()[:100]
    try:
        page = max(1, int(request.args.get("sivu", 1)))
    except (TypeError, ValueError):
        page = 1

    counts = {key: mongo.cases.count_documents(_and(scope, _case_state_clause(key))) for key in ("open", "closed", "escalated")}
    counts["all"] = counts["open"] + counts["closed"]
    type_keys = sorted(key for key in mongo.cases.distinct("type", scope) if key)
    type_options = [(key, _case_type_label(key)) for key in type_keys]
    if case_type not in type_keys:
        case_type = ""

    query = _and(
        scope,
        _case_state_clause(state),
        {"type": case_type} if case_type else None,
        _case_search_clause(query_text) if query_text else None,
    )
    total = mongo.cases.count_documents(query)
    pages = max(1, -(-total // CASE_LIST_PAGE_SIZE))
    page = min(page, pages)

    case_rows = []
    cursor = (
        mongo.cases.find(query, sort=[("created_at", -1), ("_id", -1)])
        .skip((page - 1) * CASE_LIST_PAGE_SIZE)
        .limit(CASE_LIST_PAGE_SIZE)
    )
    for doc in cursor:
        case = Case.from_dict(doc)
        demo_doc = None
        org_doc = None
        if case.case_type in {"new_demo", "demo_cancellation_request", "demo_cancelled", "demo_error_report", "demo_edit_suggestion"} and case.demo_id:
            demo_doc = mongo.demonstrations.find_one(
                {"_id": _safe_objectid(case.demo_id)},
                {"approved": 1, "accepted": 1, "rejected": 1, "cancelled": 1, "title": 1, "date": 1, "city": 1, "city_key": 1, "address": 1},
            )
        if case.case_type == "organization_edit_suggestion" and case.organization_id:
            org_doc = mongo.organizations.find_one({"_id": _safe_objectid(case.organization_id)}, {"name": 1, "updated_at": 1})
        if case.case_type == "demo_edit_suggestion" and not _can_edit_suggested_demo(demo_doc):
            _hide_suggestion_reporter(case)
        case_rows.append(_build_case_row(case, demo_doc=demo_doc, org_doc=org_doc))

    def page_url(number):
        args = {"tila": state, "tyyppi": case_type or None, "q": query_text or None, "sivu": number if number > 1 else None}
        return url_for("admin_case.cases", **{key: value for key, value in args.items() if value})

    always_emailed = global_case_permission(current_user, "VIEW_CASE")
    user_doc = mongo.users.find_one({"_id": _safe_objectid(current_user.id)}, {"case_notice_emails": 1}) or {}
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}cases/all.html",
        case_rows=case_rows,
        case_email_always=always_emailed,
        case_emails_enabled=case_notifications.is_enabled() and case_notifications.emails_enabled(),
        case_email_opt_in=user_doc.get("case_notice_emails") is True,
        counts=counts,
        state=state,
        case_type=case_type,
        type_options=type_options,
        query_text=query_text,
        total=total,
        page=page,
        pages=pages,
        prev_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < pages else None,
        state_url=lambda key: url_for("admin_case.cases", tila=key, tyyppi=case_type or None, q=query_text or None),
    )

@admin_case_bp.route("/email_notices/", methods=["POST"])
@login_required
@case_permission("LIST_CASES", allow_city=True)
def email_notices():
    """Let an admin with city-scoped case rights opt in or out of case e-mails.

    Admins with case rights everywhere always get them (maintainer decision
    20 Nov 2026), so the choice only matters for city-scoped admins.
    """
    enabled = request.form.get("enabled") == "1"
    mongo.users.update_one({"_id": _safe_objectid(current_user.id)}, {"$set": {"case_notice_emails": enabled}})
    flash_message(
        _("Saat jatkossa sähköpostin uusista tapauksista.") if enabled
        else _("Et saa enää sähköpostia uusista tapauksista."),
        "success",
    )
    return redirect(url_for("admin_case.cases"))


@admin_case_bp.route("/demo_search/", methods=["GET"])
@login_required
@admin_required
@case_permission("VIEW_CASE")
def demo_search():
    """Simple AJAX lookup for linking a support ticket to an existing demo."""
    q = (request.args.get("q", "") or "").strip()
    if not q:
        return jsonify([])
    demos = list(
        mongo.demonstrations.find(
            {"title": {"$regex": re.escape(q), "$options": "i"}},
            {"title": 1, "city": 1, "cancelled": 1},
        ).sort("title", 1).limit(10)
    )
    return jsonify([
        {
            "id": str(d.get("_id")),
            "title": d.get("title", ""),
            "city": d.get("city", ""),
            "cancelled": bool(d.get("cancelled")),
        }
        for d in demos
    ])


@admin_case_bp.route("/org_search/", methods=["GET"])
@login_required
@admin_required
@case_permission("VIEW_CASE")
def org_search():
    """Simple AJAX lookup for linking a support ticket to an organization."""
    q = (request.args.get("q", "") or "").strip()
    if not q:
        return jsonify([])
    orgs = list(
        mongo.organizations.find(
            {"name": {"$regex": re.escape(q), "$options": "i"}},
            {"name": 1},
        ).sort("name", 1).limit(10)
    )
    return jsonify([
        {"id": str(o.get("_id")), "name": o.get("name", "")}
        for o in orgs
    ])


@admin_case_bp.route("/blocklist/", methods=["GET", "POST"])
@login_required
@admin_required
@case_permission({"GET": "VIEW_CASE", "HEAD": "VIEW_CASE", "POST": "EDIT_CASE"})
def blocklist():
    """Manage the support-ticket sender blocklist.

    Supports exact addresses (``user@example.com``) and whole domains with
    their subdomains (``example.com`` or ``*@example.com``).
    """
    if request.method == "POST":
        pattern = (request.form.get("pattern", "") or "").strip().lower()
        if pattern:
            existing = mongo.support_ticket_blocklist.find_one({"pattern": pattern})
            if not existing:
                mongo.support_ticket_blocklist.insert_one(
                    {
                        "pattern": pattern,
                        "added_by": current_user.username,
                        "added_at": utcnow(),
                    }
                )
                log_admin_action_V2(
                    f"{current_user.username} lisäsi tukilippujen estolistalle: {pattern}",
                    None,
                )
                flash_message(_("Esto lisätty: %(pattern)s") % {"pattern": pattern}, "success")
            else:
                flash_message(_("Esto on jo listalla."), "warning")
        else:
            flash_message(_("Anna sähköpostiosoite tai verkkotunnus."), "warning")
        return redirect(url_for("admin_case.blocklist"))

    blocked = list(
        mongo.support_ticket_blocklist.find({}).sort("added_at", -1)
    )
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}cases/blocklist.html",
        blocked=blocked,
        case_type_label=_("Tukilippujen estolista"),
    )


@admin_case_bp.route("/blocklist/<block_id>/remove", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def blocklist_remove(block_id):
    if not ObjectId.is_valid(block_id):
        abort(404)
    doc = mongo.support_ticket_blocklist.find_one({"_id": ObjectId(block_id)})
    if doc:
        mongo.support_ticket_blocklist.delete_one({"_id": ObjectId(block_id)})
        log_admin_action_V2(
            f"{current_user.username} poisti tukilippujen estolistalta: {doc.get('pattern')}",
            None,
        )
        flash_message(_("Esto poistettu: %(pattern)s") % {"pattern": doc.get("pattern")}, "success")
    return redirect(url_for("admin_case.blocklist"))


@admin_case_bp.route("/<case_id>/")
@login_required
@case_permission("VIEW_CASE", allow_city=True)
def single_case(case_id):
    case = Case.get(case_id)
    if not case:
        abort(404)

    case_data = {
        "type_label": _case_type_label(case.case_type),
        "submitter_label": (case.submitter or {}).get("submitter_name")
        or (case.submitter or {}).get("submitter_email")
        or (case.submitter or {}).get("email")
        or _("Ei tiedossa"),
    }
    if case.case_type == "account_deletion_request":
        case_data["submitter_label"] = _account_owner_label(case.submitter_id)

    # Handle different case types
    if case.case_type == "new_demo" and case.demo_id:
        demo = mongo.demonstrations.find_one({"_id": _safe_objectid(case.demo_id)})
        submitter = mongo.submitters.find_one({"demonstration_id": _safe_objectid(case.demo_id)})
        case_data["demo"] = _stringify_demo(demo)
        case_data["submitter"] = stringify_object_ids(submitter) if submitter else {}

    elif case.case_type == "demo_error_report" and case.demo_id:
        demo = mongo.demonstrations.find_one({"_id": _safe_objectid(case.demo_id)})
        case_data["demo"] = _stringify_demo(demo)
        case_data["error_report"] = stringify_object_ids(case.error_report or {})

    elif case.case_type == "demo_edit_suggestion":
        demo = mongo.demonstrations.find_one({"_id": _safe_objectid(case.demo_id)}) if case.demo_id else None
        case_data["demo"] = _stringify_demo(demo)
        suggestion = _suggestion_for_case(case) or {}
        status = suggestion.get("status") or "new"
        can_edit_demo = _can_edit_suggested_demo(demo)
        if not can_edit_demo:
            _hide_suggestion_reporter(case)
            case_data["submitter_label"] = _("Ei tiedossa")
        case_data["demo_suggestion"] = {
            "can_edit_demo": can_edit_demo,
            "found": bool(suggestion),
            "id": str(suggestion.get("_id") or ""),
            "fields": sorted((suggestion.get("suggested_fields") or {}).keys()),
            "comment": (suggestion.get("reporter_comment") or "") if can_edit_demo else "",
            "reporter_email": (suggestion.get("reporter_email") or "") if can_edit_demo else "",
            "created_at": suggestion.get("created_at"),
            "status": status,
            "status_label": {
                "applied": _("Hyväksytty"),
                "rejected": _("Hylätty"),
                "closed": _("Suljettu"),
            }.get(status, _("Odottaa käsittelyä")),
        }

    elif case.case_type == "organization_edit_suggestion":
        from mielenosoitukset_fi.admin.admin_org_bp import _can_edit_organization
        from mielenosoitukset_fi.utils.content_formatting import safe_href
        from mielenosoitukset_fi.utils import org_suggestion_cases

        suggestion = org_suggestion_cases.suggestion_for_case(mongo, case) or {}
        org_id = case.organization_id or suggestion.get("organization_id")
        org = mongo.organizations.find_one({"_id": _safe_objectid(org_id)}) if org_id else None
        labels = {"name": _("Nimi"), "description": _("Kuvaus"), "website": _("Verkkosivusto"),
                  "email": _("Sähköposti"), "logo": _("Logo"), "social_media_links": _("Sosiaalinen media")}

        def _shown(value):
            if isinstance(value, dict):
                return ", ".join(f"{key}: {safe_href(url) or '-'}" for key, url in value.items())
            return value

        changes = []
        for key, new_val in (suggestion.get("fields") or {}).items():
            old_val = (org or {}).get(key)
            if new_val != old_val:
                changes.append({"field": labels.get(key, key), "old": _shown(old_val), "new": _shown(new_val)})
        state = (suggestion.get("status") or {}).get("state") or "pending"
        case_data["organization"] = stringify_object_ids(org) if org else {}
        case_data["changes"] = changes
        case_data["org_suggestion"] = {
            "found": bool(suggestion),
            "id": str(suggestion.get("_id") or ""),
            "org_id": str(org_id or ""),
            "state": state,
            "state_label": {
                "applied": _("Hyväksytty"),
                "partially_applied": _("Osittain hyväksytty"),
                "rejected": _("Hylätty"),
                "in_review": _("Käsittelyssä"),
            }.get(state, _("Odottaa käsittelyä")),
            "decided": state in org_suggestion_cases.DECIDED_STATES,
            "can_review": bool(org) and _can_edit_organization(current_user, org),
        }

    elif case.case_type in {"demo_cancellation_request", "demo_cancelled"} and case.demo_id:
        demo = mongo.demonstrations.find_one({"_id": _safe_objectid(case.demo_id)})
        case_data["demo"] = _stringify_demo(demo)
        case_data["cancellation"] = {
            "reason": (case.meta or {}).get("reason")
            or (demo or {}).get("cancellation_reason"),
            "requested_at": (demo or {}).get("cancellation_requested_at"),
            "requested_by": (demo or {}).get("cancellation_requested_by")
            or (case.meta or {}).get("requested_by"),
            "source": (demo or {}).get("cancellation_request_source")
            or (case.meta or {}).get("source"),
            "cancelled": bool((demo or {}).get("cancelled")),
            "cancelled_at": (demo or {}).get("cancelled_at"),
        }

    elif case.case_type == "support_ticket":
        suggestion = case.suggestion or {}
        ticket_meta = (case.meta or {}).get("ticket", {})
        messages = suggestion.get("messages") or []
        case_data["support_ticket"] = {
            "subject": ticket_meta.get("subject") or suggestion.get("subject") or _("(ei aihetta)"),
            "message": suggestion.get("message") or _("(ei viestiä)"),
            "html_message": suggestion.get("html_message") or "",
            "received_at": case.created_at,
            "source": ticket_meta.get("source", "email"),
            "message_id": ticket_meta.get("message_id"),
            "from_wrapper": ticket_meta.get("from_wrapper", False),
            "submitter_email": (case.submitter or {}).get("submitter_email"),
            "followups": [message for message in messages if message.get("direction") != "out"],
            "outgoing_replies": [message for message in reversed(messages) if message.get("direction") == "out"],
        }
        case_data["urgent"] = bool((case.meta or {}).get("urgent"))
        # Linked objects: when an admin acts on the request (e.g. creates a
        # demo), the created entity is linked back to the ticket.
        if case.demo_id:
            demo = mongo.demonstrations.find_one({"_id": _safe_objectid(case.demo_id)})
            case_data["demo"] = _stringify_demo(demo)
        if case.organization_id:
            org = mongo.organizations.find_one({"_id": _safe_objectid(case.organization_id)})
            case_data["organization"] = stringify_object_ids(org) if org else {}

    elif case.case_type == applications.CASE_TYPE:
        application = mongo.city_admin_applications.find_one({"_id": _safe_objectid((case.meta or {}).get("application_id"))})
        if application:
            activism = application.get("activism") or {}
            case_data["application"] = {
                "status": application.get("status"),
                "status_label": applications.STATUS_LABELS.get(application.get("status"), application.get("status")),
                "cities": applications.city_names(application.get("city_keys")),
                "about": application.get("about") or "",
                "active_cities": applications.city_names(activism.get("cities")),
                "organizations": activism.get("organizations") or "",
                "experience": applications.EXPERIENCE_LABELS.get(activism.get("experience"), "-"),
                "experience_details": activism.get("experience_details") or "",
                "references": application.get("references") or "",
                "created_at": application.get("created_at"),
                "user_id": str(application.get("user_id") or ""),
                "decision_statuses": [(key, applications.STATUS_LABELS[key]) for key in applications.DECISION_STATUSES],
            }

    notice_doc = mongo.cases.find_one({"_id": case._id}, {"notified_users": 1, "notified_at": 1}) or {}
    case_data["notified_users"] = notice_doc.get("notified_users") or []
    case_data["notified_at"] = notice_doc.get("notified_at")

    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}cases/case.html",
        case=case,
        case_data=case_data,
        can_edit_case=can_access_case(current_user, "EDIT_CASE", case._id),
        global_case_controls=global_case_permission(current_user, "EDIT_CASE"),
        escalation_level=_esc.current_level(case.meta),
        escalation_name=_esc.LEVEL_NAMES[_esc.current_level(case.meta)],
        escalate_label=_esc.ask_label(mongo, case.meta),
        can_escalate_case=_esc.can_escalate(case.meta, current_user),
        can_take_down=_can_take_down(case_data.get("demo")),
        undo_deadline=_undo_deadline(case_data.get("demo")),
        take_down_review=_take_down_review(case_data.get("demo")),
        take_down_second_admin=_take_down_second_admin(),
        reject_reasons=case_reasons.choices(case_reasons.REJECT_KEYS),
        close_reasons=case_reasons.choices(case_reasons.CLOSE_KEYS),
    )


# --- Add internal note/action ---
@admin_case_bp.route("/<case_id>/add_action/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE", allow_city=True)
def add_action(case_id):
    case = Case.get(case_id)
    if not case:
        abort(404)

    action_type = request.form.get("action_type")
    reason = request.form.get("reason", "").strip()

    if not action_type or not reason:
        flash_message(_("Täytä kaikki kentät."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    # Append action log to the case
    action_entry = {
        "user": current_user.username,
        "action_type": action_type,
        "reason": reason,
        "timestamp": utcnow()
    }

    mongo.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$push": {"action_logs": action_entry}}
    )

    log_admin_action_V2(f"{current_user.username} lisäsi toimenpiteen: {action_type} ({reason})", case_id)
    flash_message(_("Merkintä lisätty!"), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


# --- Link ticket to action (demo / organization) ---
@admin_case_bp.route("/<case_id>/link_demo/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE", allow_city=True)
def link_demo(case_id):
    case = Case.get(case_id)
    if not case:
        abort(404)

    demo_id = request.form.get("demo_id", "").strip()
    if not demo_id or not ObjectId.is_valid(demo_id):
        flash_message(_("Valitse tai kirjoita kelvollinen mielenosoituksen tunniste."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    demo = mongo.demonstrations.find_one({"_id": ObjectId(demo_id)}, {"title": 1})
    if not demo:
        flash_message(_("Mielenosoitusta ei löytynyt annetulla tunnisteella."), "error")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    if not global_case_permission(current_user, "EDIT_CASE"):
        scoped_demo_ids = case_scope_query(current_user, "EDIT_CASE")["$or"][0]["demo_id"]["$in"]
        if ObjectId(demo_id) not in scoped_demo_ids:
            abort(403)

    case.demo_id = ObjectId(demo_id)
    case._touch()
    case._add_history_entry({
        "timestamp": utcnow(),
        "action": "Linked to demo",
        "user": current_user.username,
        "mech_action": "link_demo",
        "metadata": {"demo_id": demo_id, "demo_title": demo.get("title")},
    })
    case.add_action("link_demo", current_user.username,
                    note=_("Liitetty mielenosoitukseen: %(title)s") % {"title": demo.get("title")})

    log_admin_action_V2(f"{current_user.username} liitti tapauksen {case.running_num} mielenosoitukseen: {demo.get('title')} ({demo_id})", case_id)
    flash_message(_("Mielenosoitus liitetty tukipyyntöön."), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/unlink_demo/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def unlink_demo(case_id):
    case = Case.get(case_id)
    if not case:
        abort(404)

    removed_id = case.demo_id
    case.demo_id = None
    case._touch()
    case._add_history_entry({
        "timestamp": utcnow(),
        "action": "Unlinked demo",
        "user": current_user.username,
        "mech_action": "unlink_demo",
        "metadata": {"demo_id": str(removed_id) if removed_id else None},
    })
    case.add_action("unlink_demo", current_user.username, note=_("Poistettu mielenosoituslinkki."))

    log_admin_action_V2(f"{current_user.username} poisti mielenosoituslinkin tapauksesta {case.running_num}", case_id)
    flash_message(_("Mielenosoituslinkki poistettu."), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/link_org/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def link_org(case_id):
    case = Case.get(case_id)
    if not case:
        abort(404)

    org_id = request.form.get("organization_id", "").strip()
    if not org_id or not ObjectId.is_valid(org_id):
        flash_message(_("Valitse tai kirjoita kelvollinen organisaation tunniste."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    org = mongo.organizations.find_one({"_id": ObjectId(org_id)}, {"name": 1})
    if not org:
        flash_message(_("Organisaatiota ei löytynyt annetulla tunnisteella."), "error")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    case.organization_id = ObjectId(org_id)
    case._touch()
    case._add_history_entry({
        "timestamp": utcnow(),
        "action": "Linked to organization",
        "user": current_user.username,
        "mech_action": "link_org",
        "metadata": {"organization_id": org_id, "organization_name": org.get("name")},
    })
    case.add_action("link_org", current_user.username,
                    note=_("Liitetty organisaatioon: %(name)s") % {"name": org.get("name")})

    log_admin_action_V2(f"{current_user.username} liitti tapauksen {case.running_num} organisaatioon: {org.get('name')} ({org_id})", case_id)
    flash_message(_("Organisaatio liitetty tukipyyntöön."), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/unlink_org/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def unlink_org(case_id):
    case = Case.get(case_id)
    if not case:
        abort(404)

    removed_id = case.organization_id
    case.organization_id = None
    case._touch()
    case._add_history_entry({
        "timestamp": utcnow(),
        "action": "Unlinked organization",
        "user": current_user.username,
        "mech_action": "unlink_org",
        "metadata": {"organization_id": str(removed_id) if removed_id else None},
    })
    case.add_action("unlink_org", current_user.username, note=_("Poistettu organisaatiolinkki."))

    log_admin_action_V2(f"{current_user.username} poisti organisaatiolinkin tapauksesta {case.running_num}", case_id)
    flash_message(_("Organisaatiolinkki poistettu."), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


from flask import jsonify, request, abort
from flask_login import login_required, current_user
from bson import ObjectId
from datetime import datetime

@admin_case_bp.route("/<case_id>/reply/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE", allow_city=True)
def reply_to_ticket(case_id):
    """Send an admin reply to a support ticket's submitter via email."""
    case = Case.get(case_id)
    if not case or case.case_type != "support_ticket":
        abort(404)

    from mielenosoitukset_fi.scripts.process_support_tickets import queue_admin_reply

    message = (request.form.get("reply_message") or "").strip()
    if not message:
        flash_message(_("Kirjoita vastaus ennen lähettämistä."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    submitter = (case.submitter or {}).get("submitter_email") or ""
    if not submitter:
        flash_message(_("Tukipyynnöllä ei ole lähettäjän sähköpostiosoitetta."), "error")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    case_doc = mongo.cases.find_one({"_id": ObjectId(case_id)})
    if not case_doc:
        abort(404)

    admin_label = getattr(current_user, "username", None) or str(current_user)

    try:
        queue_admin_reply(
            email_sender,
            current_app.config,
            case_doc,
            reply_to=submitter,
            message=message,
            admin_label=admin_label,
            require_acknowledged=True,
        )
    except Exception:
        logger.exception("Failed to queue support ticket reply for case %s", case_id)
        flash_message(_("Vastauksen jonotusta tai kirjausta ei voitu vahvistaa. Älä lähetä vastausta uudelleen ennen kuin jonon ja tukipyynnön tila on tarkistettu."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    log_admin_action_V2(f"{current_user.username} lisäsi vastauksen tukipyynnön {case.running_num} lähetysjonoon ({submitter})", case_id)
    flash_message(_("Vastaus lisätty lähetysjonoon. Toimitusta ei ole vielä vahvistettu."), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/application_status/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE", allow_city=True)
def set_application_status(case_id):
    """Record a decision. Local city admins can comment but only global admins decide."""
    if not global_case_permission(current_user, "EDIT_CASE"):
        abort(403)
    case = Case.get(case_id)
    if not case or case.case_type != applications.CASE_TYPE:
        abort(404)
    status = request.form.get("status", "")
    if status not in applications.DECISION_STATUSES:
        flash_message(_("Tuntematon tila."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))
    if not applications.set_status(mongo, case._id, status, current_user.username):
        flash_message(_("Hakemusta ei löytynyt tai se on peruttu."), "error")
        return redirect(url_for("admin_case.single_case", case_id=case_id))
    log_admin_action_V2(f"{current_user.username} asetti kaupunkiadminhakemuksen tilaksi {status}", case_id)
    flash_message(_("Hakemuksen tila päivitetty."), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/close/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE", allow_city=True)
def close_case(case_id):
    case = Case.get(case_id)
    if not case:
        return jsonify({"success": False, "error": "Tapausta ei löydy."}), 404

    if case.meta.get("closed", False):
        return jsonify({"success": False, "error": "Tapaus on jo suljettu."}), 400
    try:
        metadata, schema = case_reasons.parse_reason(
            request.get_json(silent=True), case_reasons.CLOSE_KEYS, mongo, case_id=case._id,
            can_view_case=lambda other_id: can_access_case(current_user, "VIEW_CASE", other_id),
        )
    except case_reasons.ReasonError as error:
        return jsonify({"success": False, "error": str(error)}), 400

    case._add_history_entry({
        "timestamp": utcnow(),
        "action": "Tapaus suljettu",
        "user": current_user.username,
        "mech_action": "close_case",
        "metadata": {**metadata, "permitted_by": current_user.username},
        "meta_schema": {**schema, "permitted_by": "string"},
    })

    mongo.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$set": {"meta.closed": True, "meta.closed_reason": metadata["reason"], "updated_at": utcnow()}}
    )

    flash_message(_("Tapaus suljettu."), "success")

    return jsonify({"success": True, "message": "Tapaus suljettu onnistuneesti."})


@admin_case_bp.route("/<case_id>/reopen/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE", allow_city=True)
def reopen_case(case_id):
    case = Case.get(case_id)
    if not case:
        return jsonify({"success": False, "error": "Tapausta ei löydy."}), 404

    reason = (request.json.get("reason") or "").strip()
    if not case.meta.get("closed", False):
        return jsonify({"success": False, "error": "Tapaus ei ole suljettu."}), 400
    if not reason:
        return jsonify({"success": False, "error": "Syytä ei annettu."}), 400

    case._add_history_entry({
        "timestamp": utcnow(),
        "action": "Tapaus avattu uudelleen",
        "user": current_user.username,
        "mech_action": "reopen_case",
        "metadata": {
            "reason": reason,
            "permitted_by": current_user.username
        },
        "meta_schema": {"reason": "string", "permitted_by": "string"}
    })

    mongo.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$set": {"meta.closed": False, "updated_at": utcnow()}, "$unset": {"meta.closed_reason": ""}}
    )

    flash_message(_("Tapaus avattu uudelleen."), "success")

    return jsonify({"success": True, "message": "Tapaus avattu uudelleen onnistuneesti."})



# --- Update demo info ---
@admin_case_bp.route("/<case_id>/update_demo/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def update_demo(case_id):
    case = Case.get(case_id)
    if not case or case.case_type != "new_demo" or not case.demo_id:
        abort(404)

    demo = mongo.demonstrations.find_one({"_id": ObjectId(case.demo_id)})
    if not demo:
        flash_message(_("Demoa ei löytynyt."), "danger")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    name = request.form.get("name", demo.get("name", ""))
    date = request.form.get("date", demo.get("date", ""))
    description = request.form.get("description", demo.get("description", ""))

    mongo.demonstrations.update_one(
        {"_id": ObjectId(case.demo_id)},
        {"$set": {"name": name, "date": date, "description": description}}
    )

    log_admin_action_V2(f"{current_user.username} päivitti demoa: {demo['_id']}", case_id)
    flash_message(_("Demo päivitetty!"), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


# --- Update organization suggestion ---
@admin_case_bp.route("/<case_id>/update_org_suggestion/", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def update_org_suggestion(case_id):
    case = Case.get(case_id)
    if not case or case.case_type != "organization_edit_suggestion" or not case.organization_id:
        abort(404)

    suggestion = request.form.get("suggestion", "").strip()
    if not suggestion:
        flash_message(_("Täytä muutosluonnos."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))

    mongo.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$set": {"suggestion": suggestion}}
    )

    log_admin_action_V2(f"{current_user.username} päivitti organisaation muutosluonnosta", case_id)
    flash_message(_("Muutosluonnos päivitetty!"), "success")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


# --- Delete an action note ---
@admin_case_bp.route("/<case_id>/delete_action/<action_idx>/", methods=["POST"])
@login_required
@admin_required
@case_permission("DELETE_CASE")
def delete_action(case_id, action_idx):
    case = Case.get(case_id)
    if not case or not case.action_logs:
        abort(404)

    try:
        action_idx = int(action_idx)
        if 0 <= action_idx < len(case.action_logs):
            removed = case.action_logs[action_idx]
            mongo.cases.update_one(
                {"_id": ObjectId(case_id)},
                {"$pull": {"action_logs": removed}}
            )
            log_admin_action_V2(f"{current_user.username} poisti merkinnän: {removed}", case_id)
            flash_message(_("Merkintä poistettu."), "success")
    except (ValueError, IndexError):
        flash_message(_("Virheellinen indeksi."), "danger")

    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/demo_action", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def demo_action(case_id):
    from mielenosoitukset_fi.admin.admin_demo_bp import approve_demo, reject_demo
    
    case = Case.get(case_id)
    if not case or case.case_type != "new_demo" or not case.demo_id:
        abort(404)

    data = request.get_json()
    if not data or "action" not in data:
        return jsonify({"error": _("Virheellinen pyyntö.")}), 400

    demo = mongo.demonstrations.find_one({"_id": ObjectId(case.demo_id)})
    if not demo:
        return jsonify({"error": _("Mielenosoitusta ei löytynyt.")}), 404

    response_status = ""

    if data["action"] == "accept":
        approve_demo(demo_id=case.demo_id)
        response_status = "accepted"

    elif data["action"] == "reject":
        try:
            metadata, schema = case_reasons.parse_reason(
                data, case_reasons.REJECT_KEYS, mongo, demo_id=case.demo_id,
            )
        except case_reasons.ReasonError as error:
            return jsonify({"error": str(error)}), 400

        reject_demo(case.demo_id)
        response_status = "rejected"
        now = utcnow()
        mongo.cases.update_one(
            {"_id": case._id},
            {
                "$push": {
                    "action_logs": {
                        "user": current_user.username,
                        "action_type": "reject_reason",
                        "reason": metadata["reason"],
                        "timestamp": now,
                    },
                    "case_history": {
                        "timestamp": now,
                        "action": "Mielenosoitus hylätty",
                        "user": current_user.username,
                        "mech_action": "reject_demo",
                        "metadata": metadata,
                        "meta_schema": schema,
                    },
                },
                "$set": {"updated_at": now},
            },
        )

    elif data["action"] in {"take_down", "confirm_take_down", "cancel_take_down"}:
        from mielenosoitukset_fi.admin.admin_demo_bp import _take_down_action

        body, code = _take_down_action(case.demo_id, data, source="case_take_down")
        if code != 200:
            return jsonify(body), code
        flash_message(body["message"], "success")
        return jsonify({"success": True, "status": body["status"]})

    elif data["action"] == "undo_approval":
        from mielenosoitukset_fi.demonstrations.decisions import UndoWindowClosed, undo_demo_approval
        from mielenosoitukset_fi.admin.utils import capture_actor_context
        from mielenosoitukset_fi.utils.wrappers import has_demo_approval_permission

        if not has_demo_approval_permission(current_user, demo):
            return jsonify({"error": _("Käyttöoikeutesi eivät riitä perumaan hyväksyntää.")}), 403
        try:
            undo_demo_approval(mongo, case.demo_id, actor=capture_actor_context(), source="case_undo_approval")
        except UndoWindowClosed:
            return jsonify({"error": _("Hyväksyntää ei voi enää perua. Käytä tarvittaessa Poista julkaisusta.")}), 409
        response_status = "pending"

    else:
        return jsonify({"error": _("Tuntematon toiminto.")}), 400
    flash_message(_("Mielenosoituksen tila päivitettiin."), "success")
    return jsonify({"success": True, "status": response_status})


@admin_case_bp.route("/<case_id>/approve_cancellation", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def approve_cancellation(case_id):
    case = Case.get(case_id)
    if not case or case.case_type != "demo_cancellation_request" or not case.demo_id:
        abort(404)

    demo = mongo.demonstrations.find_one({"_id": ObjectId(case.demo_id)})
    if not demo:
        return jsonify({"success": False, "error": "Mielenosoitusta ei löydy."}), 404

    if demo.get("cancelled"):
        return jsonify({"success": True, "status": "already_cancelled"})

    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or demo.get("cancellation_reason") or case.meta.get("reason"))

    try:
        cancel_demo(
            demo,
            cancelled_by={
                "user": current_user.username,
                "source": "admin_case",
                "official_contact": True,
            },
            reason=reason,
            create_case=False,
        )
    except Exception:
        logger.exception("Failed to approve cancellation for case %s", case_id)
        return jsonify({"success": False, "error": "Peruutuksen hyväksyntä epäonnistui."}), 500

    timestamp = utcnow()
    mongo.cases.update_one(
        {"_id": case._id},
        {
            "$set": {
                "meta.closed": True,
                "meta.cancellation_status": "accepted",
                "meta.resolved_by": current_user.username,
                "updated_at": timestamp,
            },
            "$push": {
                "case_history": {
                    "timestamp": timestamp,
                    "action": "cancellation_accepted",
                    "mech_action": "approve_cancellation",
                    "user": current_user.username,
                    "metadata": {"reason": reason},
                },
                "action_logs": {
                    "user": current_user.username,
                    "action_type": "approve_cancellation",
                    "reason": reason or "Hyväksytty ilman erillistä perustetta",
                    "timestamp": timestamp,
                },
            },
        },
    )

    flash_message(_("Peruutus hyväksytty ja mielenosoitus merkitty perutuksi."), "success")
    return jsonify({"success": True, "status": "cancelled"})

@admin_case_bp.route("/<case_id>/decline_cancellation", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def decline_cancellation(case_id):
    """Keep the demo and close the cancellation request with the admin's reason."""
    case = Case.get(case_id)
    if not case or case.case_type != "demo_cancellation_request":
        abort(404)
    reason = (request.form.get("reason") or "").strip()[:1000]
    if not reason:
        flash_message(_("Kirjoita syy, miksi peruutuspyyntö hylätään."), "warning")
        return redirect(url_for("admin_case.single_case", case_id=case_id))
    timestamp = utcnow()
    result = mongo.cases.update_one(
        {"_id": case._id, "meta.closed": {"$ne": True}},
        {
            "$set": {
                "meta.closed": True,
                "meta.closed_reason": "Peruutuspyyntö hylätty",
                "meta.closed_at": timestamp,
                "meta.cancellation_status": "declined",
                "meta.resolved_by": current_user.username,
                "updated_at": timestamp,
            },
            "$push": {
                "case_history": {
                    "timestamp": timestamp,
                    "action": "Peruutuspyyntö hylätty",
                    "mech_action": "decline_cancellation",
                    "user": current_user.username,
                    "metadata": {"reason": reason},
                    "meta_schema": {"reason": "string"},
                },
                "action_logs": {
                    "timestamp": timestamp,
                    "admin": current_user.username,
                    "action_type": "decline_cancellation",
                    "note": reason,
                },
            },
        },
    )
    if result.modified_count:
        flash_message(_("Peruutuspyyntö hylätty. Mielenosoitus pysyy julkaistuna."), "success")
    else:
        flash_message(_("Tapaus on jo suljettu."), "info")
    return redirect(url_for("admin_case.single_case", case_id=case_id))


@admin_case_bp.route("/<case_id>/demo_escalate", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def demo_escalate(case_id):
    case = Case.get(case_id)
    if not case or case.case_type != "new_demo" or not case.demo_id:
        abort(404)

    demo = mongo.demonstrations.find_one({"_id": ObjectId(case.demo_id)})
    if not demo:
        return jsonify({"error": _("Mielenosoitusta ei löytynyt.")}), 404

    # Escalation only asks the next level to decide (second reviewer, then
    # Johtoryhmä). It must not change the demo's status: approve and reject go
    # through the decision service, and an already decided demo is not
    # silently returned to the pending queue.
    if demo.get("approved") or demo.get("rejected"):
        return jsonify({
            "error": _("Mielenosoituksesta on jo tehty päätös, joten sitä ei voi eskaloida."),
        }), 409

    from mielenosoitukset_fi.utils import case_escalation as esc

    payload = request.get_json(silent=True) or {}
    try:
        reason = esc.clean_reason(payload.get("reason"))
    except esc.EscalationError:
        return jsonify({"error": _("Kirjoita lyhyt syy (5–500 merkkiä).")}), 400
    case_doc = mongo.cases.find_one({"_id": ObjectId(case_id)})
    try:
        level = esc.escalate(mongo, case_doc, current_user, reason, authentication="login")
    except esc.EscalationError as exc:
        messages = {
            "top_level": _("Tapaus on jo Johtoryhmän arvioitavana. Ylempää tasoa ei ole."),
            "not_allowed": _("Vain täysi ylläpitäjä voi siirtää tapausta ylemmälle tasolle."),
            "changed": _("Tapauksen taso ehti muuttua. Lataa sivu uudelleen."),
        }
        return jsonify({"error": messages.get(exc.code, _("Eskalointi ei onnistunut."))}), 409

    flash_message(_("Tapaus siirretty eteenpäin: %(name)s.", name=esc.LEVEL_NAMES[level]), "warning")
    return jsonify({"success": True})

@admin_case_bp.route("/<case_id>/deescalate", methods=["POST"])
@login_required
@admin_required
@case_permission("EDIT_CASE")
def deescalate_case(case_id):
    """De-escalate a case back to normal processing with proper action log."""

    case = mongo.cases.find_one({"_id": ObjectId(case_id)})
    if not case:
        return jsonify({"success": False, "error": _("Tapausta ei löytynyt.")}), 404

    try:
        timestamp = utcnow()

        # Update the case: remove superior_needed and append logs
        mongo.cases.update_one(
            {"_id": ObjectId(case_id)},
            {
                "$set": {"meta.superior_needed": False, "meta.escalation_level": 0, "updated_at": timestamp},
                "$push": {
                    "case_history": {
                        "timestamp": timestamp,
                        "action": "Eskalointi poistettu",
                        "user": current_user.username,
                        "metadata": {},
                    },
                    "action_logs": {
                        "user": current_user.username,
                        "action_type": "deescalate",
                        "reason": "Case de-escalated by superior",
                        "timestamp": timestamp
                    }
                }
            }
        )

        #log_admin_action_V2(
        #    f"{current_user.username} de-escalated case {case_id}", case_id
        #)

        return jsonify({"success": True, "message": "Case de-escalated successfully."}), 200

    except Exception as e:
        logger.error(f"De-escalation failed for case {case_id}: {e}")
        return jsonify({"success": False, "error": _("Palvelimella tapahtui virhe.")}), 500
