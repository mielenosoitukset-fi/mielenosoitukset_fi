"""Expiry rules for organization invitations."""

from datetime import datetime, timedelta, timezone

INVITE_LIFETIME = timedelta(days=30)


def invitation_expired(invitation, now=None) -> bool:
    """True when an invitation carries ``invited_at`` older than 30 days.

    Legacy invitations (plain e-mail strings, or dicts without ``invited_at``)
    never expire, so nothing sent before this field existed stops working.
    """
    if not isinstance(invitation, dict):
        return False
    expires_at = invitation.get("expires_at")
    if isinstance(expires_at, datetime):
        # A decided recovery carries its own, shorter lifetime.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) > expires_at
    invited_at = invitation.get("invited_at")
    if not isinstance(invited_at, datetime):
        return False
    if invited_at.tzinfo is None:
        invited_at = invited_at.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - invited_at > INVITE_LIFETIME


INVITE_ROLES = ("member", "admin", "owner")


def invited_role(invitation) -> str:
    """Role an accepted invitation grants.

    Only a dict invitation whose role was recorded with ``role_granted_by`` (set by
    the role check in ``set_invite_role``) grants ``admin`` or ``owner``. Older
    invitations carry roles that any member could set, so they join as ``member``.
    """
    if not isinstance(invitation, dict):
        return "member"
    role = invitation.get("role")
    if role in INVITE_ROLES and invitation.get("role_granted_by"):
        return role
    return "member"


def invitation_for(invitations, email):
    """The live invitation for ``email``, or None. A decided recovery wins over a plain one."""
    found = None
    for invitation in invitations or []:
        if isinstance(invitation, str) and invitation == email:
            found = found or invitation
        if (
            isinstance(invitation, dict)
            and invitation.get("email") == email
            and not invitation_expired(invitation)
        ):
            if invitation.get("recovery_request_id"):
                return invitation
            found = found or invitation
    return found
