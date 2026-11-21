"""
Step-up ("sudo") authentication state.

Elevation is a lightweight server-side session flag with a hard timeout.
Expiring the elevated state never logs the user out — the normal authenticated
session stays untouched. The flag is bound to the user id so it cannot be
replayed across accounts.

Fresh step-up tokens provide single-use, action-bound authentication for
highly sensitive operations (e.g., user deletion). Unlike session elevation,
fresh tokens cannot be reused and must be obtained through a new authentication
ceremony for each action.
"""

import hashlib
import secrets
import time
from datetime import timedelta
from functools import wraps
from typing import Optional

from bson import ObjectId
from flask import jsonify, request, session
from flask_babel import gettext as _
from flask_login import current_user

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.time_utils import utcnow

SUDO_SESSION_KEY = "sudo"
FRESH_STEP_UP_COLLECTION = "fresh_step_up_tokens"
FRESH_STEP_UP_TTL_SECONDS = 60


def _configured_timeout() -> float:
    from flask import current_app

    try:
        return float(current_app.config.get("SUDO_DEFAULT_TIMEOUT", 900))
    except (TypeError, ValueError):
        return 900.0


def grant_elevation(method: str, *, strong: bool = False) -> None:
    """Mark the current user as recently step-up authenticated.

    ``strong`` records that a passkey or a TOTP code was verified, not just
    the password.
    """
    session[SUDO_SESSION_KEY] = {
        "authenticated_at": time.time(),
        "method": method,
        "strong": bool(strong),
        "user_id": str(current_user._id),
    }
    session.modified = True


def has_strong_elevation(timeout_seconds: Optional[float] = None) -> bool:
    """Elevated within the window by a passkey or password plus TOTP."""
    return has_elevation(timeout_seconds) and bool(session.get(SUDO_SESSION_KEY, {}).get("strong"))


def clear_elevation() -> None:
    """Drop any step-up elevation for the current session."""
    if SUDO_SESSION_KEY in session:
        session.pop(SUDO_SESSION_KEY, None)
        session.modified = True


def elevation_method() -> Optional[str]:
    """Return the method used for the current elevation (stale or not)."""
    sudo = session.get(SUDO_SESSION_KEY)
    if not sudo or str(sudo.get("user_id", "")) != str(current_user._id):
        return None
    return sudo.get("method")


def has_elevation(timeout_seconds: Optional[float] = None) -> bool:
    """Return whether the current session is still elevated."""
    sudo = session.get(SUDO_SESSION_KEY)
    if not sudo:
        return False
    if str(sudo.get("user_id", "")) != str(current_user._id):
        clear_elevation()
        return False

    window = timeout_seconds if timeout_seconds is not None else _configured_timeout()
    age = time.time() - sudo.get("authenticated_at", 0)
    if age < 0 or age > window:
        clear_elevation()
        return False
    return True


def is_elevated(timeout_seconds: Optional[float] = None) -> bool:
    """Alias for callers that read better with a positive name."""
    return has_elevation(timeout_seconds=timeout_seconds)


def step_up_required_response():
    """The answer a guarded request gets when the session is not elevated."""
    return (
        jsonify(
            {
                "error": "step_up_required",
                "message": "Vahvista henkilöllisyytesi uudelleen ennen tätä toimintoa.",
            }
        ),
        403,
    )


# Password step-up attempts: a signed-in session may guess the password (and
# MFA code) only a few times per window before it has to wait.
STEP_UP_FAILURE_COLLECTION = "step_up_failures"
STEP_UP_MAX_FAILURES = 5
STEP_UP_FAILURE_WINDOW_SECONDS = 15 * 60


def _step_up_failures():
    return DatabaseManager.get_instance().get_db()[STEP_UP_FAILURE_COLLECTION]


STEP_UP_FAILURE_TTL_SECONDS = 24 * 60 * 60


def ensure_step_up_failure_indexes(db) -> None:
    """Lookup index for the window count, and a TTL so old rows go away."""
    collection = db[STEP_UP_FAILURE_COLLECTION]
    collection.create_index(
        [("user_id", 1), ("created_at", 1)], name="step_up_failures_window"
    )
    collection.create_index(
        "created_at",
        expireAfterSeconds=STEP_UP_FAILURE_TTL_SECONDS,
        name="step_up_failures_ttl",
    )


def reserve_step_up_attempt() -> Optional[ObjectId]:
    """Count this attempt before checking anything; None when the limit is used up.

    The row is written first and the window counted after, so parallel
    requests cannot all pass the check before any failure is recorded: of any
    burst, the request that counts last sees every row that passed before it.
    Refused rows are removed again, so a burst does not extend the wait. The
    caller marks the row as a failure, releases it, or clears all on success.
    """
    from mielenosoitukset_fi.utils.request_ip import get_client_ip

    user_id = ObjectId(str(current_user._id))
    now = utcnow()
    collection = _step_up_failures()
    row_id = collection.insert_one(
        {"user_id": user_id, "reason": "pending", "ip": get_client_ip(), "created_at": now}
    ).inserted_id
    since = now - timedelta(seconds=STEP_UP_FAILURE_WINDOW_SECONDS)
    used = collection.count_documents(
        {"user_id": user_id, "created_at": {"$gte": since}}
    )
    if used > STEP_UP_MAX_FAILURES:
        collection.delete_one({"_id": row_id})
        return None
    return row_id


def record_step_up_failure(row_id: ObjectId, reason: str) -> None:
    """Keep the reserved attempt as a failure."""
    _step_up_failures().update_one({"_id": row_id}, {"$set": {"reason": reason}})


def release_step_up_attempt(row_id: ObjectId) -> None:
    """Drop a reserved attempt that was neither a failure nor a success."""
    _step_up_failures().delete_one({"_id": row_id})


def clear_step_up_failures() -> None:
    """Forget failed attempts after a successful step-up."""
    _step_up_failures().delete_many({"user_id": ObjectId(str(current_user._id))})


def step_up_throttled_response():
    return (
        jsonify(
            {
                "status": "error",
                "error": "step_up_throttled",
                "message": _("Liian monta yritystä. Yritä hetken kuluttua uudelleen."),
            }
        ),
        429,
    )


def sudo_required(timeout_seconds: Optional[float] = None):
    """
    Guard a route behind recent step-up authentication.

    Returns ``403 {"error": "step_up_required"}`` when the session is not
    elevated (the frontend re-authenticates and retries the request) and
    ``401`` when no user is logged in.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return (
                    jsonify(
                        {
                            "error": "authentication_required",
                            "message": _("Kirjaudu sisään ennen tätä toimintoa."),
                        }
                    ),
                    401,
                )
            if is_elevated(timeout_seconds=timeout_seconds):
                return func(*args, **kwargs)
            return step_up_required_response()
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Fresh step-up tokens (single-use, action-bound)
# ---------------------------------------------------------------------------


def _get_mongo():
    """Return the active database handle."""
    return DatabaseManager().get_instance().get_db()


def create_fresh_step_up_token(action: str, target_id: Optional[str] = None) -> str:
    """
    Create a single-use fresh step-up token for a specific action.

    The token is bound to the current user, the action, and optionally a target ID.
    It expires after FRESH_STEP_UP_TTL_SECONDS and can only be used once.

    Returns the token string.
    """
    if not current_user.is_authenticated:
        raise RuntimeError("Cannot create fresh step-up token for anonymous user")

    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)

    normalized_target_id = str(target_id) if target_id is not None else None
    now = utcnow()
    doc = {
        "token_hash": token_hash,
        "user_id": ObjectId(current_user._id),
        "action": action,
        "target_id": normalized_target_id,
        "created_at": now,
        "expires_at": now + timedelta(seconds=FRESH_STEP_UP_TTL_SECONDS),
        "used": False,
    }
    _get_mongo()[FRESH_STEP_UP_COLLECTION].insert_one(doc)

    return token


def _hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def consume_fresh_step_up_token(token: str, action: str, target_id: Optional[str] = None) -> bool:
    """
    Atomically consume a fresh step-up token.

    Returns True if the token was valid, unused, unexpired, and matched the
    action and target_id. Returns False otherwise.
    """
    if not current_user.is_authenticated:
        return False

    token_hash = _hash_token(token)

    query = {
        "token_hash": token_hash,
        "user_id": ObjectId(current_user._id),
        "action": action,
        "used": False,
        "expires_at": {"$gt": utcnow()},
    }
    query["target_id"] = str(target_id) if target_id is not None else None

    result = _get_mongo()[FRESH_STEP_UP_COLLECTION].find_one_and_update(
        query,
        {"$set": {"used": True, "used_at": utcnow()}},
    )
    return result is not None


def require_fresh_step_up(action: str, target_id_param: str = "user_id"):
    """
    Decorator that requires a valid fresh step-up token for the given action.

    The token must be provided in the request JSON body or form data under
    the key "fresh_step_up_token". The target_id is extracted from the
    request using target_id_param.

    Returns 403 with error "fresh_step_up_required" if the token is missing,
    invalid, or already used.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return (
                    jsonify(
                        {
                            "error": "authentication_required",
                            "message": "Kirjaudu sisään ennen tätä toimintoa.",
                        }
                    ),
                    401,
                )

            data = (
                request.get_json(silent=True) or {}
                if request.is_json
                else request.form.to_dict()
            )

            token = data.get("fresh_step_up_token") if data else None
            target_id = data.get(target_id_param) if data else None

            if not token:
                return (
                    jsonify(
                        {
                            "error": "fresh_step_up_required",
                            "message": _(
                                "Tämä toiminto vaatii tuoreen vahvistuksen. "
                                "Vahvista henkilöllisyytesi uudelleen."
                            ),
                        }
                    ),
                    403,
                )

            if not consume_fresh_step_up_token(token, action, target_id):
                return (
                    jsonify(
                        {
                            "error": "fresh_step_up_required",
                            "message": _(
                                "Vahvistus on virheellinen, vanhentunut tai jo käytetty. "
                                "Vahvista henkilöllisyytesi uudelleen."
                            ),
                        }
                    ),
                    403,
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator
