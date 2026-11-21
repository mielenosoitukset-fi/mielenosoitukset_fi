"""OT review of rejections (moderation guide 18 Nov 2026, Hallinto R03)."""

from datetime import datetime, timedelta

from bson import ObjectId

from mielenosoitukset_fi.utils import moderation_reviews
from tests.test_demo_decision_service import _capture_decision_email
from tests.test_hallinto_issuer import ORIGIN, PREFIX, _identity, _login, owner  # noqa: F401


class _Sender:
    def __init__(self):
        self.queued = []

    def queue_email(self, **kwargs):
        self.queued.append(kwargs)


def test_working_days_skip_the_weekend_in_helsinki():
    friday_noon = datetime(2026, 11, 20, 10, 0)  # 12.00 Helsinki
    assert moderation_reviews.add_working_days(friday_noon, 2) == datetime(2026, 11, 24, 10, 0)
    assert moderation_reviews.add_working_days(friday_noon, 5) == datetime(2026, 11, 27, 10, 0)


def test_a_rejection_waits_for_review_and_a_new_decision_ends_it(admin_client, db, seeded_data, monkeypatch):
    queued = _capture_decision_email(monkeypatch)
    demo_id = seeded_data["pending_demo_id"]
    assert admin_client.post(f"/api/admin/demo/{demo_id}/deny").status_code == 200
    # Repeating the same rejection does not open a second review.
    admin_client.post(f"/api/admin/demo/{demo_id}/deny")
    reviews = list(db.moderation_reviews.find({"demo_id": demo_id}))
    assert len(reviews) == 1
    review = reviews[0]
    assert review["status"] == "open" and review["stage"] == "review" and review["decision"] == "rejected"
    assert review["decided_by"]["id"]
    assert review["due_target"] < review["due_max"]
    # Review notices stay off until OT switches the procedure on.
    assert "review_by" not in queued[0]["context"]
    assert admin_client.post(f"/api/admin/demo/{demo_id}/approve").status_code == 200
    assert db.moderation_reviews.find_one({"_id": review["_id"]})["status"] == "superseded"


def _reviews(client, claims, path, body):
    return client.post(PREFIX + "/moderation-reviews/" + path, base_url=ORIGIN,
                       auth=("synthetic-hallinto", "synthetic-issuer-fixture-only-client"),
                       json={"sub": claims["sub"], "sid": claims["sid"], **body})


def _rejected_demo(db, decider):
    from mielenosoitukset_fi.demonstrations.decisions import apply_demo_decision

    demo_id = db.demonstrations.insert_one({
        "title": "Tarkistettava", "date": "2030-01-01", "city": "Helsinki",
        "approved": False, "rejected": False,
    }).inserted_id
    db.submitters.insert_one({"demonstration_id": demo_id, "submitter_email": f"{demo_id}@example.test"})
    apply_demo_decision(db, demo_id, "rejected", actor={"user_id": decider, "username": "moderaattori"},
                        source="test", email_sender=_Sender())
    return demo_id, db.moderation_reviews.find_one({"demo_id": demo_id})


def test_hallinto_records_ot_review_steps(owner, db, monkeypatch):  # noqa: F811
    from mielenosoitukset_fi.users import hallinto_issuer

    app, subject = owner
    sender = _Sender()
    monkeypatch.setattr(hallinto_issuer, "_invite_sender", sender)
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    decider = str(ObjectId())
    demo_id, review = _rejected_demo(db, decider)
    rid = str(review["_id"])
    try:
        # Only a signed-in admin reads or records reviews.
        assert _reviews(app.test_client(), claims, "list", {}).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        items = _reviews(app.test_client(), claims, "list", {}).json["items"]
        item = next(i for i in items if i["id"] == rid)
        assert item["link"] == f"{ORIGIN}/admin/demo/{demo_id}/decide"
        assert item["demo"]["title"] == "Tarkistettava" and item["status"] == "open" and item["current"]

        # A step needs a reason; a reconsideration needs a confirmed review.
        assert _reviews(app.test_client(), claims, "update", {"id": rid, "action": "confirm"}).json["error"] == "message_required"
        assert _reviews(app.test_client(), claims, "update",
                        {"id": rid, "action": "reconsider", "received_on": "2026-11-21"}).status_code == 409

        asked = _reviews(app.test_client(), claims, "update",
                         {"id": rid, "action": "needs_info", "message": "Kuka järjestää?"})
        assert asked.status_code == 200 and asked.json["item"]["status"] == "needs_info"
        assert sender.queued[-1]["template_name"] == "demo_review_needs_info.html"

        confirmed = _reviews(app.test_client(), claims, "update",
                             {"id": rid, "action": "confirm", "message": "Ei kuulu palveluun."}).json["item"]
        assert confirmed["status"] == "confirmed"
        assert confirmed["history"][-1]["by"]["id"] == subject
        assert sender.queued[-1]["template_name"] == "demo_review_confirmed.html"
        assert sender.queued[-1]["context"]["final"] is False
        assert db.demonstrations.find_one({"_id": demo_id})["rejected"] is True

        today = datetime.now(moderation_reviews.HELSINKI).strftime("%Y-%m-%d")
        reopened = _reviews(app.test_client(), claims, "update",
                            {"id": rid, "action": "reconsider", "received_on": today}).json["item"]
        assert reopened["stage"] == "reconsideration" and reopened["status"] == "open"
        assert reopened["reconsideration_due"]

        # The original decider never resolves the reconsideration.
        db.moderation_reviews.update_one({"_id": review["_id"]}, {"$set": {"decided_by.id": subject}})
        assert _reviews(app.test_client(), claims, "update",
                        {"id": rid, "action": "overturn", "message": "x"}).json["error"] == "conflicted"
        db.moderation_reviews.update_one({"_id": review["_id"]}, {"$set": {"decided_by.id": decider}})

        overturned = _reviews(app.test_client(), claims, "update",
                              {"id": rid, "action": "overturn", "message": "Järjestäjä selvitetty."}).json["item"]
        assert overturned["status"] == "overturned"
        demo = db.demonstrations.find_one({"_id": demo_id})
        assert demo["approved"] is True and demo["moderation_decision"]["source"] == "hallinto_review"
        assert sender.queued[-1]["template_name"] == "demo_review_overturned.html"
        assert db.moderation_reviews.find_one({"_id": review["_id"]})["status"] == "overturned"
    finally:
        db.demonstrations.delete_one({"_id": demo_id})
        db.moderation_reviews.delete_many({"demo_id": demo_id})


def test_overturn_never_publishes_a_changed_decision(db):
    demo_id, review = _rejected_demo(db, str(ObjectId()))
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"moderation_decision.id": "newer"}})
    published = []
    try:
        moderation_reviews.record(db, str(review["_id"]), "overturn", actor={"id": str(ObjectId()), "name": "ot"},
                                  message="x", apply_decision=lambda *a: published.append(a))
    except moderation_reviews.ReviewError as exc:
        assert exc.code == "stale"
    else:
        raise AssertionError("stale overturn was accepted")
    assert published == [] and db.moderation_reviews.find_one({"_id": review["_id"]})["status"] == "open"


def test_delay_notice_is_sent_once_when_notices_are_on(db, monkeypatch):
    from config import Config

    demo_id, review = _rejected_demo(db, str(ObjectId()))
    sender = _Sender()
    later = review["due_max"] + timedelta(minutes=1)
    assert moderation_reviews.notify_overdue(db, sender, now=later) == 0
    monkeypatch.setattr(Config, "MODERATION_REVIEW_NOTICES", True, raising=False)
    assert moderation_reviews.notify_overdue(db, sender, now=later) >= 1
    assert moderation_reviews.notify_overdue(db, sender, now=later) == 0
    assert [m["template_name"] for m in sender.queued if m["recipients"] == [f"{demo_id}@example.test"]] == [
        "demo_review_delayed.html"]


def test_rejection_email_names_the_review_date_when_notices_are_on(admin_client, db, seeded_data, monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "MODERATION_REVIEW_NOTICES", True, raising=False)
    queued = _capture_decision_email(monkeypatch)
    with admin_client.application.app_context():
        admin_client.application.config["MODERATION_REVIEW_NOTICES"] = True
        try:
            assert admin_client.post(f"/api/admin/demo/{seeded_data['pending_demo_id']}/deny").status_code == 200
        finally:
            admin_client.application.config.pop("MODERATION_REVIEW_NOTICES", None)
    assert queued[0]["context"]["review_by"]


def _escalated(client, claims, path, body):
    return client.post(PREFIX + "/escalated-cases/" + path, base_url=ORIGIN,
                       auth=("synthetic-hallinto", "synthetic-issuer-fixture-only-client"),
                       json={"sub": claims["sub"], "sid": claims["sid"], **body})


def test_hallinto_decides_escalated_cases(owner, db, monkeypatch):  # noqa: F811
    from mielenosoitukset_fi.users import hallinto_issuer
    from mielenosoitukset_fi.utils.time_utils import utcnow

    app, subject = owner
    monkeypatch.setattr(hallinto_issuer, "_invite_sender", _Sender())
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    demo_ids = [db.demonstrations.insert_one({
        "title": f"Eskaloitu {i}", "date": "2030-01-01", "city": "Helsinki", "approved": False, "rejected": False,
    }).inserted_id for i in range(3)]
    case_ids = []
    for demo_id, level in zip(demo_ids, (3, 3, 4)):
        case_ids.append(str(db.cases.insert_one({
            "type": "new_demo", "demo_id": demo_id, "running_num": 7000 + level, "updated_at": utcnow(),
            "meta": {"superior_needed": True, "escalation_level": level},
            "case_history": [{"timestamp": utcnow(), "mech_action": "escalate_demo", "user": "moderaattori",
                              "metadata": {"reason": "En voi päättää"}}],
        }).inserted_id))
    try:
        # Only a signed-in admin reads or decides.
        assert _escalated(app.test_client(), claims, "list", {}).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        items = _escalated(app.test_client(), claims, "list", {}).json["items"]
        mine = {i["id"]: i for i in items if i["id"] in case_ids}
        assert len(mine) == 3 and mine[case_ids[0]]["level"] == "ot" and mine[case_ids[0]]["can_escalate"]
        assert mine[case_ids[2]]["level"] == "jory" and not mine[case_ids[2]]["can_escalate"]
        assert mine[case_ids[0]]["reason"] == "En voi päättää"

        # Escalating needs a reason; Johtoryhmä is the last level.
        assert _escalated(app.test_client(), claims, "update", {"id": case_ids[0], "action": "escalate"}).status_code == 400
        sent_up = _escalated(app.test_client(), claims, "update",
                             {"id": case_ids[0], "action": "escalate", "reason": "Kuuluu Johtoryhmälle"})
        assert sent_up.status_code == 200 and sent_up.json["item"]["level"] == "jory"
        assert _escalated(app.test_client(), claims, "update",
                          {"id": case_ids[2], "action": "escalate", "reason": "Ylemmäs vielä"}).status_code == 409

        # Reject needs a reason; approve publishes through the decision service.
        assert _escalated(app.test_client(), claims, "update", {"id": case_ids[1], "action": "reject"}).status_code == 400
        rejected = _escalated(app.test_client(), claims, "update",
                              {"id": case_ids[1], "action": "reject", "reason": "Ei kuulu palveluun"})
        assert rejected.status_code == 200 and rejected.json["item"]["status"] == "rejected"
        assert db.demonstrations.find_one({"_id": demo_ids[1]})["rejected"] is True
        approved = _escalated(app.test_client(), claims, "update", {"id": case_ids[2], "action": "approve"})
        assert approved.status_code == 200 and approved.json["item"]["status"] == "approved"
        assert db.demonstrations.find_one({"_id": demo_ids[2]})["approved"] is True
        assert db.cases.find_one({"_id": ObjectId(case_ids[2])})["case_history"][-1]["metadata"]["authentication"] == "hallinto"
        # A decided case cannot be decided again.
        assert _escalated(app.test_client(), claims, "update", {"id": case_ids[2], "action": "approve"}).status_code == 409
    finally:
        db.cases.delete_many({"_id": {"$in": [ObjectId(i) for i in case_ids]}})
        db.demonstrations.delete_many({"_id": {"$in": demo_ids}})
