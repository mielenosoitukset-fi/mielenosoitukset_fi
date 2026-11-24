import re
from unittest.mock import MagicMock

from flask import Flask

from mielenosoitukset_fi.emailer import open_tracking as ot


def test_only_staff_templates_tracked():
    assert ot.is_tracked("admin_case_notification.html")
    for public in ("demo_reminder.html", "newsletter.html", "demo_submitter_approved.html",
                   "password_reset_email.html"):
        assert not ot.is_tracked(public)


def test_add_pixel_stores_only_hash():
    db = MagicMock()
    out = ot.add_pixel(db, "<html><body><p>x</p></body></html>", "admin_case_notification.html")
    token = re.search(r"/e/o/([A-Za-z0-9_-]+)\.gif", out).group(1)
    assert out.index("<img") < out.index("</body>")
    row = db[ot.COLLECTION].insert_one.call_args[0][0]
    assert row["token_hash"] == ot._hash(token) and token not in str(row)
    assert set(row) == {"token_hash", "template", "created_at", "purge_at",
                        "first_opened_at", "open_count"}


def test_pixel_route_returns_gif_no_store():
    app = Flask(__name__)
    app.register_blueprint(ot.email_open_bp)
    r = app.test_client().get("/e/o/bad.gif")
    assert r.status_code == 200 and r.mimetype == "image/gif"
    assert r.data.startswith(b"GIF89a") and "no-store" in r.headers["Cache-Control"]


def test_open_linked_to_case_and_admin_without_ip():
    db = MagicMock()
    ot.add_pixel(db, "<body></body>", "admin_case_notification.html",
                 meta={"case_id": "c1", "user_id": "u1", "ip": "1.2.3.4", "email": "a@b.fi"})
    row = db[ot.COLLECTION].insert_one.call_args[0][0]
    assert row["case_id"] == "c1" and row["user_id"] == "u1"
    assert "ip" not in row and "email" not in row


def test_opens_for_case_merges_rows_per_admin():
    from datetime import datetime
    db = MagicMock()
    db[ot.COLLECTION].find.return_value = [
        {"user_id": "u1", "open_count": 1, "first_opened_at": datetime(2026, 11, 2)},
        {"user_id": "u1", "open_count": 2, "first_opened_at": datetime(2026, 11, 1)},
        {"user_id": "u2", "open_count": 0, "first_opened_at": None},
    ]
    out = ot.opens_for_case(db, "c1")
    assert out["u1"] == {"first_opened_at": datetime(2026, 11, 1), "open_count": 3}
    assert out["u2"]["first_opened_at"] is None
