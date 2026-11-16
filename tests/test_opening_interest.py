"""Campaign opening interest signup: confirmation mails, retention, export."""
import csv
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone

import pytest

from mielenosoitukset_fi.utils import campaign_opening_interest as oi

ON = {oi.ENABLED_FLAG: "true"}
NOW = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(db):
    for name in (oi.COLLECTION, "email_queue"):
        db[name].delete_many({})
    yield
    for name in (oi.COLLECTION, "email_queue"):
        db[name].delete_many({})


def signup(db, email, status="pending", **extra):
    row = {"email": email, "city": "Tampere", "willing": "maybe", "band": "10to20", "status": status, "locale": "fi",
           "consent_at": NOW, "consent_version": "2026-11-16", "created_at": NOW,
           "confirmation_requested_at": NOW, "confirmation_sent_at": None, "confirmation_count": 0}
    row.update(extra)
    return db[oi.COLLECTION].insert_one(row).inserted_id


def test_nothing_is_queued_without_the_mail_flag(db):
    signup(db, "a@example.test")
    assert oi.send_confirmations(db, now=NOW, environ={}) == {"enabled": False, "queued": 0}
    assert db.email_queue.count_documents({}) == 0


def test_confirmation_is_queued_once_with_hashed_tokens_and_no_form_text(db):
    row_id = signup(db, "a@example.test", city="Salainen kaupunki")
    assert oi.send_confirmations(db, now=NOW, environ=ON) == {"enabled": True, "queued": 1}
    assert oi.send_confirmations(db, now=NOW, environ=ON)["queued"] == 0
    mail = db.email_queue.find_one({})
    assert mail["kind"] == "campaign_opening_interest" and mail["recipients"] == ["a@example.test"]
    assert "Salainen kaupunki" not in mail["body"]
    assert "List-Unsubscribe-Post" in mail["extra_headers"]
    token = re.search(r"avajaiset=vahvista#t=([\w-]+)", mail["body"]).group(1)
    row = db[oi.COLLECTION].find_one({"_id": row_id})
    assert row["confirm_hash"] == hashlib.sha256(token.encode()).hexdigest() and token not in str(row)
    assert "avajaiset=peru#t=" in mail["body"] and "/api/campaign-opening/interest/unsubscribe?t=" in mail["extra_headers"]["List-Unsubscribe"]


def test_only_confirmed_rows_are_exported_and_cells_are_formula_safe(db):
    signup(db, "pending@example.test")
    signup(db, "ok@example.test", status="confirmed", city="=SUM(1)", confirmed_at=NOW)
    rows = list(csv.reader(io.StringIO(oi.export_confirmed(db))))
    assert rows[0] == ["email", "city", "willing_to_pay", "price_band", "confirmed_at"]
    assert len(rows) == 2 and rows[1][0] == "ok@example.test" and rows[1][1] == "'=SUM(1)"


def test_purge_removes_old_pending_rows_and_everything_after_the_season(db):
    signup(db, "old@example.test", created_at=NOW - timedelta(days=15))
    signup(db, "new@example.test")
    signup(db, "keep@example.test", status="confirmed", retain_until=datetime(2028, 1, 1, tzinfo=timezone.utc))
    signup(db, "done@example.test", status="confirmed")
    assert oi.purge(db, now=NOW, dry_run=True) == {"dry_run": True, "pending_expired": 1, "retention_ended": 0}
    assert oi.purge(db, now=NOW)["pending_expired"] == 1
    assert db[oi.COLLECTION].count_documents({}) == 3
    after = oi.PURGE_AFTER + timedelta(days=1)
    assert oi.purge(db, now=after)["retention_ended"] == 2
    assert db[oi.COLLECTION].distinct("email") == ["keep@example.test"]


def test_retention_date_follows_the_consent_text_and_jobs_exist():
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP

    assert oi.PURGE_AFTER.date().isoformat() == "2027-03-13"  # a week after the opening, 6.3.2027
    for key in ("deliver_opening_interest_confirmations", "purge_opening_interest"):
        assert key in JOB_DEFINITION_MAP
