import re


def _token(client):
    response = client.get("/valitus")
    assert response.status_code == 200
    assert "uudelleenarvioin".encode() in response.data
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


def _payload(token, **updates):
    data = {
        "csrf_token": token, "website": "", "kind": "valitus", "title": "Mielenosoitus X",
        "reference": "", "text": "Päätös on mielestäni väärä, koska perustelut puuttuvat.",
        "name": "Testi Ihminen", "email": "Testi@Example.Test ", "ack_reconsideration": "yes",
    }
    data.update(updates)
    return data


def _setup(app, monkeypatch):
    import basic_routes
    import mongomock

    app.config["CONTACT_MIN_FORM_SECONDS"] = 0
    db = mongomock.MongoClient().db
    monkeypatch.setattr(basic_routes, "mongo", db)
    for limiter in app.extensions.get("limiter", ()):
        limiter.reset()
    return db


def test_public_form_stores_appeal_and_queues_confirmation(app, client, monkeypatch):
    db = _setup(app, monkeypatch)
    response = client.post("/valitus", data=_payload(_token(client)))
    assert response.status_code == 302
    row = db.appeals.find_one()
    assert row["source"] == "public-form"
    assert row["complainant"]["email"] == "testi@example.test"
    assert db.email_queue.count_documents({}) == 1


def test_public_form_needs_ack_and_ignores_honeypot_and_bad_token(app, client, monkeypatch):
    db = _setup(app, monkeypatch)
    client.post("/valitus", data=_payload(_token(client), ack_reconsideration=""))
    client.post("/valitus", data=_payload(_token(client), website="http://spam"))
    assert client.post("/valitus", data=_payload("nope")).status_code == 400
    assert db.appeals.count_documents({}) == 0
    token = _token(client)
    client.post("/valitus", data=_payload(token, email="not-an-email"))
    assert db.appeals.count_documents({}) == 0
