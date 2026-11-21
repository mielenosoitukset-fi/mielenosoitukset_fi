"""Which ``admin_scope_grants`` documents are in force.

A grant is live while it is not revoked and, when it has ``expires_at``
(naive UTC, exclusive), before that instant. Expiry is checked whenever a
grant is read, so no job has to revoke it. Keep in sync with
``liveGrantClauses`` in ``go-api/city_admin_status.go``.
"""

from mielenosoitukset_fi.utils.time_utils import utcnow


def live_grant_clauses(now=None) -> list:
    now = now or utcnow()
    return [
        {"$or": [{"revoked_at": {"$exists": False}}, {"revoked_at": None}]},
        {"$or": [{"expires_at": {"$exists": False}}, {"expires_at": None}, {"expires_at": {"$gt": now}}]},
    ]


def live_grant_filter(now=None, *extra: dict) -> dict:
    """``{"$and": [...]}`` for live grants, plus any extra clauses."""
    return {"$and": [*live_grant_clauses(now), *extra]}
