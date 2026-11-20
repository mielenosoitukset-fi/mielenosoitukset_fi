"""Demo suggestions surface as cases, and new cases notify admins in the inbox.

No e-mail is involved: notices go to the ``notifications`` collection only.
"""
from copy import deepcopy
from datetime import timedelta

from bson import ObjectId

from mielenosoitukset_fi.scripts import deliver_case_notices
from mielenosoitukset_fi.utils import case_notifications, demo_suggestion_cases, notifications
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user
from tests.test_city_scoped_admin import _create_scoped_admin

ON = {"DEMO_SUGGESTION_CASES_ENABLED": "1", "CASE_NOTIFICATIONS_ENABLED": "1"}


def _run(db, environ=ON):
    return deliver_case_notices.main(db=db, environ=environ)


def test_switches_off_change_nothing(db, seeded_data):
    before_cases = db.cases.count_documents({})
    result = _run(db, environ={})
    assert result["opened"] == result["notified"] == 0
    assert db.cases.count_documents({}) == before_cases
    assert db.notifications.count_documents({"type": "new_case"}) == 0
    assert "case_id" not in db.demo_suggestions.find_one({"_id": seeded_data["suggestion_id"]})


def test_open_suggestion_becomes_one_case(db, seeded_data):
    suggestion_id = seeded_data["suggestion_id"]
    db.demo_suggestions.update_one({"_id": suggestion_id}, {"$set": {
        "ip": "203.0.113.9", "user_agent": "agent", "reporter_email": "rep@example.test",
    }})
    decided = ObjectId()
    db.demo_suggestions.insert_one({"_id": decided, "demo_id": str(seeded_data["demo_id"]),
                                    "status": "rejected", "created_at": utcnow()})

    assert _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})["opened"] == 1
    assert _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})["opened"] == 0

    case = db.cases.find_one({"_id": suggestion_id})
    assert case["type"] == "demo_edit_suggestion"
    assert str(case["demo_id"]) == str(seeded_data["demo_id"])
    assert case["meta"]["fields"] == ["title"]
    assert case["running_num"] > db.cases.find_one({"_id": seeded_data["case_id"]})["running_num"]
    assert case["submitter"] == {"submitter_email": "rep@example.test"}
    assert "ip" not in str(case) and "203.0.113.9" not in str(case)
    assert db.demo_suggestions.find_one({"_id": suggestion_id})["case_id"] == suggestion_id
    assert db.cases.find_one({"_id": decided}) is None


def test_case_closes_when_suggestion_is_decided(db, seeded_data):
    suggestion_id = seeded_data["suggestion_id"]
    _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})
    assert not db.cases.find_one({"_id": suggestion_id})["meta"].get("closed")
    db.demo_suggestions.update_one({"_id": suggestion_id}, {"$set": {"status": "applied"}})
    assert _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})["closed"] == 1
    case = db.cases.find_one({"_id": suggestion_id})
    assert case["meta"]["closed"] is True
    assert case["meta"]["closed_reason"] == "Ehdotus käsitelty"


def test_case_list_and_detail_show_suggestion(app, db, admin_client, seeded_data):
    suggestion_id = seeded_data["suggestion_id"]
    db.demo_suggestions.update_one({"_id": suggestion_id}, {"$set": {"reporter_comment": "Aika on väärin"}})
    _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})

    listing = admin_client.get("/admin/case/")
    assert listing.status_code == 200
    body = listing.get_data(as_text=True)
    assert "Muutosehdotus" in body
    assert f"/admin/case/{suggestion_id}/" in body

    detail = admin_client.get(f"/admin/case/{suggestion_id}/")
    assert detail.status_code == 200
    page = detail.get_data(as_text=True)
    assert "Aika on väärin" in page
    assert f"/admin/demo/suggestions/{suggestion_id}" in page
    assert "Käsittele ehdotus" in page

    # Deciding the suggestion closes the case on the next list view as well.
    db.demo_suggestions.update_one({"_id": suggestion_id}, {"$set": {"status": "rejected"}})
    admin_client.get("/admin/case/")
    assert db.cases.find_one({"_id": suggestion_id})["meta"]["closed"] is True


def test_city_admin_sees_only_own_city_suggestion_cases(app, db, seeded_data):
    helsinki = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE"])
    turku = _create_scoped_admin(db, ["turku"], ["LIST_CASES", "VIEW_CASE"])
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$set": {"city": "Helsinki", "city_key": "helsinki"}})
    _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})
    case_url = f"/admin/case/{seeded_data['suggestion_id']}/"
    assert _client_for_user(app, helsinki).get(case_url).status_code == 200
    assert _client_for_user(app, turku).get(case_url).status_code == 403


def test_new_case_notifies_only_admins_who_may_view_it(app, db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$set": {"city": "Helsinki", "city_key": "helsinki"}})
    helsinki = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE"])
    turku = _create_scoped_admin(db, ["turku"], ["LIST_CASES", "VIEW_CASE"])
    no_case_right = _create_scoped_admin(db, ["helsinki"], ["ACCEPT_DEMO"])
    disabled = _create_scoped_admin(db, ["helsinki"], ["VIEW_CASE"])
    db.users.update_one({"_id": disabled}, {"$set": {"active": False}})

    _run(db)
    case_id = seeded_data["suggestion_id"]
    notified = {doc["user_id"] for doc in db.notifications.find({"type": "new_case", "payload.case_id": case_id})}
    assert seeded_data["admin_id"] in notified
    assert helsinki in notified
    assert not notified & {turku, no_case_right, disabled, seeded_data["user_id"]}
    assert db.cases.find_one({"_id": case_id})["notified_count"] == len(notified)

    notice = db.notifications.find_one({"user_id": helsinki, "type": "new_case", "payload.case_id": case_id})
    assert notice["link"] == f"/admin/case/{case_id}/"
    assert "rep@" not in str(notice["payload"]) and "email" not in notice["payload"]

    # Reruns neither duplicate nor re-mark a read notice unread.
    db.notifications.update_one({"_id": notice["_id"]}, {"$set": {"read": True}})
    db.cases.update_one({"_id": case_id}, {"$unset": {"notified_at": ""}})
    _run(db)
    assert db.notifications.count_documents({"user_id": helsinki, "payload.case_id": case_id}) == 1
    assert db.notifications.find_one({"_id": notice["_id"]})["read"] is True

    client = _client_for_user(app, helsinki)
    response = client.get("/api/notifications/")
    assert response.status_code == 200
    messages = [item["message"] for item in response.json]
    assert any(message.startswith("Uusi tapaus #") for message in messages)
    assert client.get(notice["link"]).status_code == 200


def test_inbox_hides_notice_after_close_or_lost_access(app, db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$set": {"city": "Helsinki", "city_key": "helsinki"}})
    helsinki = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE"])
    _run(db)
    case_id = seeded_data["suggestion_id"]
    visible = lambda: [n for n in notifications.fetch_notifications(helsinki)
                       if n["type"] == "new_case" and n["payload"]["case_id"] == case_id]
    assert len(visible()) == 1
    db.admin_scope_grants.update_many({"user_id": helsinki}, {"$set": {"revoked_at": utcnow()}})
    assert visible() == []
    db.admin_scope_grants.update_many({"user_id": helsinki}, {"$unset": {"revoked_at": ""}})
    assert len(visible()) == 1
    db.cases.update_one({"_id": seeded_data["suggestion_id"]}, {"$set": {"meta.closed": True}})
    assert visible() == []


def test_old_and_skipped_cases_are_not_notified(db, seeded_data):
    old = deepcopy(db.cases.find_one({"_id": seeded_data["case_id"]}))
    old.update({"_id": ObjectId(), "running_num": 777001, "created_at": utcnow() - timedelta(days=30)})
    application = deepcopy(old)
    application.update({"_id": ObjectId(), "running_num": 777002, "type": "city_admin_application",
                        "created_at": utcnow()})
    db.cases.insert_many([old, application])
    case_notifications.deliver_pending(db)
    assert db.notifications.count_documents({"payload.case_id": {"$in": [old["_id"], application["_id"]]}}) == 0
    # The seeded fresh new_demo case is notified to the global admin.
    assert db.notifications.count_documents({"payload.case_id": seeded_data["case_id"],
                                             "user_id": seeded_data["admin_id"]}) == 1


def test_job_is_registered():
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP
    assert JOB_DEFINITION_MAP["deliver_case_notices"].func is deliver_case_notices.main
    assert demo_suggestion_cases.is_enabled({}) is False
    assert case_notifications.is_enabled({"CASE_NOTIFICATIONS_ENABLED": "true"}) is True


class FakeSender:
    """Records queued mails instead of writing the email queue."""

    def __init__(self):
        self.sent = []

    def queue_email(self, **kwargs):
        self.sent.append(kwargs)


EMAILS_ON = {**ON, "CASE_NOTIFICATION_EMAILS_ENABLED": "1"}


def _helsinki_admins(db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$set": {"city": "Helsinki", "city_key": "helsinki"}})
    opted_in = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE"])
    db.users.update_one({"_id": opted_in}, {"$set": {"case_notice_emails": True}})
    not_opted = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE"])
    return opted_in, not_opted


def test_emails_need_their_own_switch(db, seeded_data):
    _helsinki_admins(db, seeded_data)
    sender = FakeSender()
    deliver_case_notices.main(db=db, environ=ON, email_sender=sender)
    assert sender.sent == []
    assert db.email_queue.count_documents({}) == 0


def test_global_admins_always_mailed_city_admins_opt_in(app, db, seeded_data):
    opted_in, not_opted = _helsinki_admins(db, seeded_data)
    sender = FakeSender()
    deliver_case_notices.main(db=db, environ=EMAILS_ON, email_sender=sender)
    case_id = seeded_data["suggestion_id"]
    mails = [m for m in sender.sent if f"/admin/case/{case_id}/" in m["context"]["case_link"]]
    recipients = sorted(r for m in mails for r in m["recipients"])
    admin_email = db.users.find_one({"_id": seeded_data["admin_id"]})["email"]
    opted_email = db.users.find_one({"_id": opted_in})["email"]
    assert recipients == sorted([admin_email, opted_email])
    assert all(len(m["recipients"]) == 1 for m in mails)
    assert mails[0]["context"]["type_label"] == "Muutosehdotus"
    assert mails[0]["template_name"] == "admin_case_notification.html"

    # The case records who was told and who was mailed, and shows it.
    rows = {row["user_id"]: row["emailed"] for row in db.cases.find_one({"_id": case_id})["notified_users"]}
    assert rows[seeded_data["admin_id"]] is True and rows[opted_in] is True and rows[not_opted] is False
    page = _client_for_user(app, seeded_data["admin_id"]).get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert "Ilmoitetut ylläpitäjät" in page and "Vain ilmoitus" in page

    # A rerun does not mail again.
    db.cases.update_one({"_id": case_id}, {"$unset": {"notified_at": ""}})
    deliver_case_notices.main(db=db, environ=EMAILS_ON, email_sender=sender)
    assert len([m for m in sender.sent if f"/admin/case/{case_id}/" in m["context"]["case_link"]]) == 2
    rows = {row["user_id"]: row["emailed"] for row in db.cases.find_one({"_id": case_id})["notified_users"]}
    assert rows[opted_in] is True


def test_email_template_renders():
    # A bare Jinja environment: constructing EmailSender would reset the test database manager.
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader("mielenosoitukset_fi/templates/emails"), autoescape=select_autoescape(["html"]))
    html = env.get_template("admin_case_notification.html").render(
        running_num=100123, type_label="Muutosehdotus", demo_title="Testi",
        case_link="https://mielenosoitukset.fi/admin/case/abc/",
    )
    assert "#100123" in html and "https://mielenosoitukset.fi/admin/case/abc/" in html and "Testi" in html


def test_city_admin_toggles_case_emails(app, db, seeded_data, monkeypatch):
    monkeypatch.setenv("CASE_NOTIFICATIONS_ENABLED", "1")
    monkeypatch.setenv("CASE_NOTIFICATION_EMAILS_ENABLED", "1")
    city = _create_scoped_admin(db, ["helsinki"], ["LIST_CASES", "VIEW_CASE"])
    client = _client_for_user(app, city)
    assert "Tilaa sähköpostit uusista tapauksista" in client.get("/admin/case/").get_data(as_text=True)
    assert client.post("/admin/case/email_notices/", data={"enabled": "1"}).status_code == 302
    assert db.users.find_one({"_id": city})["case_notice_emails"] is True
    assert "Lopeta sähköpostit uusista tapauksista" in client.get("/admin/case/").get_data(as_text=True)
    client.post("/admin/case/email_notices/", data={"enabled": "0"})
    assert db.users.find_one({"_id": city})["case_notice_emails"] is False
    # Global admins are always mailed and get no toggle.
    admin_page = _client_for_user(app, seeded_data["admin_id"]).get("/admin/case/").get_data(as_text=True)
    assert "sähköpostit uusista tapauksista" not in admin_page


def test_broken_suggestion_is_skipped_once(db, seeded_data):
    broken = ObjectId()
    db.demo_suggestions.insert_one({"_id": broken, "demo_id": "not-an-id", "status": "new", "created_at": utcnow() - timedelta(days=1)})
    assert _run(db, {"DEMO_SUGGESTION_CASES_ENABLED": "1"})["opened"] == 1
    assert db.demo_suggestions.find_one({"_id": broken})["case_skipped"] == "invalid_demo_id"
    assert db.cases.find_one({"_id": broken}) is None
    assert db.cases.find_one({"_id": seeded_data["suggestion_id"]})["type"] == "demo_edit_suggestion"
