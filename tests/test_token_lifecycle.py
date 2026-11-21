"""SEC-04: regression tests for token and approval lifecycles.

One test per documented behaviour in docs/token-lifecycle.md: expiry, replay,
revocation and repeated decisions for each bearer link.
"""

import hashlib
from datetime import timedelta

import jwt
import pytest


def _expired_jwt(app, claims):
    from mielenosoitukset_fi.utils.time_utils import utcnow

    return jwt.encode(
        {**claims, "exp": utcnow() - timedelta(minutes=1)},
        app.config["SECRET_KEY"],
        algorithm="HS256",
    )


# --- Organizer cancellation link -------------------------------------------


def test_cancellation_link_is_single_use(client, db, seeded_data):
    token = seeded_data["cancel_token"]
    first = client.post(f"/cancel_demonstration/{token}", data={"reason": "x"})
    assert first.status_code == 302
    used = db.demo_cancellation_tokens.find_one({"demo_id": seeded_data["demo_id"]})
    assert used["used_at"] is not None

    replay = client.post(f"/cancel_demonstration/{token}", data={"reason": "again"})
    assert replay.status_code == 200
    assert "jo käytetty" in replay.get_data(as_text=True)
    after = db.demo_cancellation_tokens.find_one({"_id": used["_id"]})
    assert after["used_at"] == used["used_at"]
    assert after["used_reason"] == "x"


def test_expired_cancellation_link_cannot_cancel(client, db, seeded_data):
    from mielenosoitukset_fi.utils.time_utils import utcnow

    db.demo_cancellation_tokens.update_many(
        {}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}}
    )
    response = client.post(
        f"/cancel_demonstration/{seeded_data['cancel_token']}", data={"reason": "x"}
    )
    assert response.status_code == 200
    assert "vanhentunut" in response.get_data(as_text=True)
    demo = db.demonstrations.find_one({"_id": seeded_data["demo_id"]})
    assert not demo.get("cancelled")
    assert db.demo_cancellation_tokens.find_one({})["used_at"] is None


def test_unknown_cancellation_link_changes_nothing(client, db, seeded_data):
    response = client.post("/cancel_demonstration/not-a-token", data={"reason": "x"})
    assert response.status_code == 200
    assert not db.demonstrations.find_one({"_id": seeded_data["demo_id"]}).get(
        "cancelled"
    )


# --- Demonstration approve / reject / preview / edit links ------------------


def test_expired_preview_link_is_gone(client, db, seeded_data):
    from mielenosoitukset_fi.utils.time_utils import utcnow

    db.magic_links.update_many(
        {"demo_id": str(seeded_data["pending_demo_id"])},
        {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}},
    )
    response = client.get(
        f"/admin/demo/preview_demo_with_token/{seeded_data['preview_token']}"
    )
    assert response.status_code == 410


@pytest.mark.parametrize("route", ["approve", "reject"])
@pytest.mark.parametrize("state", ["valid", "expired", "revoked"])
def test_old_decision_links_open_the_case_and_never_decide(
    client, db, seeded_data, route, state
):
    """Maintainer decision (20 Nov 2026): email links open the case only."""
    from mielenosoitukset_fi.utils.time_utils import utcnow

    demo_id = seeded_data["pending_demo_id"]
    if state == "expired":
        db.magic_links.update_many(
            {"demo_id": str(demo_id)}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}}
        )
    if state == "revoked":
        db.magic_links.update_many({"demo_id": str(demo_id)}, {"$set": {"revoked": True}})
    token = seeded_data[f"{route}_token"]
    for method in (client.get, client.post):
        response = method(f"/admin/demo/{route}_demo_with_token/{token}")
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/admin/demo/{demo_id}/decide")
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert not demo.get("approved") and not demo.get("rejected")
    assert "moderation_decision" not in demo


def test_forged_decision_link_is_not_found(app, client, db, seeded_data):
    from itsdangerous import URLSafeTimedSerializer
    from mielenosoitukset_fi.admin.admin_demo_bp import serializer

    with app.app_context():
        tampered = serializer.dumps(str(seeded_data["pending_demo_id"]), salt="approve-demo") + "x"
    old_key = URLSafeTimedSerializer("your_secret_key").dumps(
        str(seeded_data["pending_demo_id"]), salt="approve-demo"
    )
    for token in (tampered, old_key):
        assert client.post(f"/admin/demo/approve_demo_with_token/{token}").status_code == 404
    assert not db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]}).get("approved")


def test_decide_link_requires_login_and_leads_to_the_case(admin_client, client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    anonymous = client.get(f"/admin/demo/{demo_id}/decide")
    assert anonymous.status_code in (302, 401)
    assert "/admin/case/" not in anonymous.headers.get("Location", "")
    case = db.cases.find_one({"type": "new_demo", "demo_id": demo_id})
    response = admin_client.get(f"/admin/demo/{demo_id}/decide")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/case/{case['_id']}/")


# --- Password reset and e-mail confirmation (signed JWT, one hour) ----------


def test_reset_token_is_single_use(client, db, seeded_data):
    token = seeded_data["reset_token"]
    new_password = "Zq7-correct-horse-battery-staple"
    first = client.post(
        f"/users/auth/password_reset/{token}",
        data={"password": new_password, "password_confirm": new_password},
    )
    assert first.status_code == 302
    assert db.used_password_change_tokens.count_documents({"token_hash": hashlib.sha256(token.encode()).hexdigest()}) == 1
    before = db.users.find_one({"email": "alice@example.test"})["password_hash"]

    other = "Another-Strong-Passphrase-9!"
    replay = client.post(
        f"/users/auth/password_reset/{token}",
        data={"password": other, "password_confirm": other},
    )
    assert replay.status_code == 302
    assert db.users.find_one({"email": "alice@example.test"})["password_hash"] == before
    assert db.used_password_change_tokens.count_documents({"token_hash": hashlib.sha256(token.encode()).hexdigest()}) == 1


def test_expired_and_garbage_reset_tokens_change_nothing(app, client, db):
    before = db.users.find_one({"email": "alice@example.test"})["password_hash"]
    password = "Zq7-correct-horse-battery-staple"
    for token in (
        _expired_jwt(app, {"email": "alice@example.test", "purpose": "password_reset"}),
        "garbage",
    ):
        response = client.post(
            f"/users/auth/password_reset/{token}",
            data={"password": password, "password_confirm": password},
        )
        assert response.status_code == 302
    assert db.users.find_one({"email": "alice@example.test"})["password_hash"] == before
    assert db.used_password_change_tokens.count_documents({}) == 0


def test_reset_and_confirmation_tokens_are_not_interchangeable(app):
    from mielenosoitukset_fi.utils.auth import (
        generate_confirmation_token,
        generate_reset_token,
        verify_confirmation_token,
        verify_reset_token,
    )

    with app.app_context():
        reset = generate_reset_token("a@example.test")
        confirm = generate_confirmation_token("a@example.test")
        assert verify_reset_token(reset) == "a@example.test"
        assert verify_confirmation_token(confirm) == "a@example.test"
        assert verify_reset_token(confirm) is None
        assert verify_confirmation_token(reset) is None


def test_links_issued_before_the_purpose_claim_still_work(app):
    from mielenosoitukset_fi.utils.auth import (
        _generate_token,
        verify_confirmation_token,
        verify_reset_token,
    )

    with app.app_context():
        legacy = _generate_token({"email": "a@example.test"}, 3600)
        assert verify_reset_token(legacy) == "a@example.test"
        assert verify_confirmation_token(legacy) == "a@example.test"


def test_expired_confirmation_link_does_not_confirm(app, client, db):
    db.users.update_one({"email": "alice@example.test"}, {"$set": {"confirmed": False}})
    token = _expired_jwt(
        app, {"email": "alice@example.test", "purpose": "email_confirmation"}
    )
    client.get(f"/users/auth/confirm_email/{token}")
    assert db.users.find_one({"email": "alice@example.test"})["confirmed"] is False


def test_confirmation_link_replay_is_harmless(client, db, seeded_data):
    db.users.update_one({"email": "alice@example.test"}, {"$set": {"confirmed": False}})
    url = f"/users/auth/confirm_email/{seeded_data['confirmation_token']}"
    assert client.get(url).status_code == 302
    assert client.get(url).status_code == 302
    assert db.users.find_one({"email": "alice@example.test"})["confirmed"] is True


# --- Campaign volunteer confirmation ----------------------------------------


def _volunteer(db, **extra):
    from mielenosoitukset_fi.utils.time_utils import utcnow

    doc = {
        "name": "V",
        "email": "v@example.test",
        "confirmed": False,
        "confirmation_token": "vol-token",
        "confirmation_expires": utcnow() + timedelta(hours=1),
        **extra,
    }
    db.volunteers.insert_one(doc)
    return doc


def test_volunteer_confirmation_is_consumed_on_use(client, db):
    _volunteer(db)
    assert client.get("/kampanja/confirm/vol-token").status_code == 200
    volunteer = db.volunteers.find_one({"email": "v@example.test"})
    assert volunteer["confirmed"] is True
    assert "confirmation_token" not in volunteer
    assert client.get("/kampanja/confirm/vol-token").status_code == 400


def test_expired_volunteer_confirmation_does_not_confirm(client, db):
    from mielenosoitukset_fi.utils.time_utils import utcnow

    _volunteer(db, confirmation_expires=utcnow() - timedelta(seconds=1))
    assert client.get("/kampanja/confirm/vol-token").status_code == 400
    assert db.volunteers.find_one({"email": "v@example.test"})["confirmed"] is False


def test_cancellation_link_is_claimed_atomically(client, db, seeded_data):
    from mielenosoitukset_fi.utils.demo_cancellation import (
        claim_cancellation_token,
        release_cancellation_token,
    )

    record = db.demo_cancellation_tokens.find_one({})
    assert claim_cancellation_token(record["_id"]) is True
    assert claim_cancellation_token(record["_id"]) is False
    release_cancellation_token(record["_id"])
    assert db.demo_cancellation_tokens.find_one({"_id": record["_id"]})["used_at"] is None
    assert claim_cancellation_token(record["_id"]) is True


def test_failed_cancellation_releases_the_link(client, db, seeded_data, monkeypatch):
    from importlib import import_module

    def boom(*args, **kwargs):
        raise RuntimeError("down")

    for name in ("mielenosoitukset_fi.basic_routes", "basic_routes"):
        try:
            monkeypatch.setattr(import_module(name), "cancel_demo", boom)
        except ImportError:
            pass
    client.post(f"/cancel_demonstration/{seeded_data['cancel_token']}", data={"reason": "x"})
    assert db.demo_cancellation_tokens.find_one({})["used_at"] is None

def test_reset_token_is_stored_hashed_and_legacy_rows_are_still_honoured(
    app, client, db, seeded_data
):
    token = seeded_data["reset_token"]
    password = "Zq7-correct-horse-battery-staple"
    client.post(
        f"/users/auth/password_reset/{token}",
        data={"password": password, "password_confirm": password},
    )
    row = db.used_password_change_tokens.find_one({})
    assert "token" not in row and row["password_change_success"] is True
    assert not db.password_changes.find_one({"token": {"$exists": True}})

    # A raw row written before hashing existed still blocks reuse, and is
    # rewritten to a hash when it is met.
    legacy = _expired_jwt(app, {"email": "alice@example.test"}).replace("a", "b", 1)
    db.used_password_change_tokens.insert_one({"token": legacy, "email": "x"})
    client.get(f"/users/auth/password_reset/{legacy}")
    migrated = db.used_password_change_tokens.find_one({"email": "x"})
    assert "token" not in migrated and "token_hash" in migrated


def test_claimed_reset_token_blocks_a_second_submission(
    client, db, seeded_data, monkeypatch
):
    from importlib import import_module

    token = seeded_data["reset_token"]
    # Simulate the race: both requests passed the "already used" read, the
    # other one has since claimed the token. Only the unique claim stops us.
    for name in ("mielenosoitukset_fi.users.BPs.auth", "users.BPs.auth"):
        try:
            monkeypatch.setattr(
                import_module(name), "_reset_token_spent", lambda *a, **k: False
            )
        except ImportError:
            pass
    db.used_password_change_tokens.insert_one(
        {
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "password_change_success": False,
        }
    )
    before = db.users.find_one({"email": "alice@example.test"})["password_hash"]
    password = "Zq7-correct-horse-battery-staple"
    client.post(
        f"/users/auth/password_reset/{token}",
        data={"password": password, "password_confirm": password},
    )
    assert db.users.find_one({"email": "alice@example.test"})["password_hash"] == before


def test_admin_email_decision_link_is_the_case_link(app, seeded_data):
    """Email links carry no bearer token; login and case rights decide access."""
    from mielenosoitukset_fi.admin.admin_demo_bp import (
        generate_demo_approve_link,
        generate_demo_reject_link,
    )

    demo_id = str(seeded_data["pending_demo_id"])
    with app.test_request_context("/"):
        assert generate_demo_approve_link(demo_id).endswith(f"/admin/demo/{demo_id}/decide")
        assert generate_demo_reject_link(demo_id) == generate_demo_approve_link(demo_id)
