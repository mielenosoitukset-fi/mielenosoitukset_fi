"""Remove a deleted account's direct personal-data records (issue #596).

Runs after the ``users`` document has been deleted. Removes records that only
exist because of the account: its settings, login and password logs, reminders,
own reports, notifications, messages, memberships, MFA devices, API tokens and
developer apps, and its links in other users' friend/follow lists and in
demonstration editor lists.

Kept on purpose (legitimate interest / evidence, decided 20 Nov 2026):
demonstrations the user submitted, moderation ``cases`` and
``city_admin_applications``, ``malicious_reports``, ``security_cases`` and the
audit logs (``admin_logs``, ``board_audit_logs``, ``super_audit_logs``,
``demo_audit_logs``). Their ``user_id`` no longer resolves to an account.
"""

from bson import ObjectId


def _erasure_queries(user_object_id, username, email):
    ids = [user_object_id, str(user_object_id)]

    def by(*fields, extra=()):
        clauses = [{field: {"$in": ids}} for field in fields]
        clauses.extend(extra)
        return {"$or": clauses}

    email_clauses = []
    if email:
        email_clauses = [
            {field: email}
            for field in (
                "email",
                "user_email",
                "reporter_email",
                "submitter_email",
                "submitter.email",
                "suggested_by.email",
                "form_snapshot.email",
                "form_snapshot.user_email",
                "form_snapshot.reporter_email",
                "form_snapshot.submitter_email",
            )
        ]
    username_clauses = []
    if username:
        username_clauses = [{"username": username}, {"user.username": username}]

    return [
        ("user_settings", by("user_id")),
        ("login_logs", by("user_id", extra=username_clauses + email_clauses)),
        ("password_changes", by("user_id", extra=email_clauses)),
        ("demo_reminders", by("user_id", extra=email_clauses)),
        ("demo_attending", by("user_id")),
        ("reports", by("user", extra=email_clauses)),
        ("demo_submission_errors", by("user.id", extra=email_clauses)),
        ("notifications", by("user_id")),
        ("messages", by("sender_id", "recipient_id")),
        ("memberships", by("user_id")),
        ("mfas", by("user_id")),
        ("mfa_login_challenges", by("user_id")),
        ("magic_links", by("intended_recipient.user_id", "created_by_id")),
        ("api_tokens", by("user_id")),
        ("api_token_requests", by("user_id")),
        ("developer_apps", by("owner_id")),
        ("developer_scope_requests", by("user_id")),
        ("admin_scope_grants", by("user_id")),
        ("board_clearances", by("user_id")),
        ("submitters", by("user_id", extra=email_clauses)),
        ("demo_suggestions", by("user_id", extra=email_clauses)),
    ]


def erase_user_personal_data(db, user_doc):
    """Delete the account's direct personal-data records; return counts per collection.

    ``user_doc`` is the already deleted ``users`` document (``_id``, ``username``,
    ``email``). Safe to call again: every step is idempotent.
    """
    user_object_id = user_doc["_id"]
    if not isinstance(user_object_id, ObjectId):
        user_object_id = ObjectId(str(user_object_id))
    ids = [user_object_id, str(user_object_id)]

    deleted = {}
    for collection, query in _erasure_queries(
        user_object_id, user_doc.get("username"), user_doc.get("email")
    ):
        count = db[collection].delete_many(query).deleted_count
        if count:
            deleted[collection] = count

    db.users.update_many(
        {"$or": [
            {"friends.user_id": {"$in": ids}},
            {"friend_requests.user_id": {"$in": ids}},
            {"followers": {"$in": ids}},
            {"following": {"$in": ids}},
        ]},
        {"$pull": {
            "friends": {"user_id": {"$in": ids}},
            "friend_requests": {"user_id": {"$in": ids}},
            "followers": {"$in": ids},
            "following": {"$in": ids},
        }},
    )
    db.users.update_many(
        {"friend_requests.sent_by": {"$in": ids}},
        {"$pull": {"friend_requests": {"sent_by": {"$in": ids}}}},
    )
    db.demonstrations.update_many(
        {"editors": {"$in": ids}},
        {"$pull": {"editors": {"$in": ids}}},
    )
    return deleted
