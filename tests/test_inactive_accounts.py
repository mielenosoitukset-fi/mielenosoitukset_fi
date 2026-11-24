"""Dry run counts for inactive accounts (#1174): counts only, exemptions respected."""
from datetime import datetime, timedelta, timezone

from bson import ObjectId

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=800)
FRESH = NOW - timedelta(days=30)
MARK = {"seeded_by": "inactive-accounts-test"}


def _cleanup(db):
    for name in ("users", "security_cases", "admin_scope_grants", "memberships"):
        db[name].delete_many(MARK)


def test_counts_inactive_accounts_and_exemptions(db):
    from mielenosoitukset_fi.scripts import inactive_accounts as ia

    _cleanup(db)
    before = ia.count_inactive(db, now=NOW)
    ids = {name: ObjectId() for name in ("plain", "fresh", "never", "admin", "case", "owner", "grant")}
    db.users.insert_many([
        {**MARK, "_id": ids["plain"], "role": "user", "last_login": OLD},
        {**MARK, "_id": ids["fresh"], "role": "user", "last_login": FRESH},
        {**MARK, "_id": ids["never"], "role": "user", "created_at": OLD},
        {**MARK, "_id": ids["admin"], "role": "user", "global_admin": True, "last_login": OLD},
        {**MARK, "_id": ids["case"], "role": "user", "last_login": OLD},
        {**MARK, "_id": ids["owner"], "role": "user", "last_login": OLD},
        {**MARK, "_id": ids["grant"], "role": "user", "last_login": OLD},
    ])
    db.security_cases.insert_one({**MARK, "status": "suspended", "target_id": ids["case"]})
    db.memberships.insert_one({**MARK, "user_id": ids["owner"], "role": "owner"})
    db.admin_scope_grants.insert_one({**MARK, "user_id": ids["grant"], "revoked_at": None})
    try:
        after = ia.count_inactive(db, now=NOW)
        assert after["users_total"] - before["users_total"] == 7
        assert after["inactive"] - before["inactive"] == 6
        assert after["never_signed_in"] - before["never_signed_in"] == 1
        assert after["would_warn"] - before["would_warn"] == 2  # plain + never
        assert after["exempt"]["privileged"] - before["exempt"]["privileged"] == 1
        assert after["exempt"]["open_security_case"] - before["exempt"]["open_security_case"] == 1
        assert after["exempt"]["org_owner_or_admin"] - before["exempt"]["org_owner_or_admin"] == 1
        assert after["exempt"]["admin_scope_grant"] - before["exempt"]["admin_scope_grant"] == 1
        assert db.users.count_documents(MARK) == 7  # nothing was removed or changed
    finally:
        _cleanup(db)


def test_closed_security_case_does_not_exempt(db):
    from mielenosoitukset_fi.scripts import inactive_accounts as ia

    _cleanup(db)
    before = ia.count_inactive(db, now=NOW)
    uid = ObjectId()
    db.users.insert_one({**MARK, "_id": uid, "role": "user", "last_login": OLD})
    db.security_cases.insert_one({**MARK, "status": "restored", "target_id": uid})
    try:
        after = ia.count_inactive(db, now=NOW)
        assert after["would_warn"] - before["would_warn"] == 1
        assert after["exempt"]["open_security_case"] == before["exempt"]["open_security_case"]
    finally:
        _cleanup(db)
