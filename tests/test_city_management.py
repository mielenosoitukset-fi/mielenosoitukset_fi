from tests.conftest import _client_for_user
from mielenosoitukset_fi.utils.time_utils import utcnow


def test_public_cities_lists_enabled_contact_cities_and_demo_cities(app, db, seeded_data):
    response = app.test_client().get("/cities")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Tampere" in page
    assert "Helsinki" in page
    assert "Varkaus" not in page


def test_admin_city_control_can_toggle_enabled_city(app, db, seeded_data):
    client = _client_for_user(app, seeded_data["admin_id"])

    response = client.post(
        "/admin/cities/",
        data={"enabled_cities[]": ["tampere", "oulu"]},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert db.city_settings.find_one({"city_key": "tampere"})["enabled"] is True
    assert db.city_settings.find_one({"city_key": "oulu"})["enabled"] is True
    assert db.city_settings.find_one({"city_key": "turku"})["enabled"] is False


def test_admin_city_control_aggregates_counts_and_offers_bulk_tools(app, db, seeded_data):
    db.demonstrations.insert_one(
        {
            "title": "City-key demonstration",
            "city": "Helsinki",
            "city_key": "helsinki",
        }
    )
    db.admin_scope_grants.insert_one(
        {
            "user_id": seeded_data["user_id"],
            "scope_type": "city",
            "scope_keys": ["helsinki", "turku"],
            "permissions": ["LIST_DEMOS"],
        }
    )
    client = _client_for_user(app, seeded_data["admin_id"])

    response = client.get("/admin/cities/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'data-city-name="Helsinki" data-demo-count="4" data-grant-count="1"' in page
    assert 'data-city-name="Turku" data-demo-count="0" data-grant-count="1"' in page
    assert '<option value="relevant" selected>' in page
    assert "Ota näkyvät käyttöön" in page
    assert "Poista näkyvät käytöstä" in page
    assert "Näytetään" in page


def test_admin_city_control_grant_count_links_to_filtered_user_list(app, db, seeded_data):
    admin_id = seeded_data["admin_id"]
    user_id = seeded_data["user_id"]
    db.admin_scope_grants.insert_one(
        {
            "user_id": user_id,
            "scope_type": "city",
            "scope_keys": ["varkaus"],
            "permissions": ["LIST_DEMOS"],
        }
    )
    client = _client_for_user(app, admin_id)

    response = client.get("/admin/cities/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert f'href="/admin/user/?city=varkaus"' in page
    # The grant count for Varkaus is rendered as a link because it is > 0.
    assert 'data-city-name="varkaus"' in page


def test_admin_city_control_shows_admin_coverage_against_the_two_admin_target(app, db, seeded_data):
    from bson import ObjectId

    db.admin_scope_grants.delete_many({})
    db.city_settings.update_many({}, {"$set": {"enabled": False}})
    for key in ("tampere", "turku", "oulu", "kuopio"):
        db.city_settings.update_one({"city_key": key}, {"$set": {"enabled": True}}, upsert=True)

    def account(**fields):
        user_id = ObjectId()
        db.users.insert_one({"_id": user_id, "username": f"u{user_id}", "active": True, "confirmed": True, **fields})
        return user_id

    def grant(user_id, keys, **fields):
        db.admin_scope_grants.insert_one({"user_id": user_id, "scope_type": "city", "scope_keys": keys,
                                          "permissions": ["LIST_DEMOS", "ACCEPT_DEMO"], **fields})

    first, second, banned, revoked = account(), account(), account(banned=True), account()
    viewer, scalar = account(), account()
    grant(first, ["tampere", "turku"])
    grant(viewer, ["oulu"], permissions=["LIST_DEMOS", "VIEW_CASE"])  # cannot approve: not coverage
    grant(scalar, "kuopio")                   # a single string scope still counts
    grant(str(second), ["tampere"])           # string user ids are still counted
    grant(banned, ["turku"])                  # inactive accounts are not coverage
    grant(revoked, ["oulu"], revoked_at=utcnow())

    page = _client_for_user(app, seeded_data["admin_id"]).get("/admin/cities/").get_data(as_text=True)

    def coverage_cell(city):
        row = page[page.index(f'data-city-name="{city.lower()}"'):]
        return row[row.index('city-admin-coverage--'):row.index('</td>', row.index('city-admin-coverage--'))]

    assert "city-admin-coverage--team" in coverage_cell("Tampere") and "Tiimi: 2" in coverage_cell("Tampere")
    assert "city-admin-coverage--single" in coverage_cell("Turku") and "Yksi, haetaan toista" in coverage_cell("Turku")
    assert "city-admin-coverage--none" in coverage_cell("Oulu") and "Ei kaupunkiadminia" in coverage_cell("Oulu")
    assert "city-admin-coverage--single" in coverage_cell("Kuopio")
    assert '<option value="needs-admins">' in page
    assert "city-admin-coverage--national" in coverage_cell("Helsinki") and "Valtakunnallinen tiimi" in coverage_cell("Helsinki")
    assert "Tavoite on vähintään 2 aktiivista kaupunkiadminia" in page
