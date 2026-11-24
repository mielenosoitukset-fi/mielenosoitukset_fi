"""The retention preview page is read-only, global admin only and audit-logged (#596)."""
from datetime import datetime, timedelta, timezone

from bson import ObjectId


def test_global_admin_sees_counts_and_nothing_changes(admin_client, db):
    marker = {"seeded_by": "retention-preview-test"}
    old = datetime.now(timezone.utc) - timedelta(days=400)
    db.login_logs.delete_many(marker)
    db.login_logs.insert_one({**marker, "user_id": ObjectId(), "timestamp": old, "ip": "1.2.3.4"})
    before = db.admin_logs.count_documents({})
    try:
        response = admin_client.get("/admin/retention-preview")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "login_logs" in body and "demo_submission_errors" in body
        assert "Käyttämättömät tilit" in body
        assert db.login_logs.count_documents(marker) == 1  # nothing was deleted
        assert db.admin_logs.count_documents({}) > before  # the view left an audit trace
    finally:
        db.login_logs.delete_many(marker)


def test_other_users_cannot_open_it(user_client):
    response = user_client.get("/admin/retention-preview")
    assert response.status_code in (302, 401, 403)
    if response.status_code == 302:
        assert "retention-preview" not in response.headers.get("Location", "")
