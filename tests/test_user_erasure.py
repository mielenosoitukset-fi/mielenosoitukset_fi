"""Deleting an account removes its direct personal-data records (#596)."""

from bson import ObjectId

from mielenosoitukset_fi.admin.user_erasure import erase_user_personal_data
from tests.conftest import _client_for_user


def _fresh_delete(app, admin_id, target_id):
    client = _client_for_user(app, admin_id)
    r = client.post(
        "/users/auth/api/v2/step-up/fresh/options",
        json={"action": "delete_user", "target_id": str(target_id)},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.post("/users/auth/api/v2/step-up/fresh/password", json={"password": "AdminPass1!"})
    assert r.status_code == 200, r.get_data(as_text=True)
    token = r.get_json()["fresh_step_up_token"]
    return client.post(
        "/admin/user/delete_user",
        json={"user_id": str(target_id), "fresh_step_up_token": token},
    )


def _seed_personal_data(db, seeded_data):
    uid = seeded_data["user_id"]
    sid = str(uid)
    demo = seeded_data["pending_demo_id"]
    email = "alice@example.test"
    db.user_settings.insert_one({"user_id": uid, "language": "fi"})
    db.login_logs.insert_one({"user_id": uid, "ip": "127.0.0.1"})
    db.password_changes.insert_many([
        {"email": email, "token": "raw-reset-token"},
        {"user_id": uid, "method": "settings_page"},
    ])
    db.demo_reminders.insert_one({"demonstration_id": demo, "user_email": email})
    db.demo_attending.insert_one({"user_id": uid, "demo_id": demo, "attending": True})
    db.reports.insert_one({"demo_id": demo, "user": uid, "reporter_email": email})
    db.demo_submission_errors.insert_one({"user": {"id": sid, "username": "alice"}})
    db.notifications.insert_one({"user_id": uid, "message": "hi"})
    db.messages.insert_one({"sender_id": seeded_data["friend_id"], "recipient_id": uid})
    db.mfas.insert_one({"user_id": uid, "secret": "x", "device_name": "Phone"})
    db.mfa_login_challenges.insert_one({"user_id": uid})
    db.magic_links.insert_one({"intended_recipient": {"user_id": sid}, "action": "login"})
    db.api_tokens.insert_one({"user_id": uid, "token_hash": "h"})
    db.api_token_requests.insert_one({"user_id": uid, "username": "alice"})
    db.developer_apps.insert_one({"owner_id": uid, "name": "Alice app"})
    db.developer_scope_requests.insert_one({"user_id": uid, "scopes": ["write"]})
    db.admin_scope_grants.insert_one({"user_id": uid, "scope": "city"})
    db.board_clearances.insert_one({"user_id": uid, "approved": True})
    db.users.update_one(
        {"_id": seeded_data["friend_id"]},
        {"$push": {"friend_requests": {"sent_by": uid}}, "$addToSet": {"followers": sid}},
    )
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$addToSet": {"editors": sid}})
    # Kept: evidence and audit records, the submitted demonstration.
    db.malicious_reports.insert_one({"user_email": email, "ip": "127.0.0.1"})
    db.cases.insert_one({"submitter_id": uid, "type": "new_demo"})
    db.board_audit_logs.insert_one({"user_id": sid, "action": "x"})
    return uid, sid, demo, email


def test_delete_user_removes_direct_personal_data(app, db, seeded_data):
    uid, sid, demo, email = _seed_personal_data(db, seeded_data)
    ids = {"$in": [uid, sid]}

    response = _fresh_delete(app, seeded_data["admin_id"], uid)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["status"] == "OK"
    assert db.users.find_one({"_id": uid}) is None
    for collection, query in [
        ("user_settings", {"user_id": ids}),
        ("login_logs", {"user_id": ids}),
        ("password_changes", {"$or": [{"user_id": ids}, {"email": email}]}),
        ("demo_reminders", {"user_email": email}),
        ("demo_attending", {"user_id": ids}),
        ("reports", {"$or": [{"user": ids}, {"reporter_email": email}]}),
        ("demo_submission_errors", {"user.id": sid}),
        ("notifications", {"user_id": ids}),
        ("messages", {"recipient_id": ids}),
        ("memberships", {"user_id": ids}),
        ("mfas", {"user_id": ids}),
        ("mfa_login_challenges", {"user_id": ids}),
        ("magic_links", {"intended_recipient.user_id": ids}),
        ("api_tokens", {"user_id": ids}),
        ("api_token_requests", {"user_id": ids}),
        ("developer_apps", {"owner_id": ids}),
        ("developer_scope_requests", {"user_id": ids}),
        ("admin_scope_grants", {"user_id": ids}),
        ("board_clearances", {"user_id": ids}),
        ("submitters", {"submitter_email": email}),
    ]:
        assert db[collection].count_documents(query) == 0, collection
    assert db.users.count_documents({"friends.user_id": ids}) == 0
    assert db.users.count_documents({"friend_requests.sent_by": ids}) == 0
    assert db.users.count_documents({"followers": ids}) == 0
    assert db.demonstrations.count_documents({"editors": ids}) == 0

    # Kept on purpose.
    assert db.demonstrations.find_one({"_id": demo}) is not None
    assert db.malicious_reports.count_documents({"user_email": email}) == 1
    assert db.cases.count_documents({"submitter_id": uid}) == 1
    assert db.board_audit_logs.count_documents({"user_id": sid}) == 1
    # Other accounts' records stay.
    assert db.users.find_one({"_id": seeded_data["friend_id"]}) is not None
    assert db.memberships.count_documents({"user_id": seeded_data["admin_id"]}) == 1


def test_erasure_leaves_other_accounts_alone_and_is_idempotent(db, seeded_data):
    other = seeded_data["friend_id"]
    db.user_settings.insert_one({"user_id": other, "language": "fi"})
    db.mfas.insert_one({"user_id": other, "secret": "y"})
    ghost = {"_id": ObjectId(), "username": "ghost", "email": "ghost@example.test"}

    assert erase_user_personal_data(db, ghost) == {}
    assert erase_user_personal_data(db, ghost) == {}
    assert db.user_settings.count_documents({"user_id": other}) == 1
    assert db.mfas.count_documents({"user_id": other}) == 1


def test_erasure_without_email_or_username_matches_by_id_only(db, seeded_data):
    uid = seeded_data["user_id"]
    db.login_logs.insert_one({"user_id": seeded_data["friend_id"], "username": None})
    db.login_logs.insert_one({"user_id": uid})

    erased = erase_user_personal_data(db, {"_id": uid})

    assert erased.get("login_logs") == 1
    assert db.login_logs.count_documents({"user_id": seeded_data["friend_id"]}) == 1
