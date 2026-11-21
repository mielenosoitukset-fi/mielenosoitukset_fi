"""Role bundles expire on read (Hallinto role bundles, no cron).

A bundle written by ``admin/role_bundles.py`` stores ``role_bundle`` on the user
with the role and permissions it set and an exclusive ``valid_until``. Once that
instant has passed, or the bundle was ended, the account no longer holds what
the bundle gave. The stored fields stay until the next write; every load goes
through ``effective_role_permissions`` so the power is gone at once.
"""

from __future__ import annotations

from typing import Optional

from mielenosoitukset_fi.utils.time_utils import utcnow


def bundle_expired(bundle: Optional[dict], now=None) -> bool:
    end = (bundle or {}).get("valid_until")
    return bool(end is not None and end <= (now or utcnow()))


def subtract_bundle(role, permissions, bundle: dict):
    """Role and permissions of the account without what the bundle gave."""
    given = set(bundle.get("permissions") or [])
    remaining = [p for p in (permissions or []) if p not in given]
    if role == bundle.get("role") and role not in {None, "user"}:
        role = "user"
    return role, remaining


def effective_role_permissions(doc: dict, default_role: str, now=None):
    """(role, permissions) as the account holds them now."""
    role = doc.get("role", default_role)
    permissions = doc.get("global_permissions", [])
    bundle = doc.get("role_bundle")
    if bundle and not bundle.get("ended") and bundle_expired(bundle, now):
        role, permissions = subtract_bundle(role, permissions, bundle)
    return role, permissions
