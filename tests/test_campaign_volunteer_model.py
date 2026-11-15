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
    assert stored["confirmed"] is False and stored["confirmation_token"]
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
