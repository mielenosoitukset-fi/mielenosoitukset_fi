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


def _setup(app, db, monkeypatch):
    import basic_routes

    app.config["CONTACT_MIN_FORM_SECONDS"] = 0
    db.appeals.delete_many({})
    db.email_queue.delete_many({"_id": {"$regex": "^appeal-"}})
    monkeypatch.setattr(basic_routes, "mongo", db)
    for limiter in app.extensions.get("limiter", ()):
        limiter.reset()
    return db


def test_public_form_stores_appeal_and_queues_confirmation(app, client, db, monkeypatch):
    _setup(app, db, monkeypatch)
    response = client.post("/valitus", data=_payload(_token(client)))
    assert response.status_code == 302
    row = db.appeals.find_one()
    assert row["source"] == "public-form"
    assert row["complainant"]["email"] == "testi@example.test"
    assert db.email_queue.count_documents({"_id": {"$regex": "^appeal-"}}) == 1


def test_public_form_needs_ack_and_ignores_honeypot_and_bad_token(app, client, db, monkeypatch):
    _setup(app, db, monkeypatch)
    client.post("/valitus", data=_payload(_token(client), ack_reconsideration=""))
    client.post("/valitus", data=_payload(_token(client), website="http://spam"))
    assert client.post("/valitus", data=_payload("nope")).status_code == 400
    assert db.appeals.count_documents({}) == 0
    token = _token(client)
    client.post("/valitus", data=_payload(token, email="not-an-email"))
    assert db.appeals.count_documents({}) == 0


def test_status_page_shows_stage_without_names_and_unknown_is_404(app, client, db, monkeypatch):
    _setup(app, db, monkeypatch)
    client.post("/valitus", data=_payload(_token(client)))
    row = db.appeals.find_one()
    mail = db.email_queue.find_one({"_id": f"appeal-received:{row['_id']}"})
    token = re.search(r"/valitus/tila/([A-Za-z0-9_-]+)", mail["body"]).group(1)
    page = client.get(f"/valitus/tila/{token}")
    assert page.status_code == 200
    assert page.headers["Referrer-Policy"] == "no-referrer" and "no-store" in page.headers["Cache-Control"]
    assert b"Vastaanotettu" in page.data and b"Testi Ihminen" not in page.data
    assert client.get("/valitus/tila/" + "a" * 32).status_code == 404
