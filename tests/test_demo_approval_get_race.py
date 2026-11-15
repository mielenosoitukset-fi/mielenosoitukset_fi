from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event


def test_detail_get_geocode_save_can_restore_stale_unapproved_state(
    app, db, seeded_data, monkeypatch
):
    """A GET that loaded approved=false can save it after a token approval."""
    started, resume = Event(), Event()

    def delayed_geocode(address, city):
        started.set()
        assert resume.wait(10), "approval did not release detail GET"
        return "60.1699", "24.9384"

    for module_name in ("basic_routes", "mielenosoitukset_fi.basic_routes"):
        module = import_module(module_name)
        monkeypatch.setattr(module, "geocode_address_lookup", delayed_geocode)

    demo_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one(
        {"_id": demo_id}, {"$unset": {"longitude": "", "latitude": ""}}
    )

    detail_client = app.test_client()
    with detail_client.session_transaction() as session:
        session["_user_id"] = str(seeded_data["admin_id"])
        session["_fresh"] = True
    approval_client = app.test_client()

    with ThreadPoolExecutor(max_workers=1) as pool:
        detail_request = pool.submit(
            detail_client.get, f"/demonstration/{demo_id}"
        )
        try:
            assert started.wait(10), "detail GET did not reach geocoding"
            approval = approval_client.post(
                f"/admin/demo/approve_demo_with_token/{seeded_data['approve_token']}"
            )
            assert approval.status_code == 302
        finally:
            resume.set()
        response = detail_request.result(timeout=10)

    assert response.status_code == 200
    final = db.demonstrations.find_one({"_id": demo_id})
    assert final["approved"] is False
    assert final["moderation_decision"]["status"] == "approved"
    assert final["moderation_decision"]["source"] == "token"
    assert final["latitude"] == "60.1699"
    assert final["longitude"] == "24.9384"
