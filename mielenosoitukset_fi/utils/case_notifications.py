"""Site inbox notices for new admin cases.

Every admin who may view a new case gets one notice in the site inbox (the bell),
with a direct link to the case. Who may view a case is decided by the same
``case_scope_query`` the case pages use, so a city admin hears only about
cases from their own cities. No e-mail is sent: the shared support inbox keeps
its existing mails (maintainer decision 20 Nov 2026, inbox only for now).

Delivery runs as a background job so it covers every place that opens a case
(Flask routes, scripts, the Go API) and retries after a failure. A stable
notice ID keeps reruns from duplicating notices or marking a read one unread.
The notice stores only the case number, type and linked demonstration title,
never submitter details. The inbox rechecks access and hides the notice once
the case is closed or the admin loses access.

Nothing is sent unless ``CASE_NOTIFICATIONS_ENABLED`` is set.
"""
import os
from datetime import timedelta

from bson import ObjectId

from mielenosoitukset_fi.utils.case_access import case_scope_query
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.system_teams import SUPER_ROLES, active_account
from mielenosoitukset_fi.utils.time_utils import utcnow

ENV_FLAG = "CASE_NOTIFICATIONS_ENABLED"
NOTICE_TYPE = "new_case"
# City admin applications already notify the national team themselves.
SKIPPED_TYPES = frozenset({"city_admin_application"})
# Only fresh cases: turning the switch on must not flood inboxes with history.
LOOKBACK = timedelta(days=2)


def is_enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_FLAG, "")).strip().lower() in {"1", "true", "yes"}


def _candidate_users(db):
    """Accounts that could hold VIEW_CASE in some scope; filtered exactly later."""
    ids = set()
    for doc in db.users.find(
        {"$or": [
            {"global_admin": True},
            {"role": {"$in": sorted(SUPER_ROLES)}},
            {"global_permissions": "VIEW_CASE"},
        ]},
        {"_id": 1},
    ):
        ids.add(doc["_id"])
    for grant in db.admin_scope_grants.find(
        {"permissions": "VIEW_CASE", "$or": [{"revoked_at": {"$exists": False}}, {"revoked_at": None}]},
        {"user_id": 1},
    ):
        if ObjectId.is_valid(str(grant.get("user_id", ""))):
            ids.add(ObjectId(str(grant["user_id"])))
    if not ids:
        return []
    return [doc for doc in db.users.find({"_id": {"$in": list(ids)}}) if active_account(doc)]


def _can_view(db, user, case_id):
    scope = case_scope_query(user, "VIEW_CASE")
    if scope is None:
        return False
    return db.cases.find_one({"$and": [scope, {"_id": case_id}]}, {"_id": 1}) is not None


def case_recipients(db, case_id, users=None):
    """Return the user IDs of active admins who may view the case."""
    from mielenosoitukset_fi.users.models import User

    recipients = []
    for doc in users if users is not None else _candidate_users(db):
        try:
            user = User.from_db(doc)
            if _can_view(db, user, case_id):
                recipients.append(doc["_id"])
        except Exception:
            logger.exception("Could not check case access for user %s", doc.get("_id"))
    return recipients


def _payload(db, case):
    payload = {"case_id": case["_id"], "running_num": case.get("running_num"), "case_type": case.get("type")}
    demo_id = case.get("demo_id")
    if demo_id and ObjectId.is_valid(str(demo_id)):
        demo = db.demonstrations.find_one({"_id": ObjectId(str(demo_id))}, {"title": 1})
        if demo and demo.get("title"):
            payload["demo_title"] = demo["title"]
    return payload


def notify_case(db, case, users=None):
    """Upsert one inbox notice per recipient. Returns the number of recipients."""
    recipients = case_recipients(db, case["_id"], users=users)
    payload = _payload(db, case)
    for user_id in recipients:
        db.notifications.update_one(
            {"_id": f"case:{case['_id']}:{user_id}"},
            {"$setOnInsert": {
                "user_id": user_id, "type": NOTICE_TYPE, "payload": payload,
                "link": f"/admin/case/{case['_id']}/",
                "created_at": utcnow(), "read": False,
            }},
            upsert=True,
        )
    return len(recipients)


def deliver_pending(db, now=None, limit=100):
    """Notify about open cases created recently that have not been notified yet."""
    now = now or utcnow()
    query = {
        "created_at": {"$gte": now - LOOKBACK},
        "type": {"$nin": sorted(SKIPPED_TYPES)},
        "meta.closed": {"$ne": True},
        "notified_at": {"$exists": False},
    }
    cases = list(db.cases.find(query).sort("created_at", 1).limit(limit))
    if not cases:
        return 0
    users = _candidate_users(db)
    delivered = 0
    for case in cases:
        try:
            count = notify_case(db, case, users=users)
        except Exception:
            logger.exception("Failed to notify admins about case %s", case.get("_id"))
            continue
        db.cases.update_one({"_id": case["_id"]}, {"$set": {"notified_at": utcnow(), "notified_count": count}})
        delivered += 1
    return delivered


def notice_visible(db, doc, user_id, user_cache):
    """Inbox read check: the case is still open and the reader may still view it."""
    from mielenosoitukset_fi.users.models import User

    case_id = (doc.get("payload") or {}).get("case_id")
    case = db.cases.find_one({"_id": case_id}, {"meta.closed": 1}) if case_id is not None else None
    if not case or (case.get("meta") or {}).get("closed"):
        return False
    if "user" not in user_cache:
        user_doc = db.users.find_one({"_id": ObjectId(user_id)})
        user_cache["user"] = User.from_db(user_doc) if user_doc and active_account(user_doc) else None
    user = user_cache["user"]
    return bool(user) and _can_view(db, user, case_id)
