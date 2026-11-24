"""Open tracking for staff emails only.

Only templates listed in ``TRACKED_TEMPLATES`` (mail to site admins and case
handlers) get a pixel. Public users' mail is never tracked. Stores a SHA-256
hash of the token, template name and timestamps: no IP, no user agent, no
recipient address. Rows expire after ``RETENTION_DAYS``.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response

COLLECTION = "email_opens"
RETENTION_DAYS = 90
BASE_URL = "https://mielenosoitukset.fi"

TRACKED_TEMPLATES = frozenset({
    "admin_case_notification.html",
    "admin_case_escalated.html",
    "admin_demo_approve_notification.html",
    "admin_demo_city_assignment.html",
    "admin_demo_city_escalation.html",
    "admin_org_suggestion_notification.html",
    "admin_suggestion_notification.html",
})

_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f9040100000000"
    "2c00000000010001000002024401003b"
)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

email_open_bp = Blueprint("email_open", __name__)


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_indexes(db):
    db[COLLECTION].create_index("token_hash", unique=True, name="email_open_token")
    db[COLLECTION].create_index("purge_at", expireAfterSeconds=0, name="email_open_purge")


def is_tracked(template_name):
    return template_name in TRACKED_TEMPLATES


def add_pixel(db, html, template_name, now=None, meta=None):
    """Register a token for this send and append the pixel to ``html``.

    ``meta`` may link the open to a staff account and case
    (``{"case_id": ObjectId, "user_id": ObjectId}``) so the case page can show
    which admin read the notice. Never put an address, IP or user agent here.
    """
    now = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(24)
    ensure_indexes(db)
    db[COLLECTION].insert_one({
        "token_hash": _hash(token),
        "template": template_name,
        "created_at": now,
        "purge_at": now + timedelta(days=RETENTION_DAYS),
        "first_opened_at": None,
        "open_count": 0,
        **{k: v for k, v in (meta or {}).items() if k in ("case_id", "user_id") and v is not None},
    })
    pixel = (f'<img src="{BASE_URL}/e/o/{token}.gif" width="1" height="1" alt="" '
             'style="display:block;width:1px;height:1px;border:0">')
    if re.search(r"</body>", html, re.I):
        return re.sub(r"</body>", lambda m: pixel + m.group(0), html, count=1, flags=re.I)
    return html + pixel


def opens_for_case(db, case_id):
    """Per staff account: ``{"first_opened_at", "open_count"}`` for one case's mails."""
    result = {}
    for row in db[COLLECTION].find({"case_id": case_id, "user_id": {"$exists": True}}):
        entry = result.setdefault(str(row["user_id"]), {"first_opened_at": None, "open_count": 0})
        entry["open_count"] += int(row.get("open_count") or 0)
        first = row.get("first_opened_at")
        if first and (entry["first_opened_at"] is None or first < entry["first_opened_at"]):
            entry["first_opened_at"] = first
    return result


@email_open_bp.route("/e/o/<token>.gif")
def open_pixel(token):
    if _TOKEN_RE.match(token):
        from mielenosoitukset_fi.database_manager import DatabaseManager
        try:
            db = DatabaseManager().get_instance().get_db()
            now = datetime.now(timezone.utc)
            col = db[COLLECTION]
            h = _hash(token)
            col.update_one({"token_hash": h, "first_opened_at": None},
                           {"$set": {"first_opened_at": now}})
            col.update_one({"token_hash": h},
                           {"$set": {"last_opened_at": now}, "$inc": {"open_count": 1}})
        except Exception:
            pass
    resp = Response(_GIF, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp
