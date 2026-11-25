"""City admin applications stored by the Go API (docs/city-admin-applications.md).

Go accepts and stores the application in ``city_admin_applications``. Hallinto
reads and decides applications; this module keeps the read model for the issuer and the
legacy decision path for cases made before Hallinto took over.
Granting the role itself stays in the accounts dashboard, so approving an
application never changes permissions on its own.
"""
from mielenosoitukset_fi.utils.cities import CITY_KEY_TO_NAME
from mielenosoitukset_fi.utils.time_utils import utcnow

CASE_TYPE = "city_admin_application"
STATUSES = ("received", "in_review", "approved", "declined", "withdrawn")
DECISION_STATUSES = ("in_review", "approved", "declined")
STATUS_LABELS = {
    "received": "Vastaanotettu",
    "in_review": "Käsittelyssä",
    "approved": "Hyväksytty",
    "declined": "Hylätty",
    "withdrawn": "Peruttu",
}
EXPERIENCE_LABELS = {"none": "Ei kokemusta", "some": "Jonkin verran", "lot": "Paljon"}


def city_names(keys):
    return [CITY_KEY_TO_NAME.get(key, key) for key in keys or []]


def set_status(db, application_id, status, actor):
    """Record a review decision on the application and its case history."""
    if status not in DECISION_STATUSES:
        raise ValueError("unknown status")
    now = utcnow()
    result = db.city_admin_applications.update_one(
        # One place decides: an application opened in Hallinto is decided there.
        {"_id": application_id, "status": {"$nin": ["withdrawn"]}, "hallinto_case": None},
        {"$set": {"status": status, "updated_at": now, "decided_by": actor if status != "in_review" else None}},
    )
    if not result.matched_count:
        return False
    update = {"$push": {"case_history": {
        "timestamp": now, "action": f"Hakemuksen tila: {STATUS_LABELS[status]}", "user": actor,
        "mech_action": "application_status", "metadata": {"status": status},
        "meta_schema": {"status": "string"},
    }}, "$set": {"updated_at": now}}
    if status in ("approved", "declined"):
        update["$set"].update({"meta.closed": True, "meta.closed_reason": STATUS_LABELS[status], "meta.closed_at": now})
    db.cases.update_one({"_id": application_id}, update)
    return True


# Proposed retention (docs/decisions.md, awaiting maintainer approval). Only
# decided applications are touched; open ones are never purged.
RETENTION_DAYS = {"declined": 90, "withdrawn": 90, "approved": 365}
RETENTION_ENV_FLAG = "CITY_ADMIN_APPLICATION_RETENTION_ENABLED"
_FREE_TEXT_FIELDS = {"about": "", "activism": "", "references": "", "consent_at": ""}


def _flag(name, environ=None):
    import os
    value = (environ if environ is not None else os.environ).get(name, "")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def retention_enabled(environ=None):
    return _flag(RETENTION_ENV_FLAG, environ)


def purge_expired(db, now=None, dry_run=False):
    """Erase the free text of decided applications once their retention ends.

    The row stays with user, cities, status and dates, so the "one open
    application" rule and the applicant's status list keep working. The
    ``purged_at`` mark makes reruns skip finished rows. Returns the count.
    """
    from datetime import timedelta
    now = now or utcnow()
    purged = 0
    for status, days in RETENTION_DAYS.items():
        query = {"status": status, "purged_at": {"$exists": False},
                 "updated_at": {"$lt": now - timedelta(days=days)}}
        if dry_run:
            purged += db.city_admin_applications.count_documents(query)
            continue
        result = db.city_admin_applications.update_many(
            query, {"$unset": _FREE_TEXT_FIELDS, "$set": {"purged_at": now}})
        purged += result.modified_count
    return purged


# --- Hallinto handles applications (hallinto issue #39) ---------------------
#
# OT handles applications in Hallinto, which reads them from this collection and
# records each step through the issuer (``/hallinto-oidc/city-admin-applications/*``).
# Flask makes no case for a new application.
HALLINTO_STATUSES = ("in_review", "approved", "declined")
_HALLINTO_LIST_STATUSES = ("received", "in_review", "approved", "declined")
_HALLINTO_CASE_MAX = 128


def hallinto_view(row, users):
    """What a Hallinto handler sees: the account, the cities and the answers.
    No email address; the account is identified by its id and username."""
    stamp = lambda value: value.isoformat() + ("" if value.tzinfo else "Z") if value else None
    user = users.get(row.get("user_id")) or {}
    activism = row.get("activism") or {}
    return {
        "id": str(row["_id"]), "status": row.get("status"),
        "account": {"id": str(row["user_id"]) if row.get("user_id") else None, "username": user.get("username"),
                    "name": user.get("displayname") or user.get("username")},
        "cities": [{"key": key, "name": CITY_KEY_TO_NAME.get(key, key)} for key in row.get("city_keys") or []],
        "about": row.get("about"), "references": row.get("references"),
        "organizations": activism.get("organizations") if isinstance(activism, dict) else None,
        "experience": EXPERIENCE_LABELS.get(activism.get("experience")) if isinstance(activism, dict) else None,
        "experience_details": activism.get("experience_details") if isinstance(activism, dict) else None,
        "case": row.get("hallinto_case"), "created_at": stamp(row.get("created_at")),
        "updated_at": stamp(row.get("updated_at")),
    }


def hallinto_list(db, limit=100):
    """Open and decided applications, newest first. Withdrawn ones are left out."""
    rows = list(db.city_admin_applications.find({"status": {"$in": list(_HALLINTO_LIST_STATUSES)}})
                .sort("created_at", -1).limit(limit))
    ids = [row["user_id"] for row in rows if row.get("user_id")]
    users = {u["_id"]: u for u in db.users.find({"_id": {"$in": ids}}, {"username": 1, "displayname": 1})}
    return [hallinto_view(row, users) for row in rows]


class HallintoConflict(Exception):
    """The application is decided, withdrawn or handled in another case."""


def hallinto_update(db, application_id, status, case, actor):
    """Record a step from Hallinto. The first step links the Hallinto case;
    later steps must name the same case. A decided application keeps its
    decision, so a replay of the same status is the only change allowed.
    Returns the updated row, None when it does not exist."""
    if status not in HALLINTO_STATUSES:
        raise ValueError("unknown status")
    if not isinstance(case, str) or not 0 < len(case) <= _HALLINTO_CASE_MAX:
        raise ValueError("case required")
    row = db.city_admin_applications.find_one({"_id": application_id})
    if not row:
        return None
    decided = row.get("status") in ("approved", "declined")
    if (row.get("status") == "withdrawn" or (row.get("hallinto_case") not in (None, case))
            or (decided and row.get("status") != status)):
        raise HallintoConflict()
    now = utcnow()
    if row.get("status") == status and row.get("hallinto_case") == case:
        return row
    result = db.city_admin_applications.find_one_and_update(
        {"_id": application_id, "status": row.get("status"), "hallinto_case": row.get("hallinto_case")},
        {"$set": {"status": status, "hallinto_case": case, "case_pending": False, "updated_at": now,
                  "decided_by": actor if status != "in_review" else None},
         "$push": {"history": {"status": status, "at": now, "by": actor, "via": "hallinto", "case": case}}},
        return_document=True,
    )
    if result is None:
        raise HallintoConflict()  # Changed meanwhile; Hallinto retries with fresh state.
    if status != "in_review" and db.cases.find_one({"_id": application_id}, {"_id": 1}):
        # A case made before the switch closes with the Hallinto decision.
        db.cases.update_one({"_id": application_id}, {"$set": {
            "meta.closed": True, "meta.closed_reason": STATUS_LABELS[status], "meta.closed_at": now, "updated_at": now},
            "$push": {"case_history": {"timestamp": now, "action": f"Hakemuksen tila Hallinnossa: {STATUS_LABELS[status]}",
                                       "user": "hallinto", "mech_action": "application_status",
                                       "metadata": {"status": status, "hallinto_case": case},
                                       "meta_schema": {"status": "string", "hallinto_case": "string"}}}})
    return result
