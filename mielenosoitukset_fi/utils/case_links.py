"""Scoped case links in admin moderation emails.

Maintainer decision (20 Nov 2026): the admin email link opens that one new
demonstration without logging in, and lets the recipient approve or reject
that demonstration only. Everything else on the site still needs a login.

Rules kept here so the routes stay thin:

* one link per recipient admin and demonstration, valid 24 hours, revoked by
  any decision on the demonstration (``TERMINAL_TOKEN_ACTIONS``);
* the first browser that presses the open button owns it (HttpOnly
  cookie, hash stored); a plain GET never binds, so link scanners cannot;
* the link opens the same case page as Tapaukset (Olivia, 21 Nov 2026). It
  never logs anyone in: the browser keeps a ``case_link`` session marker, and
  ``user_for_request`` lets the recipient act as ``current_user`` only on the
  few routes of that one case (``allowed``). Everywhere else the browser is
  anonymous, and Go and other services never see a login;
* rights are checked at use time against the recipient's current account;
* if the recipient has two-factor authentication, a decision needs a TOTP
  code; accounts with only passkeys must log in to decide (no MFA bypass);
* only pending demonstrations can be decided this way; take-down, undo and
  republish stay behind login. After a decision the page stays readable in the
  same browser until the link expires, so the person sees what happened.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

ACTION = "case"
SESSION_KEY = "case_link"
COOKIE_NAME = "moso_case_link"
MAX_CODE_FAILURES = 5


class CaseLinkError(Exception):
    """The link cannot be used; ``code`` picks the message and status."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_link(db, token_hash: str, demo_id: str) -> dict:
    doc = db.magic_links.find_one({"token_hash": token_hash})
    if not doc or doc.get("action") != ACTION or doc.get("demo_id") != str(demo_id):
        raise CaseLinkError("unknown")
    if doc.get("revoked"):
        raise CaseLinkError("revoked")
    if doc.get("used_at"):
        raise CaseLinkError("used")
    expires_at = doc.get("expires_at")
    if expires_at and expires_at < utcnow():
        raise CaseLinkError("expired")
    return doc


def bind_browser(db, doc: dict, cookie_value: str | None, *, allow_bind: bool) -> str | None:
    """Return a new cookie value when this request binds, None when the cookie matches.

    Binding happens only on an explicit POST from the opening page, so mail
    scanners that prefetch links (e.g. Safe Links) cannot claim the link.
    Raises ``other_browser`` when another browser owns it, ``not_bound`` when
    nobody does yet and this request may not bind.
    """
    stored = doc.get("bound_browser_hash")
    if stored:
        if cookie_value and hmac.compare_digest(stored, hash_secret(cookie_value)):
            return None
        raise CaseLinkError("other_browser")
    if not allow_bind:
        raise CaseLinkError("not_bound")
    fresh = secrets.token_urlsafe(32)
    claimed = db.magic_links.update_one(
        {"_id": doc["_id"], "bound_browser_hash": None},
        {"$set": {"bound_browser_hash": hash_secret(fresh), "first_seen_at": utcnow()}},
    )
    if not claimed.modified_count:
        raise CaseLinkError("other_browser")
    return fresh


def recipient_user(db, doc: dict):
    """The admin the link was sent to, as their current account, or raise."""
    from mielenosoitukset_fi.users.models import User

    recipient = doc.get("intended_recipient") or {}
    user_id = recipient.get("user_id")
    if not user_id or not ObjectId.is_valid(str(user_id)):
        raise CaseLinkError("no_recipient")
    user_doc = db.users.find_one({"_id": ObjectId(str(user_id))})
    if not user_doc:
        raise CaseLinkError("no_recipient")
    user = User.from_db(user_doc)
    if not user.is_active or not user.has_admin_access():
        raise CaseLinkError("no_rights")
    return user


def _id_variants(user):
    """MFA and passkey rows store the user id as ObjectId or as a string."""
    raw = str(getattr(user, "_id", None) or getattr(user, "id", None))
    return [raw, ObjectId(raw)] if ObjectId.is_valid(raw) else [raw]


def second_factor(db, user) -> str | None:
    """"totp" when a code is needed, "passkey_only" when only login works, else None."""
    ids = _id_variants(user)
    has_totp = bool(getattr(user, "mfa_enabled", False)) and db.mfas.count_documents({"user_id": {"$in": ids}}) > 0
    if has_totp:
        return "totp"
    from mielenosoitukset_fi.utils.webauthn_utils import PASSKEY_COLLECTION

    has_passkey = db[PASSKEY_COLLECTION].count_documents({"user_id": {"$in": ids}}) > 0
    if has_passkey or getattr(user, "mfa_enabled", False):
        return "passkey_only"
    return None


def check_code(db, doc: dict, user, code: str) -> None:
    """Verify a TOTP code; too many failures revoke the link."""
    from mielenosoitukset_fi.users.models import UserMFA

    code = (code or "").strip().replace(" ", "")
    # Same verifier as the login form; one code is never accepted twice here.
    user_oid = ObjectId(str(getattr(user, "_id", None) or user.id))
    if code and code.isdigit() and code != doc.get("last_code") and UserMFA(user_oid).verify_token(code):
        # Atomic: two requests carrying the same code cannot both pass.
        claimed = db.magic_links.update_one(
            {"_id": doc["_id"], "last_code": {"$ne": code}}, {"$set": {"last_code": code}}
        )
        if claimed.modified_count:
            return
    updated = db.magic_links.find_one_and_update(
        {"_id": doc["_id"]}, {"$inc": {"code_failures": 1}}, return_document=True
    )
    if (updated or {}).get("code_failures", 0) >= MAX_CODE_FAILURES:
        db.magic_links.update_one(
            {"_id": doc["_id"]},
            {"$set": {"revoked": True, "revoked_at": utcnow(), "revoked_reason": "too_many_code_failures"}},
        )
        raise CaseLinkError("revoked")
    raise CaseLinkError("bad_code")


def actor_for(user, doc: dict, *, mfa: bool) -> dict:
    return {
        "user_id": str(user.id),
        "username": user.username,
        "email": getattr(user, "email", None),
        "role": getattr(user, "role", None),
        "global_admin": bool(getattr(user, "global_admin", False)),
        "authentication": "case_link",
        "second_factor": "totp" if mfa else None,
        "link_id": str(doc["_id"]),
    }


# Routes the link may use, with the argument that must equal the link's own case
# or demonstration. Everything else treats the browser as signed out.
_CASE_ENDPOINTS = {
    "admin_case.single_case": "case_id",
    "admin_case.demo_action": "case_id",
    "admin_case.demo_escalate": "case_id",
}
_DEMO_ENDPOINTS = {
    "admin_demo.preview_demo": "demo_id",
    "admin_demo_api.preview_demo_decision_effects": "demo_id",
}
_WRITE_ENDPOINTS = {"admin_case.demo_action", "admin_case.demo_escalate"}
_SHARED_ENDPOINTS = {"static", "users.auth.login", "users.auth.logout"}


def begin_session(session, doc: dict, case_id: str) -> None:
    session[SESSION_KEY] = {"link": str(doc["_id"]), "case": str(case_id)}


def end_session(session) -> None:
    session.pop(SESSION_KEY, None)


def allowed(endpoint, view_args, state: dict) -> bool:
    view_args = view_args or {}
    if endpoint in _CASE_ENDPOINTS:
        return str(view_args.get(_CASE_ENDPOINTS[endpoint])) == state["case"]
    if endpoint == "admin_case.demo_search":
        # The reject dialog's "which demo is this a duplicate of" search; the
        # recipient's own case rights still decide what it may list.
        return True
    if endpoint in _DEMO_ENDPOINTS:
        return str(view_args.get(_DEMO_ENDPOINTS[endpoint])) == state.get("demo")
    return False


def user_for_request(db, session, cookies, endpoint, view_args, method):
    """Return ``(user, info)`` for a request made through a case link, else ``(None, None)``.

    ``info`` holds the link document and its case and demonstration ids. A
    link that a decision already ended still shows the case (GET only); a link
    revoked for another reason, expired, or opened in another browser shows nothing.
    """
    state = session.get(SESSION_KEY)
    if not isinstance(state, dict) or not ObjectId.is_valid(str(state.get("link"))):
        return None, None
    doc = db.magic_links.find_one({"_id": ObjectId(state["link"])})
    if not doc or doc.get("action") != ACTION:
        return None, None
    state = {**state, "demo": str(doc.get("demo_id"))}
    if not allowed(endpoint, view_args, state):
        return None, None
    if state["case"] != str(_case_id_for(db, doc)):
        return None, None
    expires_at = doc.get("expires_at")
    if expires_at and expires_at < utcnow():
        return None, None
    stored = doc.get("bound_browser_hash")
    cookie = cookies.get(COOKIE_NAME)
    if not stored or not cookie or not hmac.compare_digest(stored, hash_secret(cookie)):
        return None, None
    ended = bool(doc.get("revoked") or doc.get("used_at"))
    if doc.get("revoked") and not str(doc.get("revoked_reason") or "").startswith("demo_"):
        return None, None
    if ended and (method not in ("GET", "HEAD") or endpoint in _WRITE_ENDPOINTS):
        return None, None
    try:
        user = recipient_user(db, doc)
    except CaseLinkError:
        return None, None
    if getattr(user, "security_suspended", False):
        return None, None
    from mielenosoitukset_fi.utils.wrappers import has_demo_approval_permission

    demo = db.demonstrations.find_one({"_id": ObjectId(state["demo"])}) if ObjectId.is_valid(state["demo"]) else None
    if not demo or not has_demo_approval_permission(user, demo):
        return None, None
    return user, {"doc": doc, "case_id": state["case"], "demo_id": state["demo"], "ended": ended}


def _case_id_for(db, doc: dict):
    demo_id = str(doc.get("demo_id"))
    ids = [demo_id] + ([ObjectId(demo_id)] if ObjectId.is_valid(demo_id) else [])
    case = db.cases.find_one({"type": "new_demo", "demo_id": {"$in": ids}}, {"_id": 1}, sort=[("created_at", -1)])
    return (case or {}).get("_id")


def require_second_factor(db, info: dict, user) -> None:
    """Passkey-only accounts must log in; others with two-factor must give a code (checked by the caller)."""
    if second_factor(db, user) == "passkey_only":
        raise CaseLinkError("passkey_only")
