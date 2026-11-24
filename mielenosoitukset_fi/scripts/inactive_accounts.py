"""Dry run for the inactive-account policy (#1174). Counts only; it deletes nothing.

    python -m mielenosoitukset_fi.scripts.inactive_accounts

Maintainer decision (24.11.2026): an account is inactive after 24 months without a
successful sign-in (`last_login`, or the creation time when there is none). Plain
accounts will later get a warning mail at 30 and 7 days before removal and a
30-day grace period, then the normal erasure. Nothing here sends mail or removes
data. The counts exist so the maintainer can see the effect before any job is built.

Never removed automatically: accounts with a role above `user`, `global_admin`,
global permissions, an active admin scope grant, an organization membership as
owner or admin, or an open security case. They go to manual review instead.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.security_cases import TERMINAL

INACTIVE_MONTHS = 24
MONTH = timedelta(days=30.4375)


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def last_activity(user_doc):
    """Last successful sign-in, else creation time, else the time the id was made."""
    for field in ("last_login", "created_at"):
        value = _aware(user_doc.get(field))
        if isinstance(value, datetime):
            return value
    return user_doc["_id"].generation_time


def _id_variants(ids):
    return list(ids) + [str(i) for i in ids]


def _exempt_sets(db):
    """User ids that must never be removed automatically, with the reason."""
    reasons = {}
    for case in db.security_cases.find({"status": {"$nin": list(TERMINAL)}}, {"target_id": 1, "user_id": 1}):
        for key in ("target_id", "user_id"):
            if case.get(key) is not None:
                reasons.setdefault(str(case[key]), set()).add("open_security_case")
    for grant in db.admin_scope_grants.find({"revoked_at": None}, {"user_id": 1}):
        if grant.get("user_id") is not None:
            reasons.setdefault(str(grant["user_id"]), set()).add("admin_scope_grant")
    for membership in db.memberships.find({"role": {"$in": ["owner", "admin"]}}, {"user_id": 1}):
        if membership.get("user_id") is not None:
            reasons.setdefault(str(membership["user_id"]), set()).add("org_owner_or_admin")
    return reasons


def count_inactive(db, now=None, months=INACTIVE_MONTHS):
    """Return counts only: how many accounts are inactive and why some are exempt."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - months * MONTH
    exempt = _exempt_sets(db)
    result = {
        "cutoff": cutoff.isoformat(),
        "months": months,
        "users_total": 0,
        "inactive": 0,
        "would_warn": 0,
        "never_signed_in": 0,
        "exempt": {"privileged": 0, "open_security_case": 0, "admin_scope_grant": 0, "org_owner_or_admin": 0},
    }
    from mielenosoitukset_fi.admin.user_erasure import is_privileged

    for doc in db.users.find({}, {"last_login": 1, "created_at": 1, "role": 1, "global_admin": 1, "global_permissions": 1}):
        result["users_total"] += 1
        if last_activity(doc) >= cutoff:
            continue
        result["inactive"] += 1
        if not doc.get("last_login"):
            result["never_signed_in"] += 1
        reasons = set(exempt.get(str(doc["_id"]), set()))
        if is_privileged(doc):
            reasons.add("privileged")
        for reason in reasons:
            result["exempt"][reason] += 1
        if not reasons:
            result["would_warn"] += 1
    return result


def main(db=None):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    return count_inactive(db)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    print(json.dumps(main(), default=str))
