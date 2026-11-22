"""Admin account review before 1.1.2027: stamp, lapse on read, confirm by order."""

from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils import access_review
from tests.conftest import _user_doc
from tests.test_flask_orders import _order, _roster, _send, orders  # noqa: F401  (orders is a fixture)
from tests.test_hallinto_city_grant_api import api  # noqa: F401

AFTER = access_review.DEADLINE + timedelta(minutes=1)


@pytest.fixture
def clean_users(db):
    db.users.delete_many({})
    db.email_queue.delete_many({})
    yield


def _add(db, name, **fields):
    uid = ObjectId()
    db.users.insert_one(_user_doc(name, f"{name}@example.test", "Passw0rd!x", _id=uid, **fields))
    return uid


def test_stamp_finds_every_source_of_admin_rights(app, db, clean_users):
    with app.app_context():
        admin = _add(db, "ylla", role="admin", global_permissions=["VIEW_LOGS"])
        flag = _add(db, "lippu", global_admin=True)
        perms = _add(db, "oikeudet", global_permissions=["EDIT_DEMO"])
        translator = _add(db, "kaantaja", global_permissions=["TRANSLATE_UI"])
        _add(db, "tavallinen")
        _add(db, "orgtili", role="admin")  # organisation admins carry the role without site rights
        _add(db, "poissa", role="admin", active=False)
        dry = access_review.stamp(db)
        assert {a["username"] for a in dry["accounts"]} == {"ylla", "lippu", "oikeudet", "kaantaja"}
        assert dry["newly_stamped"] == 0 and not db.users.find_one({"_id": admin}).get("access_review")
        done = access_review.stamp(db, apply=True)
        assert done["newly_stamped"] == 4
        again = access_review.stamp(db, apply=True)
        assert again["newly_stamped"] == 0
        by = {a["username"]: a["holds"] for a in done["accounts"]}
        assert "Yleisoikeudet (koko sivusto)" in by["oikeudet"] and by["kaantaja"] == ["Kääntäjä"]


def test_unreviewed_account_lapses_after_deadline_only_when_enforced(app, db, clean_users):
    with app.app_context():
        uid = _add(db, "ylla", role="admin", global_permissions=["VIEW_LOGS"])
        access_review.stamp(db, apply=True)
        doc = db.users.find_one({"_id": uid})
        assert not access_review.lapsed_doc(doc, AFTER)  # switch off
        app.config["ACCESS_REVIEW_ENFORCE"] = True
        assert not access_review.lapsed_doc(doc, access_review.DEADLINE - timedelta(minutes=1))
        assert access_review.lapsed_doc(doc, AFTER)
        user = User.from_db({**doc, "access_review": {**doc["access_review"], "deadline": datetime(2020, 1, 1)}})
        assert user.role == "user" and user.global_permissions == [] and not user.has_admin_access()


def test_protected_and_confirmed_accounts_never_lapse(app, db, clean_users):
    with app.app_context():
        olivia = _add(db, "olivia", role="global_admin")
        other = _add(db, "toinen", role="global_admin")
        reviewed = _add(db, "tarkistettu", role="admin", global_permissions=["VIEW_LOGS"])
        access_review.stamp(db, apply=True)
        app.config.update(ACCESS_REVIEW_ENFORCE=True, OVERRIDE_OLIVIA_USER_ID=str(olivia))
        access_review.confirm(db, [str(reviewed)], decision_id="d1", decision_revision=1, order_id="o1", actor_id=olivia)
        for uid, lapsed in ((olivia, False), (other, True), (reviewed, False)):
            assert access_review.lapsed_doc(db.users.find_one({"_id": uid}), AFTER) is lapsed
        states = {i["username"]: i["state"] for i in access_review.hallinto_list(db, AFTER)}
        assert states == {"olivia": "protected", "toinen": "lapsed", "tarkistettu": "confirmed"}
        # The confirmed account's next deadline is one round on: 1.7.2027, then it is open again near it.
        stored = db.users.find_one({"_id": reviewed})["access_review"]
        assert stored["deadline"] == datetime(2027, 6, 30, 21, 0)
        near = stored["deadline"] - timedelta(days=10)
        assert not access_review.confirmable(db, [str(reviewed)], AFTER)
        assert access_review.confirmable(db, [str(reviewed)], near)


def test_rounds_are_1_1_and_1_7(app):
    nxt = access_review.next_round
    assert nxt(datetime(2026, 11, 22)) == datetime(2026, 12, 31, 22, 0)
    assert nxt(datetime(2026, 12, 31, 22, 0)) == datetime(2027, 6, 30, 21, 0)
    assert nxt(datetime(2027, 6, 30, 21, 0)) == datetime(2027, 12, 31, 22, 0)


def test_reminders_only_on_reminder_days(app, db, clean_users):
    with app.app_context():
        _add(db, "ylla", role="admin", global_permissions=["VIEW_LOGS"])
        access_review.stamp(db, apply=True)
        day = access_review.DEADLINE - timedelta(days=7, hours=1)
        assert access_review.send_reminders(db, day, dry_run=True)["queued"] == 0
        assert access_review.send_reminders(db, day, dry_run=False)["queued"] == 1
        access_review.send_reminders(db, day, dry_run=False)
        assert db.email_queue.count_documents({}) == 1  # one mail per person, deadline and day
        assert access_review.send_reminders(db, access_review.DEADLINE - timedelta(days=5), dry_run=False)["queued"] == 0


def test_order_confirms_stamped_accounts_and_refuses_others(orders):
    env = orders
    app, client, db, seeded, person = env[:5]
    a, b = person("aino", role="admin", global_permissions=["VIEW_LOGS"]), person("eero")
    with app.app_context():
        access_review.stamp(db, apply=True)
        assert db.users.find_one({"_id": b}).get("access_review") is None
    body, digest = _order("access_review", roster=_roster(a))
    response, _ = _send(env, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["effective"] == {"roster": _roster(a)}
    assert db.users.find_one({"_id": a})["access_review"]["decision_id"] == "synthetic-order"
    body, digest = _order("access_review", order_id="hallinto-order-0000000000002", roster=_roster(b))
    response, _ = _send(env, body, digest, key="hallinto-order-0000000000002")
    assert response.get_json().get("status") != "confirmed"
    assert db.users.find_one({"_id": b}).get("access_review") is None


def test_list_reports_removable_roles_and_marks_removed_accounts(app, db, clean_users):
    with app.app_context():
        uid = _add(db, "poistuva", role="admin", global_admin=True, global_permissions=["VIEW_LOGS"])
        access_review.stamp(db, apply=True)
        item = access_review.hallinto_list(db)[0]
        assert item["state"] == "open" and item["roles"] == ["admin", "global_admin"]
        db.users.update_one({"_id": uid}, {"$set": {"role": "user", "global_admin": False, "global_permissions": []}})
        item = access_review.hallinto_list(db)[0]
        assert item["state"] == "removed" and item["roles"] == []
        assert not access_review.confirmable(db, [str(uid)])


def test_contact_is_off_by_default_and_records_one_mail_per_round(app, db, clean_users):
    with app.app_context():
        uid = str(_add(db, "kontakti", role="city_admin", global_permissions=["VIEW_LOGS"]))
        access_review.stamp(db, apply=True)
        assert access_review.hallinto_list(db)[0]["contact"] is None
        preview = access_review.contact_preview(db, uid)
        assert set(preview) == {"subject", "body"} and "@" not in preview["body"]
        app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = False
        with pytest.raises(access_review.ContactError) as off:
            access_review.contact_send(db, uid, "olivia")
        assert off.value.code == "disabled" and db.email_queue.count_documents({}) == 0
        app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = True
        try:
            contact = access_review.contact_send(db, uid, "olivia")
            assert contact["by"] == "olivia" and contact["reply"] == ""
            assert db.email_queue.count_documents({}) == 1
            with pytest.raises(access_review.ContactError) as twice:
                access_review.contact_send(db, uid, "olivia")
            assert twice.value.code == "already_contacted" and db.email_queue.count_documents({}) == 1
        finally:
            app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = False


def test_contact_reply_by_hand_and_old_rounds_do_not_count(app, db, clean_users):
    with app.app_context():
        uid = str(_add(db, "vastaaja", role="city_admin", global_permissions=["VIEW_LOGS"]))
        access_review.stamp(db, apply=True)
        with pytest.raises(access_review.ContactError):
            access_review.contact_reply(db, uid, "maybe", "olivia")
        got = access_review.contact_reply(db, uid, "keep", "olivia", "Tarvitsee Tampereella")
        assert got["reply"] == "keep" and got["note"] == "Tarvitsee Tampereella" and got["reply_at"]
        assert access_review.hallinto_list(db)[0]["contact"]["reply"] == "keep"
        # A confirmed account is no longer open and the next round starts clean.
        db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"access_review.deadline": access_review.DEADLINE - timedelta(days=400),
                                                               "access_review.confirmed_at": access_review.DEADLINE - timedelta(days=500)}})
        assert access_review.hallinto_list(db)[0]["contact"] is None
        with pytest.raises(access_review.ContactError) as unknown:
            access_review.contact_preview(db, str(ObjectId()))
        assert unknown.value.code == "not_found"


def test_list_carries_evidence_of_use(app, db, clean_users):
    with app.app_context():
        db.demo_audit_logs.delete_many({})
        uid = _add(db, "aktiivinen", role="city_admin", global_permissions=["VIEW_LOGS"],
                   last_login=datetime(2026, 11, 1, 12, 0))
        _add(db, "hiljainen", role="city_admin", global_permissions=["VIEW_LOGS"])
        access_review.stamp(db, apply=True)
        now = datetime(2026, 11, 22)
        for demo, when, auto in (("d1", datetime(2026, 10, 1), False), ("d1", datetime(2026, 10, 2), False),
                                 ("d2", datetime(2026, 9, 1), False), ("d3", datetime(2026, 9, 2), True),
                                 ("d4", datetime(2025, 1, 1), False)):
            db.demo_audit_logs.insert_one({"demo_id": demo, "user_id": str(uid), "timestamp": when, "automatic": auto})
        rows = {r["username"]: r["evidence"] for r in access_review.hallinto_list(db, now)}
        assert rows["aktiivinen"] == {"last_login": "2026-11-01T12:00:00Z", "last_admin_action": "2026-10-02T00:00:00Z",
                                      "admin_actions_6m": 3, "demos_handled_6m": 2, "cities_handled": []}
        assert rows["hiljainen"]["last_admin_action"] is None and rows["hiljainen"]["admin_actions_6m"] == 0
        db.demo_audit_logs.delete_many({})


def test_contact_reply_takes_a_situation_with_roles_and_date(app, db, clean_users):
    with app.app_context():
        uid = str(_add(db, "tilanne", role="city_admin", global_permissions=["VIEW_LOGS"]))
        access_review.stamp(db, apply=True)
        bad = [dict(situation="partial"), dict(situation="needs", roles=["city_admin"]), dict(situation="inactive"),
               dict(situation="inactive", until="2020-01-01"), dict(situation="needs", until="2027-01-01"),
               dict(situation="nonsense"), dict(situation="give_up", reply="keep"), dict(situation="partial", roles=["a|b"])]
        for kwargs in bad:
            with pytest.raises(access_review.ContactError) as err:
                access_review.contact_reply(db, uid, kwargs.pop("reply", None), "olivia", **kwargs)
            assert err.value.status == 400, kwargs
        got = access_review.contact_reply(db, uid, None, "olivia", "vain Tampere", situation="partial", roles=["city_admin"])
        assert (got["reply"], got["situation"], got["roles"], got["until"]) == ("keep", "partial", ["city_admin"], "")
        until = (datetime.utcnow() + timedelta(days=60)).strftime("%Y-%m-%d")
        got = access_review.contact_reply(db, uid, None, "olivia", situation="inactive", until=until)
        assert (got["reply"], got["situation"], got["until"], got["roles"]) == ("drop", "inactive", until, [])
        assert access_review.hallinto_list(db)[0]["contact"]["situation"] == "inactive"
        assert access_review.contact_reply(db, uid, "none", "olivia")["situation"] == ""


def _send_enabled(app, db, uid):
    app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = True
    try:
        return access_review.contact_send(db, uid, "olivia")
    finally:
        app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = False


def test_mail_carries_reply_to_and_a_signed_link_that_records_the_answer(app, db, clean_users):
    with app.app_context():
        uid = str(_add(db, "linkki", role="city_admin", global_admin=True, global_permissions=["VIEW_LOGS"]))
        access_review.stamp(db, apply=True)
        _send_enabled(app, db, uid)
        mail = db.email_queue.find_one({})
        assert mail["extra_headers"]["Reply-To"] == "hallinto@mielenosoitukset.fi"
        assert "/ylla-vastaus/" in mail["body"] and "[henkilökohtainen" not in mail["body"]
        token = mail["body"].split("/ylla-vastaus/")[1].split()[0]
        client = app.test_client()
        page = client.get(f"/ylla-vastaus/{token}")
        assert page.status_code == 200 and "Cache-Control" in page.headers and "no-store" in page.headers["Cache-Control"]
        assert access_review.hallinto_list(db)[0]["contact"]["reply"] == ""  # opening the page records nothing
        bad = client.post(f"/ylla-vastaus/{token}", data={"situation": "partial"})
        assert bad.status_code == 400
        ok = client.post(f"/ylla-vastaus/{token}", data={"situation": "partial", "roles": ["city_admin", "god"], "note": "vain kaupunki"})
        assert ok.status_code == 200
        contact = access_review.hallinto_list(db)[0]["contact"]
        assert (contact["reply"], contact["situation"], contact["roles"], contact["via"]) == ("keep", "partial", ["city_admin"], "link")
        assert contact["by"] == "olivia" and contact["note"] == "vain kaupunki"  # the mailer stays the contact's author
        assert client.get("/ylla-vastaus/not-a-token").status_code == 404
        assert client.get(f"/ylla-vastaus/{token[:-3]}abc").status_code == 404


def test_link_dies_with_the_round_and_followup_mails_once(app, db, clean_users):
    with app.app_context():
        uid = str(_add(db, "myohassa", role="city_admin", global_permissions=["VIEW_LOGS"]))
        access_review.stamp(db, apply=True)
        with pytest.raises(access_review.ContactError) as early:
            access_review.contact_followup(db, uid, "olivia")
        assert early.value.code == "disabled"
        app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = True
        try:
            with pytest.raises(access_review.ContactError) as none_yet:
                access_review.contact_followup(db, uid, "olivia")
            assert none_yet.value.code == "not_contacted"
            access_review.contact_send(db, uid, "olivia")
            access_review.contact_followup(db, uid, "olivia")
            assert db.email_queue.count_documents({}) == 2
            with pytest.raises(access_review.ContactError) as twice:
                access_review.contact_followup(db, uid, "olivia")
            assert twice.value.code == "already_followed_up"
            token = access_review.reply_token(db.users.find_one({"_id": ObjectId(uid)}))
            assert access_review.reply_doc(db, token) is not None
            # The next round's deadline makes the old token useless.
            db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"access_review.deadline": access_review.DEADLINE + timedelta(days=180)}})
            assert access_review.reply_doc(db, token) is None
        finally:
            app.config["ACCESS_REVIEW_CONTACT_ENABLED"] = False
