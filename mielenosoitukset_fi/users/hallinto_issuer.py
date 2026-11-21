"""Isolated, first-party OIDC issuer for the Hallinto confidential client."""

import hashlib
import json
import logging
import re
import secrets
from mielenosoitukset_fi.utils.cities import CITY_KEY_TO_NAME
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from html import escape
from time import time
from urllib.parse import unquote_plus, urlsplit

from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import AuthorizationCodeMixin, ClientMixin
from authlib.oauth2.rfc6749.errors import InvalidGrantError, InvalidRequestError, OAuth2Error
from authlib.oauth2.rfc6749.grants import AuthorizationCodeGrant
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oidc.core import UserInfo
from authlib.oidc.core.grants import OpenIDCode
from bson import ObjectId
from flask import Blueprint, jsonify, make_response, redirect, request, session, url_for
from flask_login import current_user, user_logged_in
from joserfc.jwk import RSAKey
from pymongo.errors import DuplicateKeyError, PyMongoError

from mielenosoitukset_fi.users.hallinto_sessions import AccountVersion, issue_marker, revoke_marker
from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils.auth import generate_reset_token
from mielenosoitukset_fi.utils.validators import (
    is_reserved_identity_name,
    normalize_email,
    normalize_username,
    valid_email,
    validate_username,
)
from mielenosoitukset_fi.utils.time_utils import utcnow
from mielenosoitukset_fi.utils.webauthn_utils import PASSKEY_COLLECTION

ISSUER_PATH = "/hallinto-oidc"
SESSION_MARKER = "hallinto_owner_login"
_CODE_LIFETIME = timedelta(minutes=2)
_ID_TOKEN_SECONDS = 120
_REFERENCE = re.compile(r"[0-9a-f]{64}\Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _account_version(db, subject: str, authenticated_user: User | None = None) -> AccountVersion | None:
    if not isinstance(subject, str) or not ObjectId.is_valid(subject) or str(ObjectId(subject)) != subject:
        return None
    account_id = ObjectId(subject)
    doc = db.users.find_one({"_id": account_id})
    if not doc:
        return None
    user = User.from_db(doc)
    if authenticated_user is not None and (
        user.auth_epoch != authenticated_user.auth_epoch
        or user.password_hash != authenticated_user.password_hash
        or user.mfa_enabled != authenticated_user.mfa_enabled
    ):
        return None
    # A future relying party for god accounts requires its own assurance model.
    if (not user.is_active or not user.confirmed or user.role == "god"
            or user.forced_pwd_reset or user.forced_identity_change):
        return None
    # IDs, not factor secrets or mutable last-use counters, bind this browser
    # login to the factors that were present when Flask authenticated it.
    mfa_ids = sorted(str(row["_id"]) for row in db.mfas.find({"user_id": {"$in": [account_id, subject]}}, {"_id": 1}))
    passkey_ids = sorted(str(row["_id"]) for row in db[PASSKEY_COLLECTION].find({"user_id": {"$in": [account_id, subject]}}, {"_id": 1}))
    version = _digest(json.dumps([user.password_hash, user.mfa_enabled, mfa_ids, passkey_ids]))
    return AccountVersion(account_id, user.auth_epoch, version)


def _live_marker(db, markers, reference: str, subject: str) -> dict | None:
    if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
        return None
    account = _account_version(db, subject)
    if account is None:
        return None
    return markers.find_one(
        {
            "_id": reference, "user_id": account.user_id,
            "auth_epoch": account.auth_epoch, "credential_version": account.credential_version,
            "revoked_at": None, "expires_at": {"$gt": utcnow()},
        },
        {"issued_at": 1},
    )


# Hallinto links group memberships to accounts. Only admins signed in to
# Hallinto may search, and they see only a name, username and status.
_ACCOUNT_LOOKUP_ROLES = {"admin", "global_admin"}
_ACCOUNT_FIELDS = {"username": 1, "displayname": 1, "active": 1, "confirmed": 1, "banned": 1, "security_suspension": 1}
_ACCOUNT_ID = re.compile(r"[0-9a-f]{24}\Z")
_ACCOUNT_SEARCH_LIMIT = 10
# Organization request states Hallinto may see and set (docs/organization-requests.md).
# "unconfirmed" stays hidden until the applicant has opened the mailed link.
_ORG_REQUEST_STATUSES = ("received", "verifying", "in_review", "needs_info", "approved", "rejected", "withdrawn")
_ACCOUNT_LOOKUP_LIMIT = 100
_invite_sender = None


def invite_sender():
    """The emailer for account invites, created on first use."""
    global _invite_sender
    if _invite_sender is None:
        from mielenosoitukset_fi.emailer.EmailSender import EmailSender
        _invite_sender = EmailSender()
    return _invite_sender


def _account_view(doc: dict) -> dict:
    suspended = bool((doc.get("security_suspension") or {}).get("active"))
    return {
        "sub": str(doc["_id"]),
        "username": doc.get("username", ""),
        "name": doc.get("displayname") or doc.get("username", ""),
        "active": bool(doc.get("active", True) and doc.get("confirmed", False)
                       and not doc.get("banned") and not suspended),
    }


@dataclass(frozen=True)
class _Client(ClientMixin):
    client_id: str
    client_secret: str
    redirect_uri: str

    def get_client_id(self):
        return self.client_id

    def get_default_redirect_uri(self):
        return None

    def get_allowed_scope(self, scope):
        return "openid" if scope == "openid" else None

    def check_redirect_uri(self, redirect_uri):
        return redirect_uri == self.redirect_uri

    def check_client_secret(self, client_secret):
        return compare_digest(client_secret.encode(), self.client_secret.encode())

    def check_endpoint_auth_method(self, method, endpoint):
        return endpoint == "token" and method == "client_secret_basic"

    def check_response_type(self, response_type):
        return response_type == "code"

    def check_grant_type(self, grant_type):
        return grant_type == "authorization_code"


@dataclass(frozen=True)
class _Code(AuthorizationCodeMixin):
    document: dict

    def get_redirect_uri(self):
        return self.document["redirect_uri"]

    def get_scope(self):
        return "openid"

    def get_nonce(self):
        return self.document["nonce"]

    def get_auth_time(self):
        return int(self.document["issued_at"].replace(tzinfo=UTC).timestamp())

    def get_acr(self):
        return None

    def get_amr(self):
        return None


class _S256(CodeChallenge):
    def validate_code_challenge(self, grant, redirect_uri):
        payload = grant.request.payload.data
        if not payload.get("code_challenge") or payload.get("code_challenge_method") != "S256":
            raise InvalidRequestError("S256 is required")
        super().validate_code_challenge(grant, redirect_uri)

    def get_authorization_code_challenge(self, authorization_code):
        return authorization_code.document["challenge"]

    def get_authorization_code_challenge_method(self, authorization_code):
        return "S256"


def _https_url(value: str):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("Hallinto OIDC URLs need HTTPS without credentials, query or fragment")
    return parsed


def register_hallinto_issuer(app, db, settings: dict, *, state_db) -> None:
    issuer = settings["issuer"]
    origin = _https_url(issuer)
    if origin.path != ISSUER_PATH:
        raise ValueError("Hallinto issuer must be mounted at " + ISSUER_PATH)
    client = _Client(settings["client_id"], settings["client_secret"], settings["redirect_uri"])
    if _https_url(client.redirect_uri).path != "/auth/callback" or not client.client_id or len(client.client_secret) < 32:
        raise ValueError("Hallinto issuer client configuration is incomplete")
    key = RSAKey.import_key(settings["private_key"], parameters={"kid": settings["key_id"]})
    if not key.is_private or key.raw_value.key_size < 2048 or not settings["key_id"]:
        raise ValueError("Hallinto issuer needs an identified RSA private key of at least 2048 bits")

    # Authlib's grant debug logger includes bearer tokens in messages.
    logging.getLogger("authlib.oauth2.rfc6749.grants.authorization_code").setLevel(logging.WARNING)
    auth = AuthorizationServer(
        app,
        query_client=lambda identifier: client if identifier == client.client_id else None,
        save_token=lambda *_: None,
    )
    codes = state_db.hallinto_oidc_codes
    markers = state_db.hallinto_login_markers

    class Grant(AuthorizationCodeGrant):
        def save_authorization_code(self, code, oauth_request):
            payload = oauth_request.payload.data
            reference = _digest(session[SESSION_MARKER])
            marker = _live_marker(db, markers, reference, current_user.get_id())
            if not marker:
                raise InvalidGrantError("Owner login ended")
            try:
                # The nonce hash is Mongo's unique key. Concurrent authorization
                # requests cannot reuse a nonce even on different workers.
                codes.insert_one({
                    "_id": _digest(payload["nonce"]),
                    "code_digest": _digest(code),
                    "redirect_uri": payload["redirect_uri"],
                    "nonce": payload["nonce"],
                    "challenge": payload["code_challenge"],
                    "subject": current_user.get_id(),
                    "sid": reference,
                    "issued_at": marker["issued_at"],
                    "expires_at": utcnow() + _CODE_LIFETIME,
                    "used_at": None,
                })
            except DuplicateKeyError as exc:
                raise InvalidRequestError("Nonce already used") from exc

        def query_authorization_code(self, code, oauth_client):
            doc = codes.find_one({"code_digest": _digest(code), "used_at": None, "expires_at": {"$gt": utcnow()}})
            return _Code(doc) if doc else None

        def authenticate_user(self, authorization_code):
            # Authlib already validated the client, redirect and S256 verifier.
            doc = authorization_code.document
            now = utcnow()
            consumed = codes.update_one(
                {"_id": doc["_id"], "used_at": None, "expires_at": {"$gt": now}},
                {"$set": {"used_at": now}},
            )
            if consumed.matched_count != 1 or not _live_marker(db, markers, doc["sid"], doc["subject"]):
                return None
            return doc["subject"]

        def delete_authorization_code(self, authorization_code):
            # The atomic consume above preserves the nonce tombstone for expiry.
            pass

    class Identity(OpenIDCode):
        def exists_nonce(self, nonce, oauth_request):
            return codes.find_one({"_id": _digest(nonce)}, {"_id": 1}) is not None

        def get_client_claims(self, oauth_client):
            return {"iss": issuer, "aud": [client.client_id], "exp": int(time()) + _ID_TOKEN_SECONDS}

        def get_encode_header(self, oauth_client):
            return {"alg": "RS256", "kid": settings["key_id"]}

        def resolve_client_private_key(self, oauth_client):
            return key

        def get_authorization_code_claims(self, authorization_code):
            return {
                **super().get_authorization_code_claims(authorization_code),
                "sid": authorization_code.document["sid"],
            }

        def generate_user_info(self, user, scope):
            return UserInfo(sub=user)

    auth.register_grant(Grant, extensions=[_S256(), Identity(require_nonce=True)])
    bp = Blueprint("hallinto_issuer", __name__, url_prefix=ISSUER_PATH)

    @user_logged_in.connect_via(app)
    def start_owner_login(sender, user, **kwargs):
        previous = session.get(SESSION_MARKER)
        try:
            if previous:
                # A fresh login can replace the account ID in Flask's cookie.
                # Find the prior binding before removing the browser marker.
                old = markers.find_one({"_id": _digest(previous)}, {"user_id": 1})
                if old:
                    revoke_marker(markers, previous, old["user_id"])
                session.pop(SESSION_MARKER, None)
                session.pop("hallinto_owner_revocation_pending", None)
            account = _account_version(db, user.get_id(), user)
            if account:
                session[SESSION_MARKER] = issue_marker(
                    markers, account, utcnow() + app.permanent_session_lifetime,
                )
        except PyMongoError:
            # Hallinto downtime must not block first-party login. Retain an
            # old binding only for a later revocation attempt, never issuance.
            if session.get(SESSION_MARKER):
                session["hallinto_owner_revocation_pending"] = True
            app.logger.warning("Hallinto owner login marker unavailable")

    def end_owner_login(subject):
        marker = session.get(SESSION_MARKER)
        if marker:
            try:
                if session.get("hallinto_owner_revocation_pending"):
                    old = markers.find_one({"_id": _digest(marker)}, {"user_id": 1})
                    if old:
                        revoke_marker(markers, marker, old["user_id"])
                else:
                    revoke_marker(markers, marker, ObjectId(subject))
            except PyMongoError:
                return False
            session.pop(SESSION_MARKER, None)
            session.pop("hallinto_owner_revocation_pending", None)
        return True

    app.extensions["hallinto_owner_logout"] = end_owner_login

    @bp.before_request
    def require_canonical_origin():
        original = request.environ.get("werkzeug.proxy_fix.orig", {})
        # Require TLS confirmation at the trusted WSGI server/proxy hop.
        # ProxyFix's request scheme alone follows a forwarded header.
        if (original.get("wsgi.url_scheme", request.scheme) != "https"
                or not request.is_secure or request.host != origin.netloc):
            return jsonify(error="invalid_request"), 400

    @bp.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @bp.errorhandler(PyMongoError)
    def owner_unavailable(_error):
        return jsonify(error="temporarily_unavailable"), 503

    @bp.get("/.well-known/openid-configuration")
    def discovery():
        return jsonify(
            issuer=issuer, authorization_endpoint=issuer + "/authorize",
            token_endpoint=issuer + "/token", jwks_uri=issuer + "/jwks",
            response_types_supported=["code"], grant_types_supported=["authorization_code"],
            subject_types_supported=["public"], id_token_signing_alg_values_supported=["RS256"],
            scopes_supported=["openid"], code_challenge_methods_supported=["S256"],
            token_endpoint_auth_methods_supported=["client_secret_basic"],
        )

    @bp.get("/jwks")
    def jwks():
        return jsonify(keys=[key.as_dict(private=False)])

    @bp.get("/authorize")
    def authorize():
        oauth_request = auth.create_oauth2_request(None)
        subject = current_user.get_id()
        try:
            if (request.args.get("scope") != "openid" or not request.args.get("state")
                    or not 1 <= len(request.args.get("nonce", "")) <= 128):
                raise InvalidRequestError("OpenID scope, state and nonce are required")
            grant = auth.get_consent_grant(oauth_request, end_user=subject)
        except OAuth2Error as error:
            return auth.handle_error_response(oauth_request, error)
        if not subject:
            return redirect(url_for("users.auth.login", next=request.full_path))
        marker = session.get(SESSION_MARKER)
        if session.get("hallinto_owner_revocation_pending") or not marker or not _live_marker(db, markers, _digest(marker), subject):
            if request.args.get("relogin"):
                return jsonify(error="access_denied", error_description="Sign out and sign in again"), 403
            return _relogin_page(subject)
        return auth.create_authorization_response(oauth_request, grant_user=subject, grant=grant)

    def _relogin_page(subject):
        """Sign the visitor out and back in by themselves, then return here.

        The sign-out form carries its own token, so only this signed-in
        browser can submit it; the sign-in returns to the authorization URL
        once. A second failure shows the plain error instead of looping.
        """
        from mielenosoitukset_fi.utils.logout_token import make_logout_token

        back = request.full_path.rstrip("?") + ("&" if "?" in request.full_path.rstrip("?") else "?") + "relogin=1"
        page = (
            "<!doctype html><html lang=\"fi\"><meta charset=\"utf-8\"><title>Kirjaudutaan uudelleen</title>"
            "<body><p>Kirjaudutaan uudelleen…</p>"
            f"<form id=\"f\" method=\"post\" action=\"{url_for('users.auth.logout')}\">"
            f"<input type=\"hidden\" name=\"logout_token\" value=\"{make_logout_token(subject)}\">"
            f"<input type=\"hidden\" name=\"next\" value=\"{escape(back)}\">"
            "<noscript><button type=\"submit\">Jatka</button></noscript></form>"
            "<script src=\"" + url_for("static", filename="js/hallinto-relogin.js") + "\"></script></body></html>"
        )
        response = make_response(page, 403)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    @bp.post("/token")
    def token():
        request.max_content_length = 4096
        return auth.create_token_response()

    def client_payload(limit=4096):
        """Authenticate the Hallinto client and read its JSON object body."""
        request.max_content_length = limit
        credentials = request.authorization
        if (not credentials or credentials.type != "basic"
                or unquote_plus(credentials.username or "") != client.client_id
                or not client.check_client_secret(unquote_plus(credentials.password or ""))):
            return None, (jsonify(error="invalid_client"), 401, {"WWW-Authenticate": "Basic"})
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return None, (jsonify(error="invalid_request"), 400)
        return payload, None

    @bp.post("/session")
    def owner_session():
        payload, error = client_payload()
        if error:
            return error
        return jsonify(active=bool(_live_marker(db, markers, payload.get("sid"), payload.get("sub"))))

    def admin_payload(limit=4096):
        """Require the client and a live Hallinto sign-in of an admin account."""
        payload, error = client_payload(limit)
        if error:
            return None, error
        if not _live_marker(db, markers, payload.get("sid"), payload.get("sub")):
            return None, (jsonify(error="access_denied"), 403)
        actor = db.users.find_one({"_id": ObjectId(payload["sub"])},
                                  {"role": 1, "global_admin": 1, "global_permissions": 1, "role_bundle": 1})
        # An expired role bundle no longer makes the account an admin.
        from mielenosoitukset_fi.utils.role_bundle_expiry import effective_role_permissions
        role = effective_role_permissions(actor, "user")[0] if actor else None
        if not actor or not (actor.get("global_admin") or role in _ACCOUNT_LOOKUP_ROLES):
            return None, (jsonify(error="access_denied"), 403)
        return payload, None

    @bp.post("/moderation-blocks/sync")
    def moderation_blocks_sync():
        """Store the organizer bans and publishing precedents Hallinto recorded.

        Hallinto decides; this site enforces. Needs a signed-in admin, and
        storing by id makes repeating it harmless."""
        from mielenosoitukset_fi.utils import moderation_blocks

        payload, error = admin_payload(256 * 1024)
        if error:
            return error
        try:
            stored = moderation_blocks.sync(db, payload.get("items"))
        except ValueError:
            return jsonify(error="invalid_request"), 400
        return jsonify(stored=stored)

    @bp.post("/city-admin-applications/list")
    def city_admin_applications_list():
        """City admin applications for OT's handling in Hallinto. Hallinto
        decides who may handle them; this only needs a signed-in admin."""
        from mielenosoitukset_fi.utils import city_admin_applications

        payload, error = admin_payload()
        if error:
            return error
        return jsonify(items=city_admin_applications.hallinto_list(db))

    @bp.post("/city-admin-applications/update")
    def city_admin_applications_update():
        """Record a handling step from Hallinto: the status the applicant sees
        and the Hallinto case. Nothing here grants a role."""
        from mielenosoitukset_fi.utils import city_admin_applications

        payload, error = admin_payload()
        if error:
            return error
        application_id, status, case = payload.get("id"), payload.get("status"), payload.get("case")
        if (not isinstance(application_id, str) or not _ACCOUNT_ID.fullmatch(application_id)
                or status not in city_admin_applications.HALLINTO_STATUSES
                or not isinstance(case, str) or not 0 < len(case) <= 128):
            return jsonify(error="invalid_request"), 400
        try:
            row = city_admin_applications.hallinto_update(db, ObjectId(application_id), status, case, payload["sub"])
        except city_admin_applications.HallintoConflict:
            return jsonify(error="conflict"), 409
        if row is None:
            return jsonify(error="not_found"), 404
        users = {u["_id"]: u for u in db.users.find({"_id": row.get("user_id")}, {"username": 1, "displayname": 1})}
        return jsonify(item=city_admin_applications.hallinto_view(row, users))

    @bp.post("/accounts/search")
    def account_search():
        payload, error = admin_payload()
        if error:
            return error
        query = payload.get("q")
        if not isinstance(query, str) or not 3 <= len(query.strip()) <= 64:
            return jsonify(error="invalid_request"), 400
        prefix = "^" + re.escape(query.strip())
        rows = db.users.find(
            {"$or": [{"username": {"$regex": prefix, "$options": "i"}},
                     {"displayname": {"$regex": prefix, "$options": "i"}}]},
            _ACCOUNT_FIELDS,
        ).sort("username", 1).limit(_ACCOUNT_SEARCH_LIMIT)
        return jsonify(items=[_account_view(row) for row in rows])

    @bp.post("/accounts/lookup")
    def account_lookup():
        payload, error = admin_payload()
        if error:
            return error
        ids = payload.get("ids")
        if (not isinstance(ids, list) or not 0 < len(ids) <= _ACCOUNT_LOOKUP_LIMIT
                or not all(isinstance(i, str) and _ACCOUNT_ID.fullmatch(i) for i in ids)):
            return jsonify(error="invalid_request"), 400
        rows = db.users.find({"_id": {"$in": [ObjectId(i) for i in set(ids)]}}, _ACCOUNT_FIELDS)
        return jsonify(items=[_account_view(row) for row in rows])

    @bp.post("/accounts/invite")
    def account_invite():
        """Create an ordinary account for a person who has none and email
        them a link to set their password. The admin signed in to Hallinto
        is recorded as the creator."""
        from mielenosoitukset_fi.admin.user_history import (
            PENDING_HISTORY, flush_user_history, history_event, user_snapshot)

        payload, error = admin_payload()
        if error:
            return error
        actor_doc = db.users.find_one({"_id": ObjectId(payload["sub"])})
        actor = User.from_db(actor_doc)
        if not (actor.has_full_permissions() or "CREATE_USER" in (actor.global_permissions or [])):
            return jsonify(error="access_denied"), 403
        email, username, name = (payload.get(k) for k in ("email", "username", "name"))
        if not all(isinstance(v, str) for v in (email, username)) or not isinstance(name or "", str):
            return jsonify(error="invalid_request"), 400
        email, username = normalize_email(email), normalize_username(username)
        name = (name or "").strip() or username
        if (len(email) > 254 or not valid_email(email) or not validate_username(username)[0]
                or len(name) > 64 or is_reserved_identity_name(name)):
            return jsonify(error="invalid_request"), 400
        existing = db.users.find_one({"email_canonical": email}, _ACCOUNT_FIELDS) or db.users.find_one(
            {"email": {"$regex": "^" + re.escape(email) + "$", "$options": "i"}}, _ACCOUNT_FIELDS)
        if existing:
            return jsonify(error="email_in_use", account=_account_view(existing)), 409
        if db.users.find_one({"$or": [{"username_canonical": username},
                                      {"username": {"$regex": "^" + re.escape(username) + "$", "$options": "i"}}]},
                             {"_id": 1}):
            return jsonify(error="username_in_use"), 409

        user_doc = User.create_user(username, secrets.token_urlsafe(48), email, name)
        user_doc.update({"_id": ObjectId(), "active": True, "created_via": "hallinto"})
        creator = {"id": str(actor._id), "username": actor.username, "role": actor.role, "via": "hallinto"}
        user_doc[PENDING_HISTORY] = [history_event(user_doc["_id"], {}, user_snapshot(user_doc, {}),
                                                   creator, "user_created")]
        try:
            db.users.insert_one(user_doc)
        except DuplicateKeyError:
            return jsonify(error="username_in_use"), 409
        flush_user_history(db, user_doc["_id"])

        reset_url = f"{origin.scheme}://{origin.netloc}" + url_for(
            "users.auth.password_reset", token=generate_reset_token(email))
        try:
            invite_sender().queue_email(
                template_name="hallinto_account_invite.html",
                subject="Sinulle on luotu tili mielenosoitukset.fi-palveluun",
                recipients=[email],
                context={"user_name": name, "username": username, "reset_url": reset_url,
                         "reset_request_url": f"{origin.scheme}://{origin.netloc}"
                                              + url_for("users.auth.password_reset_request")},
            )
            sent = True
        except Exception:  # The account exists; the person can still use "forgot password".
            logging.getLogger(__name__).exception("Hallinto account invite email failed")
            sent = False
        return jsonify(account=_account_view(user_doc), invited=sent), 201

    @bp.post("/moderation-reviews/list")
    def moderation_reviews_list():
        """Rejections and take-downs waiting for OT review, then recent closed
        ones. Hallinto decides who may review; this needs a signed-in admin."""
        from mielenosoitukset_fi.utils import moderation_reviews

        payload, error = admin_payload()
        if error:
            return error
        base = f"{origin.scheme}://{origin.netloc}"
        items = moderation_reviews.list_reviews(db)
        for item in items:
            item["link"] = base + url_for("admin_demo.open_demo_decision", demo_id=item["demo"]["id"])
        return jsonify(items=items)

    @bp.post("/moderation-reviews/update")
    def moderation_reviews_update():
        """Record one OT step: confirm, overturn (publishes the demonstration),
        needs_info, or reconsider (a request that arrived on ``received_on``).
        The signed-in account is recorded; the submitter is emailed."""
        from mielenosoitukset_fi.demonstrations.decisions import apply_demo_decision
        from mielenosoitukset_fi.utils import moderation_reviews

        payload, error = admin_payload()
        if error:
            return error
        review_id, action, message = payload.get("id"), payload.get("action"), payload.get("message")
        received = payload.get("received_on")
        if (not isinstance(review_id, str) or not _ACCOUNT_ID.fullmatch(review_id)
                or action not in ("confirm", "overturn", "needs_info", "reconsider")
                or not isinstance(message or "", str) or not isinstance(received or "", str)):
            return jsonify(error="invalid_request"), 400
        received_on = None
        if received:
            try:
                day = datetime.strptime(received, "%Y-%m-%d")
            except ValueError:
                return jsonify(error="invalid_request"), 400
            received_on = (day.replace(tzinfo=moderation_reviews.HELSINKI)
                           .astimezone(UTC).replace(tzinfo=None))
        account = db.users.find_one({"_id": ObjectId(payload["sub"])}, {"username": 1}) or {}
        actor = {"id": payload["sub"], "name": account.get("username") or payload["sub"]}
        sender = invite_sender()

        row = db[moderation_reviews.COLLECTION].find_one(
            {"_id": ObjectId(review_id)}, {"demo_id": 1}) or {}
        public_url = (f"{origin.scheme}://{origin.netloc}"
                      + url_for("demonstration_detail", demo_id=str(row["demo_id"]))
                      if row.get("demo_id") else None)

        def publish(demo_id, by):
            apply_demo_decision(
                db, demo_id, "approved",
                actor={"user_id": by["id"], "username": by["name"], "via": "hallinto_review"},
                source="hallinto_review", email_sender=sender, notify_submitter=False,
                public_url=public_url,
            )

        try:
            item = moderation_reviews.record(
                db, review_id, action, actor=actor, message=message, received_on=received_on,
                email_sender=sender, public_url=public_url, apply_decision=publish,
            )
        except moderation_reviews.ReviewError as exc:
            return jsonify(error=exc.code), exc.status
        return jsonify(item=item)

    def _org_request_view(row):
        org = row.get("organization") or {}
        applicant = row.get("applicant") or {}
        stamp = lambda value: value.replace(tzinfo=UTC).isoformat() if value else None
        return {
            "id": str(row["_id"]), "status": row.get("status"),
            "organization": {**{key: org.get(key) for key in ("name", "email", "links", "business_id", "description")},
                             # The city's display name; the logo's bytes stay in Flask until O4 copies them.
                             "city": CITY_KEY_TO_NAME.get(org.get("city"), org.get("city")) if org.get("city") else None,
                             "has_logo": bool(row.get("logo"))},
            "applicant": {"name": applicant.get("name"), "email": applicant.get("email"), "role": applicant.get("role"),
                          "account": str(applicant["user_id"]) if applicant.get("user_id") else None},
            "represents": bool(row.get("represents")), "access": row.get("access"),
            "invite_email": row.get("invite_email"), "onboarding": bool(row.get("onboarding")),
            "status_message": row.get("status_message"), "case": row.get("hallinto_case"),
            "organization_id": str(row["organization_id"]) if row.get("organization_id") else None,
            "owner_state": row.get("owner_state"), "logo_state": row.get("logo_state"),
            "created_at": stamp(row.get("created_at")), "updated_at": stamp(row.get("updated_at")),
        }

    @bp.post("/organization-requests/list")
    def organization_requests_list():
        """Requests whose applicant confirmed the address, newest first. Hallinto
        decides who may handle them; this only needs a signed-in admin account."""
        payload, error = admin_payload()
        if error:
            return error
        rows = db.organization_requests.find(
            {"status": {"$in": list(_ORG_REQUEST_STATUSES)}},
            {"status_token_hash": 0, "logo.data": 0},
        ).sort("created_at", -1).limit(100)
        return jsonify(items=[_org_request_view(row) for row in rows])

    @bp.post("/organization-requests/update")
    def organization_requests_update():
        """Record a handling step from Hallinto: the status the applicant sees, an
        optional message, the Hallinto case and a changed access choice. Nothing here
        creates an organization or grants access."""
        payload, error = admin_payload()
        if error:
            return error
        request_id, status, message = payload.get("id"), payload.get("status"), payload.get("message")
        case, access, invite_email = payload.get("case"), payload.get("access"), payload.get("invite_email")
        if (not isinstance(request_id, str) or not _ACCOUNT_ID.fullmatch(request_id)
                or status not in _ORG_REQUEST_STATUSES or status == "received"
                or not isinstance(message or "", str) or len(message or "") > 1000
                or not isinstance(case or "", str) or len(case or "") > 64
                or access not in (None, "organization_account", "own_account", "invite")
                or not isinstance(invite_email or "", str)):
            return jsonify(error="invalid_request"), 400
        update = {"status": status, "updated_at": utcnow(), "case_pending": False}
        if message is not None:
            update["status_message"] = message.strip()
        if case:
            update["hallinto_case"] = case
        if access:
            if access == "invite":
                invite_email = normalize_email(invite_email or "")
                if len(invite_email) > 254 or not valid_email(invite_email):
                    return jsonify(error="invalid_request"), 400
                update["invite_email"] = invite_email
            update["access"] = access
        row = db.organization_requests.find_one_and_update(
            {"_id": ObjectId(request_id), "status": {"$in": list(_ORG_REQUEST_STATUSES)},
             **({"represents": True} if access else {})},
            {"$set": update, "$push": {"history": {"status": status, "at": update["updated_at"],
                                                   "by": payload["sub"], "access": access}}},
            projection={"status_token_hash": 0, "logo.data": 0}, return_document=True,
        )
        if not row:
            return jsonify(error="not_found"), 404
        return jsonify(item=_org_request_view(row))

    @bp.post("/organization-requests/create-organization")
    def organization_requests_create_organization():
        """Carry out an approved request (slice O4): create the organization, copy
        the logo and give the owner access as the request's access choice says.
        Idempotent: a repeat finishes the steps that are missing. Hallinto has
        decided; this only needs the request to be approved by the named case."""
        from flask import current_app

        from mielenosoitukset_fi.utils.org_request_owner import OwnerError, create_for_request
        from mielenosoitukset_fi.utils.s3 import upload_image_fileobj

        payload, error = admin_payload()
        if error:
            return error
        actor = User.from_db(db.users.find_one({"_id": ObjectId(payload["sub"])}))
        if not (actor.has_full_permissions() or "CREATE_ORGANIZATION" in (actor.global_permissions or [])):
            return jsonify(error="access_denied"), 403
        request_id, case = payload.get("id"), payload.get("case")
        if (not isinstance(request_id, str) or not _ACCOUNT_ID.fullmatch(request_id)
                or not isinstance(case, str) or not 0 < len(case) <= 64):
            return jsonify(error="invalid_request"), 400
        row = db.organization_requests.find_one({"_id": ObjectId(request_id)}, {"status_token_hash": 0})
        if not row:
            return jsonify(error="not_found"), 404
        base = f"{origin.scheme}://{origin.netloc}"

        def send_invite(email, context):
            invite_sender().queue_email(
                template_name="invite_to_organization.html",
                subject=f"Kutsu liittyä organisaatioon {context['organization_name']}",
                recipients=[email],
                context={"invite_url": base + url_for("users.user_orgs.accept_invite",
                                                      organization_id=context["organization_id"]),
                         "inviter_name": "Mielenosoitukset.fi",
                         "organization_name": context["organization_name"]})

        def send_account_invite(user_doc, email):
            invite_sender().queue_email(
                template_name="hallinto_account_invite.html",
                subject="Organisaatiollesi on luotu tili mielenosoitukset.fi-palveluun",
                recipients=[email],
                context={"user_name": user_doc.get("displayname") or user_doc["username"],
                         "username": user_doc["username"],
                         "reset_url": base + url_for("users.auth.password_reset", token=generate_reset_token(email)),
                         "reset_request_url": base + url_for("users.auth.password_reset_request")})

        try:
            result = create_for_request(
                db, row, actor_id=payload["sub"], case=case, now=utcnow(),
                send_invite=send_invite, send_account_invite=send_account_invite,
                upload=upload_image_fileobj, bucket=current_app.config.get("S3_BUCKET"))
        except OwnerError as failure:
            return jsonify(error=failure.code), failure.status
        return jsonify(**result)

    app.register_blueprint(bp)
