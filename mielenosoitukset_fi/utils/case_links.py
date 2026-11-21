"""Scoped case links in admin moderation emails.

Maintainer decision (20 Nov 2026): the admin email link opens that one new
demonstration without logging in, and lets the recipient approve or reject
that demonstration only. Everything else on the site still needs a login.

Rules kept here so the routes stay thin:

* one link per recipient admin and demonstration, valid 24 hours, revoked by
  any decision on the demonstration (``TERMINAL_TOKEN_ACTIONS``);
* the first browser that presses the open button owns it (HttpOnly
  cookie, hash stored); a plain GET never binds, so link scanners cannot;
* the link never logs anyone in and never touches ``current_user``;
* rights are checked at use time against the recipient's current account;
* if the recipient has two-factor authentication, a decision needs a TOTP
  code; accounts with only passkeys must log in to decide (no MFA bypass);
* only pending demonstrations can be decided this way; take-down, undo and
  republish stay behind login.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

ACTION = "case"
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
        db.magic_links.update_one({"_id": doc["_id"]}, {"$set": {"last_code": code}})
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
