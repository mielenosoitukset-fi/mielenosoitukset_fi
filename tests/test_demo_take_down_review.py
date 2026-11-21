"""Second admin check for take-downs.

Normal take-down: a request; the demo stays public until another admin
confirms. Urgent: hidden at once, another admin confirms within the window.
The requester can never confirm their own request.
"""

from datetime import timedelta

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user
from tests.test_demo_decision_service import _capture_decision_email


def _second_admin(app, db):
    from mielenosoitukset_fi.users.models import User

    user_id = ObjectId()
    user = User.create_user(
        username=f"second-admin-{str(user_id)[-6:]}", password="SecondPass1!",
        email=f"second-admin-{str(user_id)[-6:]}@example.test", displayname="Second",
    )
    user.update({"_id": user_id, "confirmed": True, "active": True, "role": "global_admin",
                 "global_admin": True, "global_permissions": ["ACCEPT_DEMO", "VIEW_DEMO", "LIST_DEMOS"]})
    db.users.insert_one(user)
    return _client_for_user(app, user_id)


def _approve(admin_client, demo_id):
    assert admin_client.post(f"/api/admin/demo/{demo_id}/approve", json={}).status_code == 200


def _api(client, demo_id, **payload):
    return client.post(f"/api/admin/demo/{demo_id}/take_down", json=payload)


def test_normal_request_waits_for_another_admin(app, admin_client, db, seeded_data, monkeypatch):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    queued = _capture_decision_email(monkeypatch)

    response = _api(admin_client, demo_id, reason="Väärä paikka.")
    assert response.status_code == 200 and response.get_json()["status"] == "requested"
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["approved"] is True and not demo.get("taken_down")
    assert demo["take_down_review"]["status"] == "requested"
    assert queued == []

    # The requester cannot confirm their own request.
    assert _api(admin_client, demo_id, action="confirm_take_down").status_code == 409
    assert db.demonstrations.find_one({"_id": demo_id})["approved"] is True
    # A second request while one is open is refused.
    assert _api(admin_client, demo_id, reason="Toinen.").status_code == 409

    second = _second_admin(app, db)
    response = _api(second, demo_id, action="confirm_take_down")
    assert response.status_code == 200, response.get_json()
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["taken_down"] is True and demo["approved"] is False
    assert demo["taken_down_reason"] == "Väärä paikka."
    assert demo["take_down_review"]["status"] == "confirmed"
    assert [mail["template_name"] for mail in queued] == ["demo_submitter_taken_down.html"]
    case = db.cases.find_one({"demo_id": {"$in": [demo_id, str(demo_id)]}})
    kinds = [entry["action_type"] for entry in case["action_logs"]]
    assert "take_down_requested" in kinds and "take_down_confirmed" in kinds


def test_urgent_hides_at_once_and_is_confirmed_later(app, admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)

    response = _api(admin_client, demo_id, reason="Uhkaus.", urgent=True)
    assert response.get_json()["status"] == "taken_down"
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["taken_down"] is True
    review = demo["take_down_review"]
    assert review["status"] == "awaiting_confirmation"
    assert timedelta(hours=47) < review["confirm_by"] - review["requested_at"] <= timedelta(hours=48)

    assert _api(_second_admin(app, db), demo_id, action="confirm_take_down").status_code == 200
    assert db.demonstrations.find_one({"_id": demo_id})["take_down_review"]["status"] == "confirmed"


def test_withdraw_and_decline_keep_demo_public(app, admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    _api(admin_client, demo_id, reason="Ehkä väärä.")
    assert _api(admin_client, demo_id, action="cancel_take_down").get_json()["status"] == "public"
    assert "take_down_review" not in db.demonstrations.find_one({"_id": demo_id})

    _api(admin_client, demo_id, reason="Uudestaan.")
    assert _api(_second_admin(app, db), demo_id, action="cancel_take_down").status_code == 200
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["approved"] is True and "take_down_review" not in demo
    assert _api(admin_client, demo_id, action="cancel_take_down").status_code == 409


def test_republish_clears_an_urgent_review(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    _api(admin_client, demo_id, reason="Uhkaus.", urgent=True)
    _approve(admin_client, demo_id)
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["approved"] is True and "take_down_review" not in demo


def test_one_admin_is_enough_when_turned_off(app, admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    app.config["TAKE_DOWN_SECOND_ADMIN"] = False
    try:
        response = _api(admin_client, demo_id, reason="Yksin.")
    finally:
        app.config.pop("TAKE_DOWN_SECOND_ADMIN", None)
    assert response.get_json()["status"] == "taken_down"
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["taken_down"] is True and "take_down_review" not in demo


def test_case_page_shows_request_and_right_buttons(app, admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    case = db.cases.find_one({"type": "new_demo", "demo_id": demo_id})
    url = f"/admin/case/{case['_id']}/"
    _api(admin_client, demo_id, reason="Tarkista <b>paikka</b>.")

    own = admin_client.get(url).get_data(as_text=True)
    assert "Poistopyyntö odottaa toisen ylläpitäjän vahvistusta" in own
    assert "Tarkista &lt;b&gt;paikka&lt;/b&gt;." in own
    assert 'onclick="confirmTakeDown()"' not in own and "Peru pyyntö" in own
    assert 'onclick="takeDownDemo()"' not in own

    other = _second_admin(app, db).get(url).get_data(as_text=True)
    assert 'onclick="confirmTakeDown()"' in other and "Hylkää pyyntö" in other

    db.demonstrations.update_one({"_id": demo_id}, {"$unset": {"take_down_review": ""}})
    _api(admin_client, demo_id, reason="Kiire.", urgent=True)
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$set": {"take_down_review.confirm_by": utcnow() - timedelta(hours=1)}}
    )
    overdue = admin_client.get(url).get_data(as_text=True)
    assert "Vahvistus on myöhässä" in overdue and "Julkaise uudelleen" in overdue


def test_city_admin_cannot_confirm(app, admin_client, db, seeded_data):
    from tests.test_city_scoped_admin import _create_scoped_admin

    demo_id = seeded_data["pending_demo_id"]
    _approve(admin_client, demo_id)
    _api(admin_client, demo_id, reason="Väärä paikka.")
    city_admin = _client_for_user(
        app, _create_scoped_admin(db, ["helsinki"], ["LIST_DEMOS", "VIEW_DEMO", "ACCEPT_DEMO"])
    )
    assert _api(city_admin, demo_id, action="confirm_take_down").status_code == 403
    assert _api(city_admin, demo_id, action="cancel_take_down").status_code == 403
    assert db.demonstrations.find_one({"_id": demo_id})["take_down_review"]["status"] == "requested"
