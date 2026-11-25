from datetime import datetime, timezone

from bson import ObjectId

from mielenosoitukset_fi.users.models import User


def _insert_admin_user_list_entries(db):
    entries = [
        {
            "_id": ObjectId(),
            "username": "pagination-user-01",
            "displayname": "Pagination User 01",
            "email": "pagination-user-01@example.test",
            "role": "user",
            "confirmed": True,
            "last_login": datetime.now(timezone.utc),
            "global_permissions": [],
        },
        {
            "_id": ObjectId(),
            "username": "pagination-user-02",
            "displayname": "Pagination User 02",
            "email": "pagination-user-02@example.test",
            "role": "user",
            "confirmed": True,
            "last_login": datetime.now(timezone.utc),
            "global_permissions": [],
        },
        {
            "_id": ObjectId(),
            "username": "pagination-user-03",
            "displayname": "Pagination User 03",
            "email": "pagination-user-03@example.test",
            "role": "user",
            "confirmed": True,
            "last_login": datetime.now(timezone.utc),
            "global_permissions": [],
        },
        {
            "_id": ObjectId(),
            "username": "displayname-search-user",
            "displayname": "Friendly Admin",
            "email": "friendly-admin@example.test",
            "role": "user",
            "confirmed": True,
            "last_login": datetime.now(timezone.utc),
            "global_permissions": [],
        },
    ]
    db.users.insert_many(entries)


def test_user_control_paginates_results(admin_client, db, seeded_data):
    _insert_admin_user_list_entries(db)

    first_page = admin_client.get("/admin/user/?search=pagination-user-&page=1&per_page=2")
    second_page = admin_client.get("/admin/user/?search=pagination-user-&page=2&per_page=2")

    assert first_page.status_code == 200
    assert second_page.status_code == 200

    first_body = first_page.get_data(as_text=True)
    second_body = second_page.get_data(as_text=True)

    assert "pagination-user-01" in first_body
    assert "pagination-user-02" in first_body
    assert "pagination-user-03" not in first_body
    assert "Sivu 1 / 2" in first_body

    assert "pagination-user-03" in second_body
    assert "Sivu 2 / 2" in second_body


def test_user_control_searches_display_names(admin_client, db, seeded_data):
    _insert_admin_user_list_entries(db)

    response = admin_client.get("/admin/user/?search=friendly")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Friendly Admin" in body
    assert "displayname-search-user" in body


def test_user_control_filters_by_city_admin_scope(admin_client, db, seeded_data):
    """The user list can filter users by the city they hold an active admin grant for."""
    # Two users with tampere grant, one with oulu grant only.
    tampere_users = [ObjectId(), ObjectId()]
    oulu_user = ObjectId()
    db.users.insert_many(
        [
            {
                "_id": oid,
                "username": f"tampere-admin-{idx:02d}",
                "role": "city_admin",
                "email": f"tampere-admin-{idx:02d}@example.test",
                "confirmed": True,
            }
            for idx, oid in enumerate(tampere_users)
        ]
    )
    db.users.insert_one(
        {
            "_id": oulu_user,
            "username": "oulu-admin-01",
            "role": "city_admin",
            "email": "oulu-admin-01@example.test",
            "confirmed": True,
        }
    )
    for oid in tampere_users:
        db.admin_scope_grants.insert_one(
            {
                "user_id": oid,
                "scope_type": "city",
                "scope_keys": ["tampere"],
                "permissions": ["LIST_DEMOS"],
            }
        )
    db.admin_scope_grants.insert_one(
        {
            "user_id": oulu_user,
            "scope_type": "city",
            "scope_keys": ["oulu"],
            "permissions": ["LIST_DEMOS"],
        }
    )

    response = admin_client.get("/admin/user/?city=tampere")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "tampere-admin-00" in body
    assert "tampere-admin-01" in body
    assert "oulu-admin-01" not in body
    assert "Kaupunki" in body

    # Normalized key form also works.
    response2 = admin_client.get("/admin/user/?city=Tampere")
    assert response2.status_code == 200
    assert "tampere-admin-00" in response2.get_data(as_text=True)

    # Unknown city falls back to full list.
    response3 = admin_client.get("/admin/user/?city=nowhereville")
    assert response3.status_code == 200
    assert "tampere-admin-00" in response3.get_data(as_text=True)


def test_create_city_admin_requires_city_selection(admin_client, db, seeded_data):
    response = admin_client.post(
        "/admin/user/create_user",
        data={
            "email": "cityless-admin@example.test",
            "username": "cityless-admin",
            "role": "city_admin",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert db.users.find_one({"email": "cityless-admin@example.test"}) is None


def test_create_user_rejects_reserved_admin_identity(admin_client, db, seeded_data):
    response = admin_client.post(
        "/admin/user/create_user",
        data={
            "email": "reserved-admin@example.test",
            "username": "regular-user",
            "displayname": "@Admin",
            "role": "user",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert db.users.find_one({"email": "reserved-admin@example.test"}) is None


def test_create_user_persists_active_true(admin_client, db, seeded_data):
    response = admin_client.post(
        "/admin/user/create_user",
        data={
            "email": "active-test@example.test",
            "username": "active-test-user",
            "displayname": "Active Test",
            "role": "user",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    user_doc = db.users.find_one({"email": "active-test@example.test"})
    assert user_doc is not None
    assert user_doc["active"] is True
    assert user_doc["banned"] is False


def test_user_from_db_defaults_active_true(db, seeded_data):
    oid = ObjectId()
    db.users.insert_one(
        {
            "_id": oid,
            "username": "legacy-no-active",
            "email": "legacy-no-active@example.test",
            "password_hash": "fake",
            "role": "user",
            "confirmed": True,
        }
    )

    doc = db.users.find_one({"_id": oid})
    user = User.from_db(doc)
    assert user.active is True


