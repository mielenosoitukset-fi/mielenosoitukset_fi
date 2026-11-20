"""Deleting an account: delete, keep pseudonymised, seal (#596, docs/account-erasure.md)."""

import datetime

from bson import ObjectId
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mielenosoitukset_fi.admin import user_erasure
from mielenosoitukset_fi.admin.user_erasure import (
    SEAL_COLLECTION,
    erase_user,
    is_privileged,
    open_identity_seal,
    pseudonym,
    pseudonymous_id,
    purge_expired,
)
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user

SECRET = "test-secret-key-for-erasure-0123456789"
EMAIL = "erase-me@example.test"
NAME = "Erase Me Person"


def _keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return public_pem, private_pem


def _seed(db, uid, friend_id, demo_id):
    sid = str(uid)
    db.user_settings.insert_one({"user_id": uid, "language": "fi"})
    db.mfas.insert_one({"user_id": uid, "secret": "x"})
    db.mfa_login_challenges.insert_one({"user_id": uid})
    db.magic_links.insert_one({"intended_recipient": {"user_id": sid}, "action": "login"})
    db.api_tokens.insert_one({"user_id": uid, "token_hash": "h"})
    db.password_changes.insert_one({"user_id": uid, "method": "settings_page"})
    db.notifications.insert_one({"user_id": uid, "message": "hi"})
    db.demo_attending.insert_one({"user_id": uid, "demo_id": demo_id})
    db.board_clearances.insert_one({"user_id": uid, "approved": True})
    db.login_logs.insert_one({"user_id": uid, "username": "erase-me", "ip": "198.51.100.7"})
    db.messages.insert_one({"sender_id": uid, "recipient_id": friend_id, "body": f"mail me at {EMAIL}"})
    db.malicious_reports.insert_one({"user_email": EMAIL, "ip": "198.51.100.7", "reason": "spam"})
    db.cases.insert_one({"submitter_id": uid, "meta": {"submitter_name": NAME}})
    db.board_audit_logs.insert_one({"user_id": sid, "action": "x"})
    db.admin_logs.insert_one({"actor": {"id": sid, "username": "erase-me", "email": EMAIL}, "ip": "198.51.100.7"})
    db.users.update_one({"_id": friend_id}, {"$push": {"friend_requests": {"sent_by": uid}}, "$addToSet": {"followers": sid}})
    db.demonstrations.update_one({"_id": demo_id}, {"$addToSet": {"editors": sid}})


def _user(uid, **extra):
    return {"_id": uid, "username": "erase-me", "email": EMAIL, "displayname": NAME, "role": "user", **extra}


def _no_trace(db, collection, uid):
    for record in db[collection].find():
        text = repr(record)
        assert str(uid) not in text and EMAIL not in text and NAME not in text, (collection, record)


def test_ordinary_account_deletes_personal_data_and_pseudonymises_evidence(db, seeded_data):
    uid = ObjectId()
    friend_id, demo_id = seeded_data["friend_id"], seeded_data["demo_id"]
    _seed(db, uid, friend_id, demo_id)
    public_pem, private_pem = _keypair()

    report = erase_user(db, _user(uid), SECRET, public_pem)

    assert report["privileged"] is False
    ids = {"$in": [uid, str(uid)]}
    for collection in ("user_settings", "mfas", "mfa_login_challenges", "api_tokens",
                       "password_changes", "notifications", "demo_attending", "board_clearances"):
        assert db[collection].count_documents({"user_id": ids}) == 0, collection
        assert db[collection].count_documents({"erased_subject": {"$exists": True}}) == 0, collection
    assert db.magic_links.count_documents({"intended_recipient.user_id": ids}) == 0

    alias, fake = pseudonym(SECRET, uid), pseudonymous_id(SECRET, uid)
    login = db.login_logs.find_one({"erased_subject": alias})
    assert login["user_id"] == fake and login["username"] == alias and login["ip"] == "198.51.100.7"
    message = db.messages.find_one({"erased_subject": alias})
    assert message["sender_id"] == fake and alias in message["body"] and EMAIL not in message["body"]
    report_doc = db.malicious_reports.find_one({"erased_subject": alias})
    assert report_doc["user_email"] == alias and report_doc["ip"] == "198.51.100.7"
    assert db.cases.find_one({"erased_subject": alias})["meta"]["submitter_name"] == alias
    for collection in ("login_logs", "messages", "malicious_reports", "cases", "board_audit_logs", "admin_logs"):
        _no_trace(db, collection, uid)

    assert db.users.count_documents({"followers": str(uid)}) == 0
    assert db.users.count_documents({"friend_requests.sent_by": uid}) == 0
    assert db.demonstrations.count_documents({"editors": str(uid)}) == 0
    assert db.users.find_one({"_id": friend_id}) is not None

    seal = db[SEAL_COLLECTION].find_one({"_id": alias})
    assert EMAIL not in repr(seal) and str(uid) not in repr(seal)
    identity = open_identity_seal(private_pem, seal["seal"])
    assert identity["user_id"] == str(uid) and identity["email"] == EMAIL


def test_privileged_account_keeps_every_record_pseudonymised(db, seeded_data):
    uid = ObjectId()
    _seed(db, uid, seeded_data["friend_id"], seeded_data["demo_id"])
    user = _user(uid, role="admin", global_permissions=["DELETE_USER"])
    assert is_privileged(user)

    report = erase_user(db, user, SECRET)

    assert report["privileged"] is True and report["sealed"] is False
    alias = pseudonym(SECRET, uid)
    for collection in ("password_changes", "notifications", "demo_attending", "board_clearances"):
        assert db[collection].count_documents({"erased_subject": alias}) == 1, collection
        _no_trace(db, collection, uid)
    for collection in ("mfas", "api_tokens", "user_settings"):
        assert db[collection].count_documents({"user_id": uid}) == 0, collection


def test_other_accounts_untouched_and_rerun_is_harmless(db, seeded_data):
    other = seeded_data["friend_id"]
    db.user_settings.insert_one({"user_id": other, "language": "fi"})
    db.login_logs.insert_one({"user_id": other, "ip": "203.0.113.1"})
    uid = ObjectId()
    db.login_logs.insert_one({"user_id": uid, "ip": "203.0.113.2"})

    erase_user(db, _user(uid), SECRET)
    again = erase_user(db, _user(uid), SECRET)

    assert again["pseudonymised"] == {} and again["deleted"] == {}
    assert db.user_settings.count_documents({"user_id": other}) == 1
    assert db.login_logs.count_documents({"user_id": other}) == 1


def test_retention_drops_seal_and_kept_records_but_not_audit(db, monkeypatch):
    monkeypatch.setenv("ERASURE_RETENTION_DAYS", "730")
    old = utcnow() - datetime.timedelta(days=800)
    recent = utcnow() - datetime.timedelta(days=10)
    db.login_logs.insert_many([
        {"erased_subject": "poistettu-a", "erased_at": old},
        {"erased_subject": "poistettu-b", "erased_at": recent},
        {"user_id": ObjectId(), "timestamp": old},
    ])
    db.admin_logs.insert_one({"erased_subject": "poistettu-a", "erased_at": old})
    db[SEAL_COLLECTION].insert_many([{"_id": "poistettu-a", "created_at": old},
                                     {"_id": "poistettu-b", "created_at": recent}])

    dry = purge_expired(db, dry_run=True)
    assert dry["counts"]["login_logs"] == 1 and db.login_logs.count_documents({}) == 3

    purge_expired(db)

    assert db.login_logs.count_documents({}) == 2
    assert db.login_logs.count_documents({"erased_subject": "poistettu-a"}) == 0
    assert db.admin_logs.count_documents({"erased_subject": "poistettu-a"}) == 1
    assert [doc["_id"] for doc in db[SEAL_COLLECTION].find()] == ["poistettu-b"]


def test_retention_days_has_a_floor(monkeypatch):
    monkeypatch.setenv("ERASURE_RETENTION_DAYS", "1")
    assert user_erasure.retention_days() == 30
    monkeypatch.setenv("ERASURE_RETENTION_DAYS", "nope")
    assert user_erasure.retention_days() == 730


def test_admin_delete_runs_erasure(app, db, seeded_data):
    uid = seeded_data["user_id"]
    db.mfas.insert_one({"user_id": uid, "secret": "x"})
    db.login_logs.insert_one({"user_id": uid, "ip": "198.51.100.9"})
    client = _client_for_user(app, seeded_data["admin_id"])
    r = client.post("/users/auth/api/v2/step-up/fresh/options",
                    json={"action": "delete_user", "target_id": str(uid)})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.post("/users/auth/api/v2/step-up/fresh/password", json={"password": "AdminPass1!"})
    assert r.status_code == 200, r.get_data(as_text=True)
    token = r.get_json()["fresh_step_up_token"]

    r = client.post("/admin/user/delete_user", json={"user_id": str(uid), "fresh_step_up_token": token})

    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.users.find_one({"_id": uid}) is None
    assert db.mfas.count_documents({"user_id": uid}) == 0
    assert db.login_logs.count_documents({"user_id": uid}) == 0
    assert db.login_logs.count_documents({"erased_subject": {"$exists": True}, "ip": "198.51.100.9"}) == 1
