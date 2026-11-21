"""Owner operation for approved organization requests (slice O4).

Hallinto decides a request; Flask, which owns accounts and organizations,
carries the decision out. Everything here is idempotent: a retry after a
partial failure finishes the remaining steps and never creates a second
organization, membership or invitation. The request row remembers each step
(`organization_id`, `owner_state`), so the same call can be repeated safely.

Nothing here decides anything. The caller has checked that the request is
approved by the Hallinto case named in the call.
"""

from __future__ import annotations

import io
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

from bson import ObjectId

from mielenosoitukset_fi.utils.validators import normalize_email, normalize_username, validate_username

logger = logging.getLogger(__name__)

OWNER_ROLE = "owner"
ACCOUNT_KIND = "organization"


class OwnerError(Exception):
    """A step that cannot continue; `code` is the stable error name."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def _username_for(db, name: str) -> str:
    """A free, valid username derived from the organization's name."""
    base = re.sub(r"[^a-z0-9]+", "-", normalize_username(name).translate(
        str.maketrans("äöåé", "aaoe"))).strip("-")[:24] or "organisaatio"
    for attempt in range(50):
        candidate = base if attempt == 0 else f"{base[:24]}-{secrets.randbelow(9000) + 1000}"
        if validate_username(candidate)[0] and not db.users.find_one(
                {"$or": [{"username_canonical": candidate}, {"username": candidate}]}, {"_id": 1}):
            return candidate
    raise OwnerError("username_in_use")


def _logo_url(db, row: dict, upload: Callable[..., str | None], bucket: str | None) -> tuple[str | None, str]:
    """Copy the request's logo to the organization store: (url, state).

    The state is `none` (no logo), `copied` or `pending` (the store was not
    available; the logo stays on the request and a retry copies it).
    """
    stored = db.organization_requests.find_one({"_id": row["_id"]}, {"logo": 1}) or {}
    logo = stored.get("logo")
    if not logo or not logo.get("data"):
        return None, "none"
    if not bucket:
        return None, "pending"
    try:
        url = upload(bucket, io.BytesIO(bytes(logo["data"])), "logo.png", "organization_logos")
    except Exception:
        logger.exception("Organization logo copy failed")
        return None, "pending"
    return (url, "copied") if url else (None, "pending")


def _membership(db, org_id: ObjectId, user_id: ObjectId) -> None:
    """Make the account an owner of the organization; a repeat changes nothing."""
    from mielenosoitukset_fi.utils.classes.Organization import Organization
    from mielenosoitukset_fi.users.BPs.orgs import _grant_admin_panel
    from mielenosoitukset_fi.users.models import User

    org = Organization.from_dict(db.organizations.find_one({"_id": org_id}))
    if org.is_member(user_id=user_id):
        return
    org.add_member(user_id, role=OWNER_ROLE, permissions=[])
    user = User.from_db(db.users.find_one({"_id": user_id}))
    _grant_admin_panel(user, OWNER_ROLE)


def create_for_request(
    db,
    row: dict,
    *,
    actor_id: str,
    case: str,
    now: datetime,
    send_invite: Callable[[str, dict], None],
    send_account_invite: Callable[[dict, str], None],
    upload: Callable[..., str | None],
    bucket: str | None,
) -> dict[str, Any]:
    """Create the organization of an approved request and its owner access.

    `send_invite(email, context)` mails an organization invitation and
    `send_account_invite(user, email)` mails a new account's password link.
    Returns `{organization_id, access, logo, owner_state}`.
    """
    from mielenosoitukset_fi.users.models import User

    organization = row.get("organization") or {}
    access = row.get("access")
    applicant = row.get("applicant") or {}
    if not row.get("represents") or row.get("status") != "approved" or row.get("hallinto_case") != case:
        raise OwnerError("not_approved")
    if access not in ("organization_account", "own_account", "invite"):
        raise OwnerError("access_missing")
    org_email = normalize_email(organization.get("email") or "")
    invite_email = normalize_email(row.get("invite_email") or "")
    if access == "invite" and not invite_email:
        raise OwnerError("invite_email_missing")
    if access == "organization_account" and not org_email:
        raise OwnerError("organization_email_missing")
    owner_user = None
    if access == "own_account":
        user_id = applicant.get("user_id")
        owner_user = db.users.find_one({"_id": user_id}) if user_id else None
        if not owner_user:
            raise OwnerError("applicant_account_missing")
    if access == "organization_account":
        taken = db.users.find_one({"email_canonical": org_email}, {"_id": 1}) or db.users.find_one(
            {"email": {"$regex": "^" + re.escape(org_email) + "$", "$options": "i"}}, {"_id": 1})
        existing_owner = db.organizations.find_one({"_id": row.get("organization_id")}) if row.get("organization_id") else None
        if taken and not (existing_owner and row.get("owner_user_id") == taken["_id"]):
            # The address already has an account; Hallinto switches the access choice.
            raise OwnerError("email_in_use")

    org_id = row.get("organization_id")
    created = False
    if not org_id:
        # Claim the request first, so two calls at once make one organization.
        claim = ObjectId()
        claimed = db.organization_requests.update_one(
            {"_id": row["_id"], "organization_id": {"$exists": False}},
            {"$set": {"organization_id": claim, "updated_at": now}})
        if claimed.modified_count:
            org_id, created = claim, True
        else:
            org_id = (db.organization_requests.find_one({"_id": row["_id"]}, {"organization_id": 1}) or {}).get("organization_id")
            if not org_id:
                raise OwnerError("organization_missing")
    stored = db.organizations.find_one({"_id": org_id})
    if not stored:
        # First time, or a call that stopped between claiming and inserting.
        url, logo_state = _logo_url(db, row, upload, bucket)
        links = organization.get("links") or []
        db.organizations.update_one({"_id": org_id}, {"$setOnInsert": {
            "name": organization.get("name"), "description": organization.get("description") or "",
            "email": organization.get("email"), "website": links[0] if links else "",
            "social_media_links": {}, "members": [], "invitations": [], "verified": True,
            # Only fields Organization.from_dict accepts: anything else breaks every loader.
            # The request row links back through `organization_id`; the city stays on it too.
            **({"logo": url} if url else {}),
        }}, upsert=True)
        db.organization_requests.update_one({"_id": row["_id"]}, {"$set": {"logo_state": logo_state}})
        row = {**row, "logo_state": logo_state}
    elif row.get("logo_state") == "pending" and not stored.get("logo"):
        url, logo_state = _logo_url(db, row, upload, bucket)
        if url:
            db.organizations.update_one({"_id": org_id}, {"$set": {"logo": url}})
            db.organization_requests.update_one({"_id": row["_id"]}, {"$set": {"logo_state": logo_state}})
            row = {**row, "logo_state": logo_state}

    owner_state = row.get("owner_state")
    if access == "own_account":
        _membership(db, org_id, owner_user["_id"])
        owner_state = "member"
    elif access == "organization_account":
        user_id = row.get("owner_user_id")
        if not user_id:
            username = _username_for(db, organization.get("name") or "organisaatio")
            user_doc = User.create_user(username, secrets.token_urlsafe(48), org_email,
                                        (organization.get("name") or username)[:64])
            user_doc.update({"_id": ObjectId(), "active": True, "created_via": "organization_request",
                             "account_kind": ACCOUNT_KIND})
            db.users.insert_one(user_doc)
            user_id = user_doc["_id"]
            db.organization_requests.update_one({"_id": row["_id"]}, {"$set": {"owner_user_id": user_id}})
            send_account_invite(user_doc, org_email)
        _membership(db, org_id, user_id)
        owner_state = "account"
    else:
        org_doc = db.organizations.find_one({"_id": org_id}, {"invitations": 1, "name": 1})
        pending = [i for i in org_doc.get("invitations", []) if isinstance(i, dict) and i.get("email") == invite_email]
        if not pending and owner_state != "invited":
            db.organizations.update_one({"_id": org_id}, {"$push": {"invitations": {
                "email": invite_email, "invited_at": now, "role": OWNER_ROLE,
                # The Hallinto decision is the grant; Flask's role check reads this marker.
                "role_granted_by": f"hallinto:{case}"}}})
            send_invite(invite_email, {"organization_id": str(org_id), "organization_name": org_doc.get("name")})
        owner_state = "invited"
    db.organization_requests.update_one(
        {"_id": row["_id"]},
        {"$set": {"owner_state": owner_state, "updated_at": now},
         "$push": {"history": {"status": "organization_ready" if created else "organization_retry",
                               "at": now, "by": actor_id, "access": access}}})
    return {"organization_id": str(org_id), "access": access, "owner_state": owner_state,
            "logo": row.get("logo_state") or "none"}
