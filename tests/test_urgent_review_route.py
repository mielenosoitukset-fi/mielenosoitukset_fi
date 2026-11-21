"""Alert and notice links open the demonstration's case page, for anyone who may decide it."""

from datetime import UTC, datetime
import re

from bson import ObjectId
import pytest

from tests.conftest import _client_for_user
from tests.test_city_scoped_admin import _create_scoped_admin
from mielenosoitukset_fi.utils.urgent_review_alerts import build_urgent_review_alert


def _review_client(app, db, permissions=("ACCEPT_DEMO",), city="helsinki"):
    return _client_for_user(app, _create_scoped_admin(db, [city], list(permissions)))


def _helsinki(db, demo_id, **extra):
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"city": "Helsinki", "city_key": "helsinki", **extra}})


def test_alert_link_opens_the_case_with_only_scoped_approval_permission(app, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    private = "private-review-sentinel"
    _helsinki(db, demo_id, date="2026-10-24", start_time="15:00", submitter_email=private, token=private,
              description='<script>alert("unsafe")</script>\nVisible content')
    demo = db.demonstrations.find_one({"_id": demo_id})
    snapshot = build_urgent_review_alert(demo, datetime(2026, 10, 23, 12, tzinfo=UTC))
    path = re.search(r'href="https://mielenosoitukset.fi([^"]+)"', snapshot["text"])[1]
    assert path == f"/admin/demo/{demo_id}/decide"
    client = _review_client(app, db)
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/admin/case/" in response.request.path
    assert "Hyväksy mielenosoitus" in html and "Hylkää mielenosoitus" in html
    assert '<script>alert("unsafe")</script>' not in html
    assert private not in html
    assert client.get(f"/admin/demo/edit_demo/{demo_id}").status_code == 403
    assert client.get(f"/admin/demo/command-center/{demo_id}").status_code == 403
    assert app.test_client().get(path).status_code == 302
    # Old alert and notice links keep working.
    assert client.get(f"/admin/demo/review/{demo_id}").status_code == 302


@pytest.mark.parametrize("permissions,city", [(["ACCEPT_DEMO"], "turku"), (["VIEW_DEMO"], "helsinki")])
def test_case_rejects_other_city_and_non_approvers(app, db, seeded_data, permissions, city):
    demo_id = seeded_data["pending_demo_id"]
    _helsinki(db, demo_id)
    client = _review_client(app, db, permissions, city)
    case_id = db.cases.find_one({"type": "new_demo"})["_id"] if db.cases.find_one({"type": "new_demo"}) else None
    if case_id is None:
        case_id = db.cases.insert_one({"type": "new_demo", "demo_id": demo_id, "meta": {}}).inserted_id
    before = db.demonstrations.find_one({"_id": demo_id})
    assert client.get(f"/admin/demo/{demo_id}/decide").status_code in (302, 403)
    assert client.get(f"/admin/case/{case_id}/").status_code == 403
    assert client.post(f"/admin/case/{case_id}/demo_action", json={"action": "accept"}).status_code == 403
    assert db.demonstrations.find_one({"_id": demo_id}) == before


@pytest.mark.parametrize("action,approved", [("accept", True), ("reject", False)])
def test_city_admin_decides_in_the_case_with_the_canonical_service(app, db, seeded_data, action, approved):
    demo_id = seeded_data["pending_demo_id"]
    _helsinki(db, demo_id, city_assignment={"escalated": False})
    client = _review_client(app, db)
    case_id = client.get(f"/admin/demo/{demo_id}/decide").headers["Location"].rstrip("/").rsplit("/", 1)[1]
    extra = {} if approved else {"reason_key": "spam"}
    assert client.post(f"/admin/case/{case_id}/demo_action", json={"action": action, **extra}).status_code == 200
    updated = db.demonstrations.find_one({"_id": demo_id})
    assert updated["approved"] is approved and updated["rejected"] is (not approved)
    assert updated["moderation_decision"]["source"] == "admin_api"
    assert not updated.get("city_assignment")
    snapshot = build_urgent_review_alert(updated, datetime(2026, 10, 23, tzinfo=UTC), has_message=True)
    assert snapshot["responsibility"] is None
    page = client.get(f"/admin/case/{case_id}/").get_data(as_text=True)
    assert 'onclick="approveDemo()"' not in page
    before = updated["moderation_decision"]["id"]
    assert client.post(f"/admin/case/{case_id}/demo_action", json={"action": action, **extra}).status_code in (200, 409)
    assert db.demonstrations.find_one({"_id": demo_id})["moderation_decision"]["id"] == before


def test_decide_link_handles_missing_identity(app, db, seeded_data, admin_client):
    client = _review_client(app, db)
    assert client.get("/admin/demo/not-an-id/decide").status_code in (403, 404)
    assert client.get(f"/admin/demo/{ObjectId()}/decide").status_code in (403, 404)
    assert admin_client.get("/admin/demo/not-an-id/decide").status_code == 404
    assert admin_client.get(f"/admin/demo/{ObjectId()}/decide").status_code == 404
