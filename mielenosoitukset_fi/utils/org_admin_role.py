"""The ``org_admin`` account role: organisation admin rights given by Hallinto.

Whoever is owner or admin of an organisation gets this role automatically
(``admin/org_admin_grants.py``, template ``org_admin_v1``): ``users.role =
"org_admin"`` and one ``admin_scope_grants`` row per organisation
(``scope_type: "organization"``). The grant reaches only that organisation.
Hallinto can only end a grant.

Every organisation check in Flask reads ``User.memberships``. A live grant
therefore shows up there as the membership role ``admin`` with the grant's
permissions: a membership the account has is raised to ``admin`` (an owner
stays owner) and its permissions get the grant's; no new membership row is
ever written. 
"""

from __future__ import annotations

from bson import ObjectId

ROLE = "org_admin"
SCOPE_TYPE = "organization"
TEMPLATE = "org_admin_v1"
# Flask owns this list; Hallinto confirms only exactly this set.
PERMISSIONS = sorted({
    "VIEW_ORGANIZATION", "LIST_ORGANIZATIONS", "EDIT_ORGANIZATION", "INVITE_TO_ORGANIZATION",
    "CREATE_DEMO", "EDIT_DEMO", "VIEW_DEMO", "LIST_DEMOS", "GENERATE_EDIT_LINK",
    "CREATE_RECURRING_DEMO", "EDIT_RECURRING_DEMO", "VIEW_RECURRING_DEMO", "LIST_RECURRING_DEMOS",
    "LIST_CASES", "VIEW_CASE",
})
RANK = {"member": 0, "admin": 1, "owner": 2}


def _oid(value):
    return ObjectId(str(value)) if value is not None and ObjectId.is_valid(str(value)) else None


def granted_organizations(grants) -> dict:
    """Organisation id -> permissions from live ``org_admin_v1`` grants.

    The grant, not the account role label, carries the rights: an account that
    keeps another site role (admin, translator) still gets its grants.
    """
    from mielenosoitukset_fi.utils.system_teams import protected_organization

    reach: dict = {}
    for grant in grants or []:
        if grant.get("scope_type") != SCOPE_TYPE or grant.get("template") != TEMPLATE:
            continue
        keys = grant.get("scope_keys") or []
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            org_id = _oid(key)
            if org_id is None or protected_organization(org_id):
                continue
            # Never more than the template gives, whatever a row holds.
            allowed = set(grant.get("permissions") or []) & set(PERMISSIONS)
            reach.setdefault(org_id, set()).update(allowed)
    return reach


def with_org_admin_grants(memberships, grants):
    """Raise the account's own memberships to ``admin`` where a live grant applies."""
    reach = granted_organizations(grants)
    if not reach:
        return memberships
    out = []
    for membership in memberships:
        org_id = _oid(getattr(membership, "organization_id", None))
        permissions = reach.get(org_id)
        if permissions is None:
            out.append(membership)
            continue
        if RANK.get(membership.role, 0) < RANK["admin"]:
            membership.role = "admin"
        membership.permissions = sorted(set(membership.permissions or []) | permissions)
        membership.granted_by_hallinto = True
        out.append(membership)
    return out
