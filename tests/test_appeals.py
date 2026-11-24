"""Valitukset: stored with the complainant's address, answered with a päätös that names no person."""

import pytest

from mielenosoitukset_fi.utils import appeals

LETTER = {
    "body": "Operatiivinen tiimi", "decision_ref": "MOSO/002/RAT/2026/003", "case_ref": "MOSO/002/VLT/2026/001",
    "decided_on": "2026-11-24", "subject": "Mielenosoitus Tampereella", "outcome": "rejected",
    "reasoning": "Ilmoitus ei täyttänyt julkaisuperiaatteita, koska järjestäjä ei ollut tavoitettavissa.", "final": False,
}
TEXT = "PÄÄTÖS\nPäätöksen numero: MOSO/002/RAT/2026/003\nValitus hylätään\n"
NEW = {"kind": "valitus", "title": "Mielenosoitus Tampereella", "reference": "", "text": "Päätös oli virheellinen.",
       "email": "Valittaja@Example.test", "name": "Vilma Valittaja"}


@pytest.fixture
def clean(db):
    db[appeals.COLLECTION].delete_many({})
    db.email_queue.delete_many({"_id": {"$regex": "^appeal-"}})
    yield db
    db[appeals.COLLECTION].delete_many({})
    db.email_queue.delete_many({"_id": {"$regex": "^appeal-"}})


def test_create_keeps_the_address_here_and_confirms_once(clean):
    row = appeals.create(clean, NEW, "hallinto-sub")
    assert row["complainant"] == {"name": "Vilma Valittaja", "email": "valittaja@example.test"}
    seen = appeals.view(row)
    assert "complainant" not in seen and "valittaja" not in str(seen).lower()
    assert seen["status"] == "received" and seen["subject"]["title"] == "Mielenosoitus Tampereella"
    assert all("complainant" not in item for item in appeals.list_rows(clean))
    mail = clean.email_queue.find_one({"_id": f"appeal-received:{row['_id']}"})
    assert mail["recipients"] == ["valittaja@example.test"]
    assert mail["extra_headers"]["Reply-To"] == "hallinto@mielenosoitukset.fi"


@pytest.mark.parametrize("bad", [{"kind": "muu"}, {"email": "nope"}, {"text": "lyhyt"}, {"text": ""}, {"title": "x" * 301}])
def test_create_refuses_bad_input(clean, bad):
    with pytest.raises(appeals.AppealError):
        appeals.create(clean, {**NEW, **bad}, "s")


def test_decision_is_mailed_once_and_names_nobody(clean):
    row = appeals.create(clean, NEW, "s")
    step = {"id": str(row["_id"]), "status": "in_review", "case": "case-1"}
    assert appeals.record_step(clean, step, "s")[1] is True
    assert appeals.record_step(clean, step, "s")[1] is False
    decided = {"id": str(row["_id"]), "status": "decided", "case": "case-1", "letter": {**LETTER, "owner": "tili 1a2b", "verifier": "x"}, "text": TEXT}
    item, changed = appeals.record_step(clean, decided, "s")
    assert changed and item["status"] == "decided" and item["decision_ref"] == "MOSO/002/RAT/2026/003"
    again, changed = appeals.record_step(clean, decided, "s")
    assert not changed
    stored = clean[appeals.COLLECTION].find_one({"_id": row["_id"]})
    assert set(stored["letter"]) == set(appeals.LETTER_FIELDS)  # the extra fields were dropped
    mails = list(clean.email_queue.find({"_id": f"appeal-decision:{row['_id']}"}))
    assert len(mails) == 1 and mails[0]["recipients"] == ["valittaja@example.test"]
    assert "MOSO/002/RAT/2026/003" in mails[0]["body"] and "tili 1a2b" not in mails[0]["body"]
    assert mails[0]["extra_headers"]["Reply-To"] == "hallinto@mielenosoitukset.fi"
    # A closed appeal takes no other päätös.
    with pytest.raises(appeals.AppealError) as closed:
        appeals.record_step(clean, {**decided, "letter": {**LETTER, "outcome": "accepted"}}, "s")
    assert closed.value.status == 409


@pytest.mark.parametrize("edit", [
    {"outcome": "won"}, {"decision_ref": "x"}, {"case_ref": ""}, {"decided_on": "24.11.2026"}, {"final": "no"}, {"reasoning": ""}, {"body": ""},
])
def test_a_bad_letter_is_refused(clean, edit):
    row = appeals.create(clean, NEW, "s")
    with pytest.raises(appeals.AppealError) as refused:
        appeals.record_step(clean, {"id": str(row["_id"]), "status": "decided", "letter": {**LETTER, **edit}, "text": TEXT}, "s")
    assert refused.value.code == "invalid_letter"
    assert clean.email_queue.count_documents({"_id": f"appeal-decision:{row['_id']}"}) == 0


def test_steps_are_checked(clean):
    row = appeals.create(clean, NEW, "s")
    for bad in ({"id": "zz", "status": "in_review"}, {"id": str(row["_id"]), "status": "withdrawn"},
                {"id": str(row["_id"]), "status": "in_review", "letter": LETTER}):
        with pytest.raises(appeals.AppealError):
            appeals.record_step(clean, bad, "s")
    with pytest.raises(appeals.AppealError) as missing:
        appeals.record_step(clean, {"id": "0" * 24, "status": "in_review"}, "s")
    assert missing.value.status == 404


def test_original_deciders_and_pdf(clean):
    from bson import ObjectId
    demo, rejecter, confirmer = ObjectId(), ObjectId(), ObjectId()
    clean.moderation_reviews.insert_one({"demo_id": demo, "decided_by": {"id": str(rejecter)},
                                         "history": [{"by": {"id": str(confirmer)}}, {"by": {"id": None}}]})
    try:
        row = appeals.create(clean, {**NEW, "reference": f"https://mielenosoitukset.fi/demonstration/{demo}"}, "s")
        assert appeals.view(row, clean)["original_deciders"] == sorted([str(rejecter), str(confirmer)])
        assert appeals.view(row)["original_deciders"] == []
        import base64
        pdf = b"%PDF-1.4 test"
        step = {"id": str(row["_id"]), "status": "decided", "letter": LETTER, "text": TEXT, "pdf": base64.b64encode(pdf).decode()}
        appeals.record_step(clean, step, "s")
        mail = clean.email_queue.find_one({"_id": f"appeal-decision:{row['_id']}"})
        assert mail["attachments"][0]["mime_type"] == "application/pdf" and mail["attachments"][0]["content"] == pdf
        other = appeals.create(clean, NEW, "s")
        with pytest.raises(appeals.AppealError):
            appeals.record_step(clean, {"id": str(other["_id"]), "status": "decided", "letter": LETTER, "text": TEXT, "pdf": "bm90IGEgcGRm"}, "s")
    finally:
        clean.moderation_reviews.delete_many({"demo_id": demo})


def test_status_link_shows_the_stage_only(clean):
    row = appeals.create(clean, NEW, "hallinto-sub")
    mail = clean.email_queue.find_one({"_id": f"appeal-received:{row['_id']}"})
    import re
    token = re.search(r"/valitus/tila/([A-Za-z0-9_-]+)", mail["body"]).group(1)
    assert appeals.hash_token(token) == row["token_hash"] and token not in str(row)
    seen = appeals.public_status(clean, token)
    assert seen["stage"] == "received" and seen["decision_ref"] == ""
    assert "valittaja" not in str(seen).lower() and "Vilma" not in str(seen)
    assert appeals.public_status(clean, "x" * 30) is None and appeals.public_status(clean, "") is None
    assert "token_hash" not in appeals.view(row)


def test_retention_removes_the_complainant_after_three_years_and_keeps_the_decision(clean):
    from datetime import timedelta
    from mielenosoitukset_fi.utils.time_utils import utcnow

    opened = appeals.create(clean, NEW, "hallinto-sub")
    decided = appeals.create(clean, NEW, "hallinto-sub")
    fresh = appeals.create(clean, NEW, "hallinto-sub")
    long_ago = utcnow() - timedelta(days=1100)
    clean[appeals.COLLECTION].update_one({"_id": decided["_id"]}, {"$set": {
        "status": "decided", "decided_at": long_ago, "letter": {"decision_ref": "MOSO/002/RAT/2026/001"}, "letter_text": "PÄÄTÖS"}})
    clean[appeals.COLLECTION].update_one({"_id": fresh["_id"]}, {"$set": {"status": "decided", "decided_at": utcnow()}})
    clean[appeals.COLLECTION].update_one({"_id": opened["_id"]}, {"$set": {"created_at": long_ago, "updated_at": long_ago}})
    assert clean.email_queue.count_documents({"_id": f"appeal-received:{decided['_id']}"}) == 1
    assert appeals.purge_expired(clean, dry_run=True)["purged"] == 1
    assert appeals.purge_expired(clean)["purged"] == 1
    assert clean.email_queue.count_documents({"_id": f"appeal-received:{decided['_id']}"}) == 0
    assert clean.email_queue.count_documents({"_id": f"appeal-received:{fresh['_id']}"}) == 1
    assert appeals.purge_expired(clean)["purged"] == 0
    gone = clean[appeals.COLLECTION].find_one({"_id": decided["_id"]})
    assert not {"complainant", "text", "token_hash"} & set(gone) and gone["letter"]["decision_ref"] and gone["letter_text"]
    assert "title" not in gone["subject"] and gone["purged_at"]
    assert clean[appeals.COLLECTION].find_one({"_id": opened["_id"]})["complainant"]
    assert clean[appeals.COLLECTION].find_one({"_id": fresh["_id"]})["complainant"]
    assert appeals.purge_expired(clean, days=0)["enabled"] is False
