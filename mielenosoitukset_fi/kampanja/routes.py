# Standard library
import hashlib
import re
import secrets
import unicodedata
from mielenosoitukset_fi.utils.time_utils import utcnow
from datetime import datetime, timedelta

# Third-party
from flask import render_template, jsonify, request, make_response
from flask_babel import _

# Local imports
from . import campaign_bp
from mielenosoitukset_fi.utils.campaign_publication import campaign_preview_required
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.emailer.EmailSender import EmailSender
from mielenosoitukset_fi.utils.request_ip import get_client_ip

# Initialize email sender and database
email_sender = EmailSender()
db_manager = DatabaseManager().get_instance()
mongo = db_manager.get_db()


@campaign_bp.route("/")
@campaign_preview_required
def index():
    """Serve the main campaign page."""
    return render_template("kampanja/index.html")


# Role keys a volunteer can pick. The page sends these keys; labels live in the page.
VOLUNTEER_ROLES = ("stickers", "pride_banner", "events", "communications")
_FIELD_LIMITS = {"name": 120, "city": 100, "area": 200, "notes": 2000}
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _text(data, key):
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, str):
        return ""
    # Format characters (right-to-left overrides, zero-width spaces) can disguise text.
    return "".join(ch for ch in value if unicodedata.category(ch) != "Cf").strip()


# Limits per hour. The mail goes to an address the visitor typed, so unlimited
# signups would let anyone send unlimited confirmation mails to a stranger.
_LIMIT_WINDOW = timedelta(hours=1)
_LIMIT_PER_IP = 5
_LIMIT_PER_EMAIL = 2
_UNCONFIRMED_KEEP = timedelta(days=7)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _over_limit(kind, value, limit, now):
    """Count this attempt under a hashed key and report whether it exceeds the limit.
    Only a hash and a time are kept, and entries older than the window are dropped."""
    attempts = mongo.volunteer_signup_attempts
    attempts.delete_many({"at": {"$lt": now - _LIMIT_WINDOW}})
    key = f"{kind}:{_digest(value.lower())}"
    recent = attempts.count_documents({"key": key, "at": {"$gte": now - _LIMIT_WINDOW}})
    attempts.insert_one({"key": key, "at": now})
    return recent >= limit


def _volunteer_fields(data):
    """Validate a signup and return (fields, error). Unknown keys, including the
    removed phone number, are ignored and never stored."""
    name, email = _text(data, "name"), _text(data, "email")
    if not name or not email:
        return None, _("Nimi ja sähköpostiosoite ovat pakollisia.")
    roles = data.get("roles", [])
    if (
        not _EMAIL_PATTERN.match(email) or len(email) > 254
        or any(len(_text(data, key)) > limit for key, limit in _FIELD_LIMITS.items())
        or not isinstance(roles, list)
        or any(role not in VOLUNTEER_ROLES for role in roles)
    ):
        return None, _("Tarkista korostetut tiedot ja yritä uudelleen.")
    roles = [role for role in VOLUNTEER_ROLES if role in roles]
    fields = {
        "name": name, "email": email, "city": _text(data, "city"), "roles": roles,
        # The area only means something when the volunteer would hand out stickers.
        "area": _text(data, "area") if "stickers" in roles else "",
        "notes": _text(data, "notes"),
    }
    return fields, None


@campaign_bp.route("/api/volunteers", methods=["POST"])
@campaign_preview_required(api=True)
def add_volunteer():
    """Handle volunteer signups."""
    fields, error = _volunteer_fields(request.get_json(silent=True))
    if error:
        return jsonify({"success": False, "error": error}), 400

    now = utcnow()
    if _over_limit("ip", get_client_ip() or "unknown", _LIMIT_PER_IP, now):
        return jsonify({"success": False, "error": _("Yhteys palveluun epäonnistui. Yritä myöhemmin uudelleen.")}), 429
    # Do not reveal or act on repeats: the same address gets one pending mail, silently.
    if _over_limit("email", fields["email"], _LIMIT_PER_EMAIL, now):
        return jsonify({"success": True}), 201
    # Unconfirmed signups are removed a week after their link expired.
    mongo.volunteers.delete_many({
        "confirmed": {"$ne": True}, "confirmation_expires": {"$lt": now - _UNCONFIRMED_KEEP},
    })

    token = secrets.token_urlsafe(16)
    expires_at = now + timedelta(days=1)

    # Older documents have a phone number and no roles or area; readers treat
    # every new field as optional.
    volunteer = {
        **fields,
        "confirmed": False,
        # Only a hash of the link token is stored; older documents keep a plain token.
        "confirmation_token_hash": _digest(token),
        "confirmation_expires": expires_at,
    }

    mongo.volunteers.insert_one(volunteer)

    from mielenosoitukset_fi.utils.site_analytics import record_event_for_request

    record_event_for_request("volunteer_signup", resource_id=volunteer["city"] or None)

    confirm_link = f"https://mielenosoitukset.fi/kampanja/confirm/{token}"

    email_sender.queue_email(
        template_name="kampanja/welcome.html",
        subject="Kiitos ilmoittautumisesta! Vahvista sähköpostisi",
        recipients=[volunteer["email"]],
        context={"name": volunteer["name"], "confirm_link": confirm_link},
    )

    return jsonify({"success": True}), 201


def _confirmation_response(status, code=200):
    response = make_response(render_template(
        "kampanja/confirm_success.html", confirmation_status=status,
    ), code)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@campaign_bp.route("/confirm/<token>", methods=["GET"])
def confirm_volunteer(token):
    """Confirm volunteer email using token."""
    volunteer = mongo.volunteers.find_one({"$or": [
        {"confirmation_token_hash": _digest(token)}, {"confirmation_token": token},
    ]})
    if not volunteer:
        return _confirmation_response("invalid", 400)

    if volunteer["confirmation_expires"] < utcnow():
        return _confirmation_response("expired", 400)

    mongo.volunteers.update_one(
        {"_id": volunteer["_id"]},
        {"$set": {"confirmed": True}, "$unset": {"confirmation_token": "", "confirmation_token_hash": "", "confirmation_expires": ""}}
    )

    return _confirmation_response("success")
