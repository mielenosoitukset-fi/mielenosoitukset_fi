"""Scoped case links in admin emails (maintainer, 20 Nov 2026; one page, 21 Nov 2026).

The link opens the demonstration's case page, the same page as in Tapaukset,
without login and lets that admin approve, reject or escalate that case only.
It never logs anyone in, binds to the first browser, needs the admin's TOTP
code when they use two-factor, and stops working for decisions after one.
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
    """GET shows an open button (scanners stop here); the POST binds this browser and shows the case."""
    first = browser.get(path)
    assert first.status_code == 200 and "Avaa ilmoitus" in first.get_data(as_text=True)
    return browser.post(path, data={"csrf": _csrf(first.get_data(as_text=True)), "open": "1"})


def _case_path(db, seeded_data):
    return f"/admin/case/{_case(db, seeded_data)['_id']}/"


def _act(browser, db, seeded_data, action, **extra):
    return browser.post(
        _case_path(db, seeded_data) + "demo_action", json={"action": action, **extra}
    )


def test_link_opens_the_case_page_without_login_and_binds_browser(app, db, seeded_data):
    _case(db, seeded_data)
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    opened = _open(browser, path)
    assert opened.status_code == 302 and opened.headers["Location"] == _case_path(db, seeded_data)
    page = browser.get(opened.headers["Location"])
    body = page.get_data(as_text=True)
    assert page.status_code == 200, body[:500]
    assert "Pending Demonstration" in body and "Hyväksy mielenosoitus" in body and "Hylkää mielenosoitus" in body
    # Same page, without the admin menu and without the submitter's contact details.
    assert 'id="sidebarMenu"' not in body and "Piilotettu" in body and "Sisäiset muistiinpanot" not in body

    # It never logs in: the rest of the admin still needs a login.
    with browser.session_transaction() as session:
        assert "_user_id" not in session
    assert browser.get("/admin/dashboard").status_code in (302, 401, 403)
    assert browser.get("/admin/case/").status_code in (302, 401, 403)
    # Another browser cannot use the same link, and plain GETs never bind.
    assert app.test_client().get(path).status_code == 403


def test_link_reaches_only_its_own_case(app, db, seeded_data):
    _case(db, seeded_data)
    other = db.cases.insert_one({"type": "new_demo", "demo_id": ObjectId(), "meta": {}}).inserted_id
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    assert browser.get(f"/admin/case/{other}/").status_code in (302, 401, 403)
    assert browser.post(f"/admin/case/{other}/demo_action", json={"action": "accept"}).status_code in (302, 401, 403)
    assert browser.get(f"/admin/case/{_case(db, seeded_data)['_id']}/").status_code == 200


def test_link_cannot_do_the_logged_in_only_actions(app, db, seeded_data):
    _case(db, seeded_data)
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    for action in ("take_down", "undo_approval"):
        assert _act(browser, db, seeded_data, action).status_code == 403
    assert browser.post(_case_path(db, seeded_data) + "close/").status_code in (302, 401, 403)


def test_prefetch_does_not_claim_the_link(app, db, seeded_data):
    _case(db, seeded_data)
    path = _link(app, db, seeded_data)
    scanner = app.test_client()
    scanner.get(path)
    scanner.get(path)
    assert _open(app.test_client(), path).status_code == 302


def test_approve_through_link_uses_decision_service_and_ends_link(app, db, seeded_data):
    _case(db, seeded_data)
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    _open(browser, path)

    response = _act(browser, db, seeded_data, "accept")

    assert response.status_code == 200, response.get_data(as_text=True)
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert demo["approved"] is True
    decision = demo["moderation_decision"]
    assert decision["source"] == "case_link"
    assert decision["actor"]["authentication"] == "case_link"
    # The page stays readable in this browser and says what happened; the link decides nothing more.
    after = browser.get(_case_path(db, seeded_data))
    assert after.status_code == 200 and "Päätös on tehty" in after.get_data(as_text=True)
    assert _act(browser, db, seeded_data, "reject", reason_key="spam").status_code in (302, 401, 403)
    assert app.test_client().get(path).status_code == 410


def test_reject_needs_reason_and_records_it(app, db, seeded_data):
    _case(db, seeded_data)
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    assert _act(browser, db, seeded_data, "reject").status_code == 400

    response = _act(browser, db, seeded_data, "reject", reason_key="spam")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["rejected"] is True


def test_two_factor_user_needs_a_code(app, db, seeded_data):
    _case(db, seeded_data)
    secret = pyotp.random_base32()
    db.users.update_one({"_id": seeded_data["admin_id"]}, {"$set": {"mfa_enabled": True}})
    db.mfas.insert_one({"user_id": seeded_data["admin_id"], "secret": secret})
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    _open(browser, path)

    assert _act(browser, db, seeded_data, "accept").status_code == 400
    assert _act(browser, db, seeded_data, "accept", code="000000").status_code == 400
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["approved"] is False

    ok = _act(browser, db, seeded_data, "accept", code=pyotp.TOTP(secret).now())
    assert ok.status_code == 200, ok.get_data(as_text=True)
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert demo["approved"] is True and demo["moderation_decision"]["actor"]["second_factor"] == "totp"


def test_too_many_bad_codes_revoke_the_link(app, db, seeded_data):
    _case(db, seeded_data)
    db.users.update_one({"_id": seeded_data["admin_id"]}, {"$set": {"mfa_enabled": True}})
    db.mfas.insert_one({"user_id": seeded_data["admin_id"], "secret": pyotp.random_base32()})
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    _open(browser, path)
    for _ in range(5):
        _act(browser, db, seeded_data, "accept", code="123456")
    assert app.test_client().get(path).status_code == 410
    assert browser.get(_case_path(db, seeded_data)).status_code in (302, 401, 403)


def test_passkey_only_account_must_log_in(app, db, seeded_data):
    from mielenosoitukset_fi.utils.webauthn_utils import PASSKEY_COLLECTION

    _case(db, seeded_data)
    db[PASSKEY_COLLECTION].insert_one({"user_id": seeded_data["admin_id"], "credential_id": "x"})
    path = _link(app, db, seeded_data)
    browser = app.test_client()
    _open(browser, path)
    body = browser.get(_case_path(db, seeded_data)).get_data(as_text=True)
    assert "pääsyavainta" in body and "onclick=\"approveDemo()\"" not in body
    assert _act(browser, db, seeded_data, "accept").status_code == 403
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["approved"] is False


def test_link_follows_current_rights(app, db, seeded_data):
    _case(db, seeded_data)
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


def _case(db, seeded_data):
    case = db.cases.find_one({"type": "new_demo", "demo_id": {"$in": [seeded_data["pending_demo_id"], str(seeded_data["pending_demo_id"])]}})
    if not case:
        db.cases.insert_one({"type": "new_demo", "demo_id": seeded_data["pending_demo_id"], "status": "open", "meta": {}})
        case = db.cases.find_one({"type": "new_demo", "demo_id": seeded_data["pending_demo_id"]})
    return case


def test_escalate_through_link_needs_reason_and_keeps_demo_pending(app, db, seeded_data):
    _case(db, seeded_data)
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    url = _case_path(db, seeded_data) + "demo_escalate"

    assert browser.post(url, json={"reason": " "}).status_code == 400
    ok = browser.post(url, json={"reason": "En tunne järjestäjää"})
    assert ok.status_code == 200, ok.get_data(as_text=True)[:500]

    case = _case(db, seeded_data)
    assert case["meta"]["superior_needed"] is True
    entry = case["case_history"][-1]
    assert entry["metadata"]["authentication"] == "case_link" and entry["metadata"]["reason"] == "En tunne järjestäjää"
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert not demo.get("approved") and not demo.get("rejected")
    # Nobody is on duty, so level 1 is skipped: straight to the national team.
    assert case["meta"]["escalation_level"] == 2
    # A full admin can raise it again. No OT member is known, so OT is skipped: Johtoryhmä.
    second = browser.post(url, json={"reason": "Päätös kuuluu Johtoryhmälle"})
    assert second.status_code == 200, second.get_data(as_text=True)[:500]
    assert _case(db, seeded_data)["meta"]["escalation_level"] == 4
    # Johtoryhmä is the last level: nothing above it, nothing duplicated.
    history = len(_case(db, seeded_data)["case_history"])
    third = browser.post(url, json={"reason": "Vielä ylemmäs"})
    assert third.status_code == 409
    assert len(_case(db, seeded_data)["case_history"]) == history


def test_escalation_levels_and_who_may_raise_them():
    from types import SimpleNamespace as U

    from mielenosoitukset_fi.utils import case_escalation as esc

    handler, reviewer = U(global_admin=False), U(global_admin=True)
    assert esc.current_level({}) == 0 and esc.current_level({"superior_needed": True}) == 2
    assert esc.current_level({"superior_needed": True, "escalation_level": 4}) == 4
    assert esc.can_escalate({}, handler)
    assert not esc.can_escalate({"superior_needed": True}, handler)
    assert esc.can_escalate({"superior_needed": True}, reviewer)
    assert not esc.can_escalate({"superior_needed": True, "escalation_level": 4}, reviewer)


def test_levels_skip_empty_duty_and_ot_then_include_them(app, db, seeded_data):
    from datetime import timedelta

    from mielenosoitukset_fi.utils import case_escalation as esc
    from mielenosoitukset_fi.utils import duty_shifts
    from datetime import datetime, timezone

    with app.app_context():
        assert esc.next_target(db, 0)[0] == 2 and esc.next_target(db, 2)[0] == 4
        now = datetime.now(timezone.utc)
        duty_id, ot_id = ObjectId(), ObjectId()
        db.users.insert_many([
            {"_id": duty_id, "username": "vuoro", "email": "v@example.test", "active": True, "confirmed": True},
            {"_id": ot_id, "username": "ot", "email": "ot@example.test", "active": True, "confirmed": True},
        ])
        grant = db[duty_shifts.GRANTS].insert_one({
            "user_id": duty_id, "revoked_at": None, "approvers": [ot_id],
            "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=1),
            "permissions": sorted(duty_shifts.NATIONAL_MODERATOR_V1),
        }).inserted_id
        db[duty_shifts.SHIFTS].insert_one({
            "grant_id": grant, "user_id": duty_id, "starts_at": duty_shifts._naive(now - timedelta(hours=1)),
            "ends_at": duty_shifts._naive(now + timedelta(hours=1)), "approved_at": now, "cancelled_at": None,
        })
        level, users = esc.next_target(db, 0)
        assert level == 1 and [u["username"] for u in users] == ["vuoro"]
        assert esc.next_target(db, 2)[0] == 3
        assert [u["username"] for u in esc.next_target(db, 2)[1]] == ["ot"]
        assert esc.next_target(db, 3)[0] == 4


def test_escalate_through_link_needs_code_and_refuses_passkey_only(app, db, seeded_data):
    _case(db, seeded_data)
    secret = pyotp.random_base32()
    db.users.update_one({"_id": seeded_data["admin_id"]}, {"$set": {"mfa_enabled": True}})
    db.mfas.insert_one({"user_id": seeded_data["admin_id"], "secret": secret})
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    url = _case_path(db, seeded_data) + "demo_escalate"
    form = {"reason": "Tarvitsen apua"}
    assert browser.post(url, json=form).status_code == 400
    assert not _case(db, seeded_data).get("meta", {}).get("superior_needed")
    assert browser.post(url, json={**form, "code": pyotp.TOTP(secret).now()}).status_code == 200
    assert _case(db, seeded_data)["meta"]["superior_needed"] is True


def test_reject_form_offers_the_matching_johtoryhma_decision_first(app, db, seeded_data):
    from mielenosoitukset_fi.utils import moderation_blocks

    _case(db, seeded_data)
    demo_id = str(seeded_data["pending_demo_id"])
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    page = _case_path(db, seeded_data)
    assert "jory_decision" not in browser.get(page).get_data(as_text=True)
    # Without a decision on this demonstration nobody can claim one.
    refused = _act(browser, db, seeded_data, "reject", reason_key="jory_decision")
    assert refused.status_code == 400
    assert db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})["rejected"] is False

    moderation_blocks.sync(db, [{"id": "pre-1", "kind": "demo", "organizer": "Tämä mielenosoitus",
                                 "organization_id": demo_id, "reason": "Ei sovi periaatteisiin",
                                 "from": "2026-11-01", "until": "", "source": "MOSO/JORY/ENN/2026/007"}])
    try:
        shown = browser.get(page).get_data(as_text=True)
        assert "jory_decision" in shown and "MOSO/JORY/ENN/2026/007" in shown
        ok = _act(browser, db, seeded_data, "reject", reason_key="jory_decision")
        assert ok.status_code == 200, ok.get_data(as_text=True)[:400]
        case = _case(db, seeded_data)
        entry = next(e for e in case["case_history"] if e.get("mech_action") == "reject_demo")
        assert entry["metadata"]["decision_id"] == "MOSO/JORY/ENN/2026/007"
        assert "Johtoryhmän päätös MOSO/JORY/ENN/2026/007" in entry["metadata"]["reason"]
    finally:
        db[moderation_blocks.COLLECTION].delete_many({"_id": "pre-1"})


def test_link_can_preview_the_decision_effects_of_its_own_demo_only(app, db, seeded_data):
    _case(db, seeded_data)
    browser = app.test_client()
    _open(browser, _link(app, db, seeded_data))
    demo_id = seeded_data["pending_demo_id"]
    for decision in ("approved", "rejected"):
        response = browser.get(f"/api/admin/demo/{demo_id}/decision_preview?decision={decision}")
        assert response.status_code == 200 and response.get_json()["success"] is True
    # Not another demonstration.
    assert browser.get(f"/api/admin/demo/{seeded_data['demo_id']}/decision_preview?decision=approved").status_code in (302, 401, 403)
