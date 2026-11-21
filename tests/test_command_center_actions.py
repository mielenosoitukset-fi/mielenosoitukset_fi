"""Command center offers only the actions that fit the demo's state (audit #11)."""


def _page(admin_client, demo_id):
    return admin_client.get(f"/admin/demo/command-center/{demo_id}").get_data(as_text=True)


def test_pending_demo_offers_decision_and_preview_not_recommend(admin_client, seeded_data):
    page = _page(admin_client, seeded_data["pending_demo_id"])
    assert "Hyväksy tai hylkää tapauksessa" in page
    assert 'data-command="toggle-recommend"' not in page
    assert 'data-command="screenshot"' not in page
    assert f"/admin/demo/{seeded_data['pending_demo_id']}/preview" in page
    assert "Avaa julkinen sivu" not in page


def test_approved_demo_offers_recommend_and_public_page(admin_client, seeded_data):
    page = _page(admin_client, seeded_data["demo_id"])
    assert "Hyväksy tai hylkää tapauksessa" not in page
    assert 'data-command="toggle-recommend"' in page
    assert 'data-command="screenshot"' in page
    assert "Avaa julkinen sivu" in page


def test_rejected_demo_cannot_be_cancelled_or_recommended(admin_client, db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["pending_demo_id"]}, {"$set": {"rejected": True}})
    page = _page(admin_client, seeded_data["pending_demo_id"])
    assert 'data-command="cancel"' not in page
    assert 'data-command="toggle-recommend"' not in page
    assert "Mielenosoitus on hylätty" in page


def test_links_appear_once(admin_client, seeded_data):
    page = _page(admin_client, seeded_data["demo_id"])
    assert page.count(f'/admin/demo/edit_demo/{seeded_data["demo_id"]}"') == 1
    assert page.count("Muokkaushistoria</a>") == 1
    assert page.count("Audit-loki</a>") == 1
