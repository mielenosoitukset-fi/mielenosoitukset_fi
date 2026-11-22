"""Case permissions inherit city scope only from the linked demonstration.

Organisation admins and owners (membership role ``admin``/``owner``) read the
cases about their own organisation and its demonstrations and add notes to
them (``ORG_SCOPE_PERMISSIONS``). Everything else stays with global and city
case rights.
"""

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.cities import CITY_KEY_TO_NAME

CASE_PERMISSIONS = {"LIST_CASES", "VIEW_CASE", "EDIT_CASE", "DELETE_CASE"}
# What an organisation admin may do with its own organisation's cases. Notes go
# through ``case_scope_query(..., org_notes=True)`` on the note route only.
ORG_SCOPE_PERMISSIONS = frozenset({"LIST_CASES", "VIEW_CASE"})
ORG_ROLES = frozenset({"admin", "owner"})
ORG_DEMO_CASE_TYPES = (
    "new_demo", "demo_error_report", "demo_cancellation_request",
    "demo_cancelled", "demo_edit_suggestion",
)
ORG_OWN_CASE_TYPES = ("organization_edit_suggestion", "organization_right_request")


def managed_organization_ids(user):
    """Organisations where ``user`` is admin or owner (system teams excluded)."""
    from mielenosoitukset_fi.utils.system_teams import protected_organization

    ids = []
    for membership in getattr(user, "memberships", None) or []:
        org_id = getattr(membership, "organization_id", None)
        if getattr(membership, "role", None) in ORG_ROLES and org_id is not None and not protected_organization(org_id):
            ids.append(org_id)
    return list(dict.fromkeys(ids))


def _both_forms(ids):
    return list(ids) + [str(value) for value in ids]


def org_scope_query(user):
    """Cases about the user's own organisations and their demos, or None."""
    org_ids = managed_organization_ids(user)
    if not org_ids:
        return None
    db = DatabaseManager.get_instance().get_db()
    demo_ids = list(db.demonstrations.distinct("_id", {"organizers.organization_id": {"$in": _both_forms(org_ids)}}))
    return {"$or": [
        {"demo_id": {"$in": _both_forms(demo_ids)}, "type": {"$in": list(ORG_DEMO_CASE_TYPES)}},
        {"organization_id": {"$in": _both_forms(org_ids)}, "type": {"$in": list(ORG_OWN_CASE_TYPES)}},
    ]}


def is_org_scope_only(user, permission, case_id):
    """True when the user reaches this case only as an organisation admin."""
    if global_case_permission(user, permission):
        return False
    city = _city_scope_query(user, permission)
    if city is not None:
        db = DatabaseManager.get_instance().get_db()
        if db.cases.find_one({"$and": [city, {"_id": case_id}]}, {"_id": 1}) is not None:
            return False
    return can_access_case(user, permission, case_id)


def global_case_permission(user, permission):
    """Distinguish global grants from navigation-level scoped permission."""
    return user.has_full_permissions() or permission in user.global_permissions


def case_scope_query(user, permission, *, org_notes=False):
    """Return the permission scope, or None when no case capability is granted.

    ``org_notes`` lets the note route accept organisation admins for
    ``EDIT_CASE``; no other edit route does.
    """
    if global_case_permission(user, permission):
        return {}
    city = _city_scope_query(user, permission)
    org = org_scope_query(user) if (permission in ORG_SCOPE_PERMISSIONS or org_notes) else None
    if city is None or org is None:
        return city if org is None else org
    return {"$or": [city, org]}


def _city_scope_query(user, permission):
    keys = user.scoped_city_keys_for(permission)
    if not keys:
        return None
    db = DatabaseManager.get_instance().get_db()
    demo_ids = list(db.demonstrations.distinct("_id", {"$or": [
        {"city_key": {"$in": keys}},
        {"city_key": {"$in": [None, ""]},
         "city": {"$in": [CITY_KEY_TO_NAME[key] for key in keys if key in CITY_KEY_TO_NAME]}},
    ]}))
    demo_cases = {
        "demo_id": {"$in": demo_ids + [str(value) for value in demo_ids]},
        "type": {"$in": [
            "new_demo", "demo_error_report", "demo_cancellation_request",
            "demo_cancelled", "support_ticket", "demo_edit_suggestion",
        ]},
    }
    # Local admins see applications for their own cities so they can vouch in
    # the case notes. Decisions stay with global admins (admin_case_bp).
    applications = {"type": "city_admin_application", "meta.city_keys": {"$in": list(keys)}}
    return {"$or": [demo_cases, applications]}


def can_access_case(user, permission, case_id, *, org_notes=False):
    """Apply the same scope to an individual record before any action."""
    query = case_scope_query(user, permission, org_notes=org_notes)
    if query is None:
        return False
    db = DatabaseManager.get_instance().get_db()
    return db.cases.find_one({"$and": [query, {"_id": case_id}]}, {"_id": 1}) is not None
