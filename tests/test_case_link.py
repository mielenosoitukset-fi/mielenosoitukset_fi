"""Scoped case links in admin emails (maintainer, 20 Nov 2026).

The link opens one demonstration without login and lets that admin approve or
reject it. It never logs anyone in, binds to the first browser, needs the
admin's TOTP code when they use two-factor, and stops working after a decision.
"""

import re

import pyotp
from bson import ObjectId


def _link(app, db, seeded_data, user_id=None):
    from mielenosoitukset_fi.admin.admin_demo_bp import generate_demo_approve_link

    user_id = user_id or seeded_data["admin_id"]
    with app.test_request_context(base_url="http://localhost"):
        url = generate_demo_approve_link(
            str(seeded_data["pending_demo_id"]),
            recipient={"email": "admin@example.test", "user_id": str(user_id), "username": "admin"},
        )
    assert "/admin/demo/case_link/" in url
    return url.replace("http://localhost", "")


def _csrf(body):
    return re.search(r'name="csrf" value="([^"]+)"', body).group(1)


def _open(browser, path):
    """GET shows an open button (scanners stop here); the POST binds this browser."""
    first = browser.get(path)
    assert first.status_code == 200 and "Avaa tässä selaimessa" in first.get_data(as_text=True)
    return browser.post(path, data={"csrf": _csrf(first.get_data(as_text=True)), "open": "1"})


def test_link_opens_one_demo_without_login_and_binds_browser(app, db, seeded_data):
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    page = _open(browser, path)
    body = page.get_data(as_text=True)
    assert page.status_code == 200, body[:500]
    assert "Pending Demonstration" in body and "Hyväksy ja julkaise" in body
    assert page.headers["Cache-Control"] == "no-store"

    # It never logs in: the rest of the admin still needs a login.
    assert browser.get("/admin/dashboard").status_code in (302, 401, 403)
    # Another browser cannot use the same link, and plain GETs never bind.
    assert app.test_client().get(path).status_code == 403


def test_prefetch_does_not_claim_the_link(app, db, seeded_data):
    path = _link(app, db, seeded_data)
    scanner = app.test_client()
    scanner.get(path)
    scanner.get(path)
    assert _open(app.test_client(), path).status_code == 200


def test_approve_through_link_uses_decision_service_and_ends_link(app, db, seeded_data):
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    csrf = _csrf(_open(browser, path).get_data(as_text=True))

    response = browser.post(path, data={"csrf": csrf, "decision": "approved"})

    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert demo["approved"] is True
    decision = demo["moderation_decision"]
    assert decision["source"] == "case_link"
    assert decision["actor"]["authentication"] == "case_link"
    assert browser.get(path).status_code == 410


def test_missing_csrf_is_refused(app, db, seeded_data):
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    _open(browser, path)
    assert browser.post(path, data={"decision": "approved"}).status_code == 400
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["approved"] is False


def test_reject_needs_reason_and_records_it(app, db, seeded_data):
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    csrf = _csrf(_open(browser, path).get_data(as_text=True))
    assert browser.post(path, data={"csrf": csrf, "decision": "rejected"}).status_code == 400

    response = browser.post(path, data={"csrf": csrf, "decision": "rejected", "reason_key": "spam"})

    assert response.status_code == 200
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["rejected"] is True


def test_two_factor_user_needs_a_code(app, db, seeded_data):
    secret = pyotp.random_base32()
    db.users.update_one({"_id": seeded_data["admin_id"]}, {"$set": {"mfa_enabled": True}})
    db.mfas.insert_one({"user_id": seeded_data["admin_id"], "secret": secret})
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    body = _open(browser, path).get_data(as_text=True)
    assert "Vahvistuskoodi" in body
    csrf = _csrf(body)

    assert browser.post(path, data={"csrf": csrf, "decision": "approved"}).status_code == 400
    assert browser.post(path, data={"csrf": csrf, "decision": "approved", "code": "000000"}).status_code == 400
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["approved"] is False

    ok = browser.post(path, data={"csrf": csrf, "decision": "approved", "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert demo["approved"] is True and demo["moderation_decision"]["actor"]["second_factor"] == "totp"


def test_too_many_bad_codes_revoke_the_link(app, db, seeded_data):
    db.users.update_one({"_id": seeded_data["admin_id"]}, {"$set": {"mfa_enabled": True}})
    db.mfas.insert_one({"user_id": seeded_data["admin_id"], "secret": pyotp.random_base32()})
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    csrf = _csrf(_open(browser, path).get_data(as_text=True))
    for _ in range(5):
        browser.post(path, data={"csrf": csrf, "decision": "approved", "code": "123456"})
    assert browser.get(path).status_code == 410


def test_passkey_only_account_must_log_in(app, db, seeded_data):
    from mielenosoitukset_fi.utils.webauthn_utils import PASSKEY_COLLECTION

    db[PASSKEY_COLLECTION].insert_one({"user_id": seeded_data["admin_id"], "credential_id": "x"})
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    body = _open(browser, path).get_data(as_text=True)
    assert "pääsyavainta" in body and 'name="csrf"' not in body
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["approved"] is False


def test_link_follows_current_rights(app, db, seeded_data):
    path = _link(app, db, seeded_data)
    db.users.update_one({"_id": seeded_data["admin_id"]}, {"$set": {"active": False}})
    assert app.test_client().get(path).status_code == 403


def test_decision_elsewhere_ends_the_link(app, admin_client, db, seeded_data):
    path = _link(app, db, seeded_data)
    assert admin_client.post(f"/api/admin/demo/{seeded_data['pending_demo_id']}/approve", json={}).status_code == 200
    assert app.test_client().get(path).status_code == 410


def test_unknown_recipient_gets_the_login_link(app, seeded_data):
    from mielenosoitukset_fi.admin.admin_demo_bp import generate_demo_approve_link

    with app.test_request_context(base_url="http://localhost"):
        url = generate_demo_approve_link(str(seeded_data["pending_demo_id"]), recipient={"email": "x@example.test"})
    assert "/decide" in url and "case_link" not in url


def test_forged_token_is_unknown(app, seeded_data):
    assert app.test_client().get(f"/admin/demo/case_link/{ObjectId()}").status_code == 404
