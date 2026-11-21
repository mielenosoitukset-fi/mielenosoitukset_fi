"""Audit item 5: rejected demos can be found; "Kaikki" includes them."""


def _reject(db, seeded_data):
    db.demonstrations.update_one(
        {"_id": seeded_data["pending_demo_id"]},
        {"$set": {"approved": False, "rejected": True, "title": "Rejected Demo Xyz"}},
    )


def test_all_includes_rejected_in_its_own_group(admin_client, db, seeded_data):
    _reject(db, seeded_data)
    body = admin_client.get("/admin/demo/?approved=all").get_data(as_text=True)
    assert "Rejected Demo Xyz" in body and "Hylätyt mielenosoitukset" in body


def test_rejected_filter_shows_only_rejected(admin_client, db, seeded_data):
    _reject(db, seeded_data)
    body = admin_client.get("/admin/demo/?approved=rejected").get_data(as_text=True)
    assert "Rejected Demo Xyz" in body
    assert "Hyväksytyt mielenosoitukset" not in body


def test_pending_filter_leaves_rejected_out(admin_client, db, seeded_data):
    _reject(db, seeded_data)
    body = admin_client.get("/admin/demo/?approved=pending").get_data(as_text=True)
    assert "Rejected Demo Xyz" not in body
