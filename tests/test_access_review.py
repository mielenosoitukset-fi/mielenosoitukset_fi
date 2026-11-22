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
