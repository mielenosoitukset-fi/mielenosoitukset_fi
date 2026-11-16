"""The volunteer signup stores the new data model and still reads old documents."""

import pytest


@pytest.fixture(autouse=True)
def published_campaign(db, seeded_data):
    db.campaign_settings.update_one({"_id": "public_pages"}, {"$set": {"published": True}}, upsert=True)
    yield
    db.campaign_settings.delete_one({"_id": "public_pages"})


@pytest.fixture
def queued(monkeypatch):
    from mielenosoitukset_fi.kampanja.routes import email_sender

    sent = []
    monkeypatch.setattr(email_sender, "queue_email", lambda **kwargs: sent.append(kwargs))
    return sent


def _post(client, **payload):
    return client.post("/kampanja/api/volunteers", json=payload)


def test_new_signup_stores_roles_and_area_but_never_a_phone_number(client, db, queued):
    response = _post(
        client, name=" Aino ", email="aino-roles@example.test", city=" Tampere ",
        roles=["communications", "stickers"], area=" Tampereen keskusta ", notes=" Hei ",
        phone="040 123 4567", unexpected="x",
    )
    assert response.status_code == 201
    stored = db.volunteers.find_one({"email": "aino-roles@example.test"})
    assert stored["name"] == "Aino" and stored["city"] == "Tampere" and stored["notes"] == "Hei"
    assert stored["roles"] == ["stickers", "communications"]
    assert stored["area"] == "Tampereen keskusta"
    assert "phone" not in stored and "unexpected" not in stored
    assert stored["confirmed"] is False and stored["confirmation_token_hash"] and "confirmation_token" not in stored
    assert len(queued) == 1 and queued[0]["recipients"] == ["aino-roles@example.test"]


def test_area_is_dropped_unless_stickers_are_chosen(client, db, queued):
    assert _post(client, name="Aino", email="aino-area@example.test", roles=["events"], area="Turku").status_code == 201
    stored = db.volunteers.find_one({"email": "aino-area@example.test"})
    assert stored["roles"] == ["events"] and stored["area"] == ""


def test_roles_and_area_are_optional(client, db, queued):
    assert _post(client, name="Aino", email="aino-min@example.test").status_code == 201
    stored = db.volunteers.find_one({"email": "aino-min@example.test"})
    assert stored["roles"] == [] and stored["area"] == "" and stored["city"] == ""


@pytest.mark.parametrize("payload", [
    {"roles": "stickers"},
    {"roles": ["stickers", "hacker"]},
    {"roles": [["stickers"]]},
    {"email": "not-an-email"},
    {"name": "x" * 121},
    {"notes": "x" * 2001},
    {"city": "x" * 101},
])
def test_invalid_values_are_rejected_without_storing(client, db, queued, payload):
    before = db.volunteers.count_documents({})
    body = {"name": "Aino", "email": "aino-bad@example.test", **payload}
    response = _post(client, **body)
    assert response.status_code == 400 and response.get_json()["success"] is False
    assert db.volunteers.count_documents({}) == before and not queued


def test_non_object_json_is_a_validation_error(client, queued):
    assert client.post("/kampanja/api/volunteers", json=["x"]).status_code == 400
    assert client.post("/kampanja/api/volunteers", data="nope", content_type="application/json").status_code == 400


def test_old_documents_still_confirm_and_export(client, db, admin_client):
    from datetime import timedelta
    from mielenosoitukset_fi.utils.time_utils import utcnow

    db.volunteers.insert_one({
        "name": "Vanha", "email": "old-doc@example.test", "phone": "0401", "city": "Oulu", "notes": "",
        "confirmed": False, "confirmation_token": "old-token-1", "confirmation_expires": utcnow() + timedelta(hours=1),
    })
    assert client.get("/kampanja/confirm/old-token-1").status_code == 200
    export = admin_client.get("/admin/kampanja/api/volunteers/export/csv")
    assert export.status_code == 200
    text = export.get_data(as_text=True)
    assert "Roles" in text.splitlines()[0] and "old-doc@example.test" in text


def test_link_token_is_stored_only_as_a_hash_and_confirms(client, db, queued):
    _post(client, name="Aino", email="aino-hash@example.test")
    token = queued[0]["context"]["confirm_link"].rsplit("/", 1)[1]
    stored = db.volunteers.find_one({"email": "aino-hash@example.test"})
    assert token not in str(stored)
    assert client.get("/kampanja/confirm/" + token).status_code == 200
    confirmed = db.volunteers.find_one({"_id": stored["_id"]})
    assert confirmed["confirmed"] is True and "confirmation_token_hash" not in confirmed


def test_same_address_gets_one_mail_per_hour_without_revealing_it(client, db, queued):
    statuses = [_post(client, name="Aino", email="repeat@example.test").status_code for _ in range(4)]
    assert statuses == [201, 201, 201, 201]
    assert len(queued) == 2 and db.volunteers.count_documents({"email": "repeat@example.test"}) == 2


def test_one_client_is_limited_across_addresses(client, db, queued):
    statuses = [_post(client, name="Aino", email=f"many{i}@example.test").status_code for i in range(7)]
    assert statuses[:5] == [201] * 5 and statuses[5:] == [429, 429]
    assert len(queued) == 5


def test_old_unconfirmed_signups_are_purged_on_the_next_signup(client, db, queued):
    from datetime import timedelta
    from mielenosoitukset_fi.utils.time_utils import utcnow

    db.volunteers.insert_many([
        {"name": "Old", "email": "stale@example.test", "confirmed": False, "confirmation_expires": utcnow() - timedelta(days=8)},
        {"name": "Fresh", "email": "fresh@example.test", "confirmed": False, "confirmation_expires": utcnow() - timedelta(days=2)},
        {"name": "Done", "email": "done@example.test", "confirmed": True, "confirmation_expires": utcnow() - timedelta(days=30)},
    ])
    _post(client, name="Aino", email="purge-trigger@example.test")
    left = {v["email"] for v in db.volunteers.find({})}
    assert "stale@example.test" not in left and {"fresh@example.test", "done@example.test"} <= left


def test_format_characters_are_stripped_from_free_text(client, db, queued):
    _post(client, name="Ai\u202eno\u200b", email="aino-cf@example.test", notes="a\u200bb")
    stored = db.volunteers.find_one({"email": "aino-cf@example.test"})
    assert stored["name"] == "Aino" and stored["notes"] == "ab"


def test_export_escapes_formula_cells(admin_client, db):
    db.volunteers.insert_one({"name": "=HYPERLINK(\"x\")", "email": "csv@example.test", "notes": "+1", "confirmed": True})
    text = admin_client.get("/admin/kampanja/api/volunteers/export/csv").get_data(as_text=True)
    assert "'=HYPERLINK" in text and "'+1" in text and ",=HYPERLINK" not in text


def test_season_purge_waits_for_the_end_date_and_supports_dry_run(db):
    from datetime import datetime, timezone
    from mielenosoitukset_fi.scripts import purge_campaign_volunteers as purge

    db.volunteers.delete_many({})
    db.volunteers.insert_many([{"email": "a@example.test", "confirmed": True}, {"email": "b@example.test", "confirmed": False}])
    before = datetime(2027, 5, 31, 20, 59, tzinfo=timezone.utc)  # 23.59 Helsinki
    assert purge.purge(db, now=before) == {"due": False, "deleted": 0}
    assert db.volunteers.count_documents({}) == 2
    after = datetime(2027, 5, 31, 21, 0, tzinfo=timezone.utc)  # 1.6. 00.00 Helsinki
    assert purge.purge(db, now=after, dry_run=True) == {"due": True, "would_delete": 2}
    assert db.volunteers.count_documents({}) == 2
    assert purge.purge(db, now=after) == {"due": True, "deleted": 2}
    assert db.volunteers.count_documents({}) == 0


def test_purge_job_is_registered():
    from mielenosoitukset_fi.background_jobs.definitions import JOB_DEFINITION_MAP

    assert "purge_campaign_volunteers" in JOB_DEFINITION_MAP
