from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event


def _admin_client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _pause_geocoder(monkeypatch, resume, coordinates):
    started = Event()

    def delayed_geocode(address, city):
        started.set()
        assert resume.wait(10), "test did not resume preview request"
        return coordinates

    for module_name in ("basic_routes", "mielenosoitukset_fi.basic_routes"):
        module = import_module(module_name)
        monkeypatch.setattr(module, "geocode_address_lookup", delayed_geocode)
    return started


def test_preview_geocoding_after_approval_preserves_approval(
    app, db, seeded_data, monkeypatch
):
    resume = Event()
    started = _pause_geocoder(monkeypatch, resume, ("60.1699", "24.9384"))
    app.config["PUBLIC_DETAIL_REACT_ENABLED"] = False
    demo_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$unset": {"longitude": "", "latitude": ""}}
    )

    detail_client = _admin_client(app, seeded_data["admin_id"])
    approval_client = _admin_client(app, seeded_data["admin_id"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        preview = pool.submit(
            detail_client.get, f"/demonstration/{demo_id}?preview=1"
        )
        try:
            assert started.wait(10), "preview did not reach geocoding"
            approval = approval_client.post(f"/api/admin/demo/{demo_id}/approve")
            assert approval.status_code == 200
            approved = db.demonstrations.find_one({"_id": demo_id})
            assert approved["approved"] is True
            assert approved["moderation_decision"]["status"] == "approved"
        finally:
            resume.set()
        response = preview.result(timeout=10)

    final = db.demonstrations.find_one({"_id": demo_id})
    assert response.status_code == 200
    assert final["approved"] is True
    assert final["rejected"] is False
    assert final["moderation_decision"]["status"] == "approved"
    assert final["moderation_decision"]["source"] == "admin_api"
    assert final["latitude"] == "60.1699"
    assert final["longitude"] == "24.9384"


def test_preview_geocoding_does_not_replace_coordinates_added_while_waiting(
    app, db, seeded_data, monkeypatch
):
    resume = Event()
    started = _pause_geocoder(monkeypatch, resume, ("60.1699", "24.9384"))
    app.config["PUBLIC_DETAIL_REACT_ENABLED"] = False
    demo_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$unset": {"longitude": "", "latitude": ""}}
    )

    detail_client = _admin_client(app, seeded_data["admin_id"])
    approval_client = _admin_client(app, seeded_data["admin_id"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        preview = pool.submit(
            detail_client.get, f"/demonstration/{demo_id}?preview=1"
        )
        try:
            assert started.wait(10), "preview did not reach geocoding"
            approval = approval_client.post(f"/api/admin/demo/{demo_id}/approve")
            assert approval.status_code == 200
            db.demonstrations.update_one(
                {"_id": demo_id},
                {"$set": {"latitude": "60.2000", "longitude": "24.9000"}},
            )
        finally:
            resume.set()
        response = preview.result(timeout=10)

    final = db.demonstrations.find_one({"_id": demo_id})
    assert response.status_code == 200
    assert final["approved"] is True
    assert final["moderation_decision"]["status"] == "approved"
    assert final["latitude"] == "60.2000"
    assert final["longitude"] == "24.9000"


def test_loading_normalizes_legacy_times_without_implicit_database_write(
    db, seeded_data
):
    from mielenosoitukset_fi.utils.classes.Demonstration import Demonstration

    demo_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$set": {"start_time": "12:00", "end_time": ""}}
    )
    before = db.demonstrations.find_one({"_id": demo_id})

    loaded = Demonstration.load_by_id(str(demo_id))

    after = db.demonstrations.find_one({"_id": demo_id})
    assert loaded.start_time == "12:00:00"
    assert loaded.end_time is None
    assert after["start_time"] == "12:00"
    assert after["end_time"] == ""
    assert after["last_modified"] == before["last_modified"]


def test_normalization_persistence_requires_explicit_opt_in(db, seeded_data):
    from mielenosoitukset_fi.utils.classes.Demonstration import Demonstration

    demo_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$set": {"start_time": "12:00", "end_time": ""}}
    )
    raw = db.demonstrations.find_one({"_id": demo_id})

    Demonstration.from_dict(raw, save_on_normalize=True)

    persisted = db.demonstrations.find_one({"_id": demo_id})
    assert persisted["start_time"] == "12:00:00"
    assert persisted["end_time"] is None


def test_direct_constructor_normalization_does_not_save_by_default(
    db, seeded_data
):
    from mielenosoitukset_fi.utils.classes.Demonstration import Demonstration

    demo_id = seeded_data["pending_demo_id"]
    before = db.demonstrations.find_one({"_id": demo_id})

    model = Demonstration(
        title=before["title"],
        date=before["date"],
        start_time="12:00",
        end_time="",
        city=before["city"],
        address=before["address"],
        aliases=before.get("aliases", []),
        _id=demo_id,
    )

    after = db.demonstrations.find_one({"_id": demo_id})
    assert model.start_time == "12:00:00"
    assert model.end_time is None
    assert after == before
