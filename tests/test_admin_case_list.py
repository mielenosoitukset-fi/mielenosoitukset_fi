"""Case list: read-only, open first, search, type filter and pages."""

from copy import deepcopy

from bson import ObjectId


def _clone_cases(db, seeded_data, count, **fields):
    base = db.cases.find_one({"_id": seeded_data["case_id"]})
    docs = []
    for index in range(count):
        doc = deepcopy(base)
        doc.update({"_id": ObjectId(), "running_num": 700000 + index, **fields})
        docs.append(doc)
    db.cases.insert_many(docs)
    return docs


def test_list_defaults_to_open_and_shows_counts_once(admin_client, db, seeded_data):
    closed = _clone_cases(db, seeded_data, 1, meta={"closed": True})[0]
    body = admin_client.get("/admin/case/").get_data(as_text=True)
    assert f"/admin/case/{closed['_id']}/" not in body
    assert body.count("Automaattisesti suljetut:") == 0
    body = admin_client.get("/admin/case/?tila=closed").get_data(as_text=True)
    assert f"/admin/case/{closed['_id']}/" in body
    assert f"/admin/case/{seeded_data['case_id']}/" not in body


def test_list_pages_fifty_per_page(admin_client, db, seeded_data):
    _clone_cases(db, seeded_data, 60)
    first = admin_client.get("/admin/case/?tila=all").get_data(as_text=True)
    assert first.count('class="case-card"') == 50
    assert "sivu=2" in first
    second = admin_client.get("/admin/case/?tila=all&sivu=2").get_data(as_text=True)
    assert 0 < second.count('class="case-card"') <= 50
    assert admin_client.get("/admin/case/?sivu=abc&tila=bogus").status_code == 200


def test_search_by_number_and_type_filter(admin_client, db, seeded_data):
    ticket = _clone_cases(db, seeded_data, 1, type="support_ticket", suggestion={"subject": "Kirjautuminen ei toimi"})[0]
    body = admin_client.get("/admin/case/?tila=all&q=700000").get_data(as_text=True)
    assert f"/admin/case/{ticket['_id']}/" in body
    assert f"/admin/case/{seeded_data['case_id']}/" not in body
    body = admin_client.get("/admin/case/?tila=all&q=kirjautuminen").get_data(as_text=True)
    assert f"/admin/case/{ticket['_id']}/" in body
    body = admin_client.get("/admin/case/?tila=all&tyyppi=support_ticket").get_data(as_text=True)
    assert f"/admin/case/{ticket['_id']}/" in body
    assert f"/admin/case/{seeded_data['case_id']}/" not in body
    # Regex metacharacters are searched literally.
    assert admin_client.get("/admin/case/?q=.*(").status_code == 200
