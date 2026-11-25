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


def test_render_email_wraps_tracks_staff_mail_only():
    from jinja2 import Environment, DictLoader
    from mielenosoitukset_fi.emailer.render import render_email

    env = Environment(loader=DictLoader({
        "admin_case_notification.html": "<html><body><p>Hei!</p></body></html>",
        "demo_reminder.html": "<html><body><p>Hei!</p></body></html>",
    }), autoescape=True)
    db = MagicMock()
    staff_html, staff_text = render_email(env, db, "admin_case_notification.html", {}, {"demo_id": "d1"})
    public_html, public_text = render_email(env, db, "demo_reminder.html", {})
    assert "/e/o/" in staff_html and "/e/o/" not in public_html
    assert "mfi-card" in public_html and "<" not in public_text
    assert db[ot.COLLECTION].insert_one.call_args[0][0]["demo_id"] == "d1"
    # no db (stub sender): no pixel, no crash
    assert "/e/o/" not in render_email(env, None, "admin_case_notification.html", {})[0]


def test_send_email_wraps_finished_html_from_go_queue(monkeypatch):
    from mielenosoitukset_fi.emailer import EmailSender as mod

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, _s, _r, raw): sent.append(raw)

    monkeypatch.setattr(mod.smtplib, "SMTP", FakeSMTP)
    sender = object.__new__(mod.EmailSender)
    sender._config = type("C", (), {"MAIL_SERVER": "x", "MAIL_DEFAULT_SENDER": "a@b.fi"})()
    sender._logger = MagicMock()
    sender._mailer_name, sender._mailer_version = "t", "1"
    job = type("J", (), {"sender": None, "attachments": [], "subject": "s", "recipients": ["r@x.fi"],
                         "extra_headers": {}, "body": "plain", "html": "<p>Hei!</p>"})()
    assert sender.send_email(job) is True
    import email, email.policy
    msg = email.message_from_string(sent[0], policy=email.policy.default)
    html = msg.get_body(("html",)).get_content()
    assert "mfi-card" in html and "<p>Hei,</p>" in html


def test_queue_email_works_on_sender_without_db_handle():
    import logging
    from jinja2 import DictLoader, Environment
    from mielenosoitukset_fi.emailer.EmailSender import EmailSender

    sender = object.__new__(EmailSender)
    sender._env = Environment(loader=DictLoader({"admin_case_notification.html": "<body><p>x</p></body>"}), autoescape=True)
    sender._queue_collection = MagicMock()
    sender._instance_id = "t"
    sender._logger = logging.getLogger(__name__)
    sender._start_retry_timer = MagicMock()
    sender.queue_email("admin_case_notification.html", "s", ["a@b.fi"], {})
    doc = sender._queue_collection.insert_one.call_args[0][0]
    assert "mfi-card" in doc["html"] and "/e/o/" not in doc["html"]
