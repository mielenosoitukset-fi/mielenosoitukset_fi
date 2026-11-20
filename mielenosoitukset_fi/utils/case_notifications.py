"""Site inbox notices for new admin cases.

Every admin who may view a new case gets one notice in the site inbox (the bell),
with a direct link to the case. Who may view a case is decided by the same
``case_scope_query`` the case pages use, so a city admin hears only about
cases from their own cities. With ``CASE_NOTIFICATION_EMAILS_ENABLED`` they also
get one e-mail each (queued, one recipient per mail, sent only when the inbox
notice is first created). Maintainer decision 20 Nov 2026: admins with
case rights everywhere (full admins and global case grants) always get the
mail; admins with only city-scoped case rights get it when they opt in on the
case list (``users.case_notice_emails``). Each case records who was notified
and who was mailed (``notified_users``) and shows it on the case page. The
shared support inbox keeps its existing mails.

Delivery runs as a background job so it covers every place that opens a case
(Flask routes, scripts, the Go API) and retries after a failure. A stable
notice ID keeps reruns from duplicating notices or marking a read one unread.
The notice stores only the case number, type and linked demonstration title,
never submitter details. The inbox rechecks access and hides the notice once
the case is closed or the admin loses access.

Both switches are on by default (maintainer go, 20 Nov 2026). Setting
``CASE_NOTIFICATIONS_ENABLED`` or ``CASE_NOTIFICATION_EMAILS_ENABLED`` to
``0``/``false``/``no``/``off`` in the Flask environment switches that part off.
"""
import os
from datetime import timedelta

from bson import ObjectId

from mielenosoitukset_fi.utils.case_access import case_scope_query, global_case_permission
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.system_teams import SUPER_ROLES, active_account
from mielenosoitukset_fi.utils.time_utils import utcnow

ENV_FLAG = "CASE_NOTIFICATIONS_ENABLED"
EMAIL_ENV_FLAG = "CASE_NOTIFICATION_EMAILS_ENABLED"
SITE_URL = "https://mielenosoitukset.fi"
EMAIL_TEMPLATE = "admin_case_notification.html"
# Plain Finnish labels: the job runs without a request locale.
TYPE_LABELS = {
    "new_demo": "Uusi mielenosoitus",
    "demo_error_report": "Virheilmoitus",
    "demo_edit_suggestion": "Muutosehdotus",
    "organization_edit_suggestion": "Organisaation muutospyyntö",
    "demo_cancellation_request": "Peruutuspyyntö",
    "demo_cancelled": "Peruttu mielenosoitus",
    "support_ticket": "Tukipyyntö",
}
NOTICE_TYPE = "new_case"
# City admin applications already notify the national team themselves.
SKIPPED_TYPES = frozenset({"city_admin_application"})
# Only fresh cases: turning the switch on must not flood inboxes with history.
LOOKBACK = timedelta(days=2)


OFF_VALUES = {"0", "false", "no", "off"}


def _flag(environ, name):
    """On by default since 20 Nov 2026; set the flag to 0/false/no/off to switch off."""
    environ = os.environ if environ is None else environ
    return str(environ.get(name, "")).strip().lower() not in OFF_VALUES


def is_enabled(environ=None):
    return _flag(environ, ENV_FLAG)


def emails_enabled(environ=None):
    return _flag(environ, EMAIL_ENV_FLAG)


_SENDER = None


def _email_sender():
    """One shared sender per process, created only when mail is switched on."""
    global _SENDER
    if _SENDER is None:
        from mielenosoitukset_fi.emailer.EmailSender import EmailSender
        _SENDER = EmailSender()
    return _SENDER


def _queue_email(sender, recipient_email, case, payload):
    running_num = payload.get("running_num") or ""
    type_label = TYPE_LABELS.get(case.get("type"), "Tapaus")
    sender.queue_email(
        template_name=EMAIL_TEMPLATE,
        subject=f"Uusi tapaus #{running_num}: {type_label}",
        recipients=[recipient_email],
        context={
            "running_num": running_num,
            "type_label": type_label,
            "demo_title": payload.get("demo_title") or "",
            "case_link": f"{SITE_URL}/admin/case/{case['_id']}/",
        },
    )


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
    """Return (user document, User) pairs for active admins who may view the case."""
    from mielenosoitukset_fi.users.models import User

    recipients = []
    for doc in users if users is not None else _candidate_users(db):
        try:
            user = User.from_db(doc)
            if _can_view(db, user, case_id):
                recipients.append((doc, user))
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


def wants_email(doc, user):
    """Global case admins always get mail; city-scoped admins opt in."""
    return global_case_permission(user, "VIEW_CASE") or doc.get("case_notice_emails") is True


def notify_case(db, case, users=None, email_sender=None):
    """Upsert one inbox notice per recipient, and e-mail newly notified ones.

    Returns one ``{"user_id", "username", "emailed"}`` row per recipient. Pass
    ``email_sender`` to send e-mail; without it only inbox notices are written.
    """
    recipients = case_recipients(db, case["_id"], users=users)
    payload = _payload(db, case)
    notified = []
    for doc, user in recipients:
        user_id = doc["_id"]
        result = db.notifications.update_one(
            {"_id": f"case:{case['_id']}:{user_id}"},
            {"$setOnInsert": {
                "user_id": user_id, "type": NOTICE_TYPE, "payload": payload,
                "link": f"/admin/case/{case['_id']}/",
                "created_at": utcnow(), "read": False,
            }},
            upsert=True,
        )
        # Only a newly created notice mails, so reruns never mail twice.
        email = (doc.get("email") or "").strip()
        if email_sender is not None and result.upserted_id is not None and email and wants_email(doc, user):
            try:
                _queue_email(email_sender, email, case, payload)
                db.notifications.update_one({"_id": f"case:{case['_id']}:{user_id}"}, {"$set": {"emailed": True}})
            except Exception:
                logger.exception("Failed to queue case notice mail for case %s", case.get("_id"))
        # Read back so a rerun after a crash still reports mail sent earlier.
        notice = db.notifications.find_one({"_id": f"case:{case['_id']}:{user_id}"}, {"emailed": 1}) or {}
        emailed = bool(notice.get("emailed"))
        notified.append({"user_id": user_id, "username": doc.get("username") or "", "emailed": emailed})
    return notified


def deliver_pending(db, now=None, limit=100, email_sender=None):
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
            notified = notify_case(db, case, users=users, email_sender=email_sender)
        except Exception:
            logger.exception("Failed to notify admins about case %s", case.get("_id"))
            continue
        db.cases.update_one({"_id": case["_id"]}, {"$set": {
            "notified_at": utcnow(), "notified_count": len(notified), "notified_users": notified,
        }})
        delivered += 1
    return delivered


def notice_visible(db, doc, user_id, user_cache):
    """Inbox read check: the case is still open and the reader may still view it.

    The reader's case scope is resolved once per inbox read (``user_cache``),
    not once per notice, because the inbox is read on every page render.
    """
    from mielenosoitukset_fi.users.models import User

    case_id = (doc.get("payload") or {}).get("case_id")
    if case_id is None:
        return False
    if "scope" not in user_cache:
        user_doc = db.users.find_one({"_id": ObjectId(user_id)})
        user = User.from_db(user_doc) if user_doc and active_account(user_doc) else None
        user_cache["scope"] = case_scope_query(user, "VIEW_CASE") if user else None
    scope = user_cache["scope"]
    if scope is None:
        return False
    query = {"$and": [scope, {"_id": case_id, "meta.closed": {"$ne": True}}]}
    return db.cases.find_one(query, {"_id": 1}) is not None
