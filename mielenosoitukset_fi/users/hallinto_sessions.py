"""Server-side markers owned by Flask for individual Hallinto logins."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from secrets import token_urlsafe

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow


@dataclass(frozen=True)
class AccountVersion:
    """A snapshot supplied by the authentication owner after live validation."""

    user_id: ObjectId
    auth_epoch: int
    credential_version: str


def _marker_digest(marker: str) -> str:
    return sha256(marker.encode("ascii")).hexdigest()


def _marker_filter(marker: str, user_id: ObjectId) -> dict:
    return {"_id": _marker_digest(marker), "user_id": user_id}


def issue_marker(markers, account: AccountVersion, expires_at: datetime) -> str:
    """Insert a new marker for a validated owner session using the repository’s naive UTC convention."""
    now = utcnow()
    if expires_at.tzinfo is not None or expires_at <= now:
        raise ValueError("Hallinto login expiry must be a future naive UTC time")
    marker = token_urlsafe(32)
    markers.insert_one(
        {
            **_marker_filter(marker, account.user_id),
            "auth_epoch": account.auth_epoch,
            "credential_version": account.credential_version,
            "issued_at": now,
            "expires_at": expires_at,
            "revoked_at": None,
        }
    )
    return marker


def marker_is_live(markers, marker: str, account: AccountVersion | None) -> bool:
    """Require both an active marker and a freshly validated owner account."""
    if account is None or not _valid_marker(marker):
        return False
    return markers.find_one(
        {
            **_marker_filter(marker, account.user_id),
            "auth_epoch": account.auth_epoch,
            "credential_version": account.credential_version,
            "expires_at": {"$gt": utcnow()},
            "revoked_at": None,
        },
        {"_id": 1},
    ) is not None


def revoke_marker(markers, marker: str, user_id: ObjectId) -> None:
    """Revoke one browser's marker without recreating missing records."""
    if _valid_marker(marker):
        markers.update_one(
            {**_marker_filter(marker, user_id), "revoked_at": None},
            {"$set": {"revoked_at": utcnow()}},
        )


def _valid_marker(marker: str) -> bool:
    return isinstance(marker, str) and len(marker) == 43 and marker.isascii()
