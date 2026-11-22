"""The organisation right to approve its own demonstrations (decision Olivia, 22 Nov 2026).

An organisation right, not a person's right. Hallinto grants and ends it
(``admin/org_rights.py``); this module only reads it. The record lives at
``organizations.rights.approve_own_demos``::

    {grant_id, case_id, decision_id, decision_revision, granted_at, granted_by,
     valid_until, status, ended_at, ended_reason}

``status`` is ``live``, ``revoked``, ``frozen`` or ``given_up`` as written; the
effective status also looks at the organisation and the clock (``unverified``,
``expired``). While the right is live the organisation's admins and owners
(``org_admin`` role holders; membership roles until their cutover) may decide
the ``new_demo`` case of a demonstration whose organisers all hold a live
right. Nobody gets a permission or a role from it. Off unless
``ORG_APPROVAL_RIGHT_ENABLED`` is true.
"""

from __future__ import annotations

from typing import Any, Optional

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

RIGHT = "approve_own_demos"
CASE_TYPE = "organization_right_request"
LIVE = "live"
MANAGER_ROLES = {"admin", "owner"}
END_REASONS = {"revoked": "hallinto_revoked", "frozen": "hallinto_frozen",
               "given_up": "owner_gave_up", "unverified": "unverified"}


def enabled() -> bool:
    from flask import current_app, has_app_context

    return bool(has_app_context() and current_app.config.get("ORG_APPROVAL_RIGHT_ENABLED") is True)


def _db():
    from mielenosoitukset_fi.database_manager import DatabaseManager

    return DatabaseManager.get_instance().get_db()


def _oid(value) -> Optional[ObjectId]:
    return ObjectId(str(value)) if value is not None and ObjectId.is_valid(str(value)) else None


def record(organization: Optional[dict]) -> Optional[dict]:
    rights = (organization or {}).get("rights")
    row = rights.get(RIGHT) if isinstance(rights, dict) else None
    return row if isinstance(row, dict) else None


def status_of(organization: Optional[dict], now=None) -> str:
    """live | expired | revoked | frozen | given_up | unverified | none."""
    row = record(organization)
    if row is None:
        return "none"
    stored = row.get("status")
    if stored != LIVE:
        return stored if stored in {"revoked", "frozen", "given_up", "unverified"} else "none"
    if organization.get("verified") is not True:
        return "unverified"
    end = row.get("valid_until")
    if end is None or end <= (now or utcnow()):
        return "expired"
    return LIVE


def is_live(organization: Optional[dict], now=None) -> bool:
    from mielenosoitukset_fi.utils.system_teams import protected_organization

    if not organization or protected_organization(organization["_id"]):
        return False
    return status_of(organization, now) == LIVE


def organizer_organization_ids(demo: Any) -> Optional[list]:
    """The organisations behind a demonstration; None when any organiser is not one."""
    organizers = (demo.get("organizers") if isinstance(demo, dict) else getattr(demo, "organizers", None)) or []
    ids = []
    for organizer in organizers:
        value = organizer.get("organization_id") if isinstance(organizer, dict) else getattr(organizer, "organization_id", None)
        oid = _oid(value)
        if oid is None:
            return None
        ids.append(oid)
    return ids or None


def managed_organization_ids(user) -> set:
    """Organisations where the account is owner or admin (a Hallinto grant counts)."""
    try:
        memberships = list(getattr(user, "memberships", None) or [])
    except Exception:  # noqa: BLE001 - a broken lookup never grants anything
        return set()
    return {oid for m in memberships if getattr(m, "role", None) in MANAGER_ROLES
            for oid in [_oid(getattr(m, "organization_id", None))] if oid is not None}


def rights_holders(demo: Any, db=None) -> list:
    """Organisation documents behind the demonstration, when every one holds a live right; else []."""
    ids = organizer_organization_ids(demo)
    if not ids:
        return []
    db = db if db is not None else _db()
    rows = {row["_id"]: row for row in db.organizations.find({"_id": {"$in": ids}})}
    if set(ids) - set(rows) or not all(is_live(rows[oid]) for oid in ids):
        return []
    return [rows[oid] for oid in dict.fromkeys(ids)]


def user_may_approve(user, demo: Any) -> bool:
    """May the account decide this demonstration because of its organisations' right?"""
    if not enabled() or not getattr(user, "is_authenticated", False):
        return False
    managed = managed_organization_ids(user)
    if not managed:
        return False
    try:
        holders = rights_holders(demo)
    except Exception:  # noqa: BLE001
        return False
    return any(org["_id"] in managed for org in holders)


def user_holds_any_right(user) -> bool:
    """Does the account administer an organisation that holds a live right (for menus)?"""
    if not enabled() or not getattr(user, "is_authenticated", False):
        return False
    managed = managed_organization_ids(user)
    if not managed:
        return False
    try:
        return any(is_live(org) for org in _db().organizations.find({"_id": {"$in": list(managed)}}))
    except Exception:  # noqa: BLE001
        return False


def approving_names(user, demo: Any) -> list:
    """Names of the organisations whose right lets the account decide (for the case history)."""
    managed = managed_organization_ids(user)
    return [org.get("name") or "" for org in rights_holders(demo) if org["_id"] in managed]


def end_right(db, organization_id, status: str, *, actor: str = "järjestelmä") -> bool:
    """Flask-side end of a live right (owner gives it up, organisation loses verified). True when ended."""
    if status not in ("given_up", "unverified"):
        raise ValueError("unknown end status")
    now = utcnow()
    result = db.organizations.update_one(
        {"_id": organization_id, f"rights.{RIGHT}.status": LIVE},
        {"$set": {f"rights.{RIGHT}.status": status, f"rights.{RIGHT}.ended_at": now,
                  f"rights.{RIGHT}.ended_reason": END_REASONS[status]}},
    )
    return bool(result.modified_count)
