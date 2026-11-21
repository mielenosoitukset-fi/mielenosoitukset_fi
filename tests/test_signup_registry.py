"""One admin page for every volunteer / interest sign-up list."""
import re
from datetime import datetime, timezone

import pytest

from mielenosoitukset_fi.utils import volunteer_registry as registry


def _csrf(client, source, row_id):
    page = client.get(f"/admin/kampanja/ilmoittautumiset/{source}/{row_id}/poista")
    assert page.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))[1]


@pytest.fixture
def lists(db):
    now = datetime.now(timezone.utc)
    vid = db.volunteers.insert_one({"name": "Vera", "email": "vera@example.test", "city": "Tampere",
                                    "roles": ["stickers", "events"], "notes": "=HYPERLINK(1)", "confirmed": False}).inserted_id
    pid = db.pride_banner_volunteers.insert_one({"email": "pat@example.test", "name": "Pat", "city": "Turku",
                                                 "pride": "Turku Pride", "status": "confirmed", "created_at": now}).inserted_id
    oid = db.campaign_opening_interest.insert_one({"email": "olli@example.test", "city": "Tampere", "willing": "yes",
                                                   "status": "confirmed", "created_at": now}).inserted_id
    return {"kampanja": str(vid), "pride": str(pid), "avajaiset": str(oid)}


def test_every_list_has_a_tab_with_its_rows(admin_client, lists):
    for key, needle in (("kampanja", "vera@example.test"), ("pride", "pat@example.test"),
                        ("avajaiset", "olli@example.test")):
        page = admin_client.get(f"/admin/kampanja/ilmoittautumiset?lahde={key}")
        assert page.status_code == 200
        assert needle in page.get_data(as_text=True)


def test_unknown_list_and_status_are_rejected(admin_client):
    assert admin_client.get("/admin/kampanja/ilmoittautumiset?lahde=x").status_code == 404
    assert admin_client.get("/admin/kampanja/ilmoittautumiset?tila=x").status_code == 400
    assert admin_client.get("/admin/kampanja/ilmoittautumiset/x.csv").status_code == 404


def test_pages_need_login(client):
    assert client.get("/admin/kampanja/ilmoittautumiset").status_code in (301, 302, 401, 403)


def test_csv_is_formula_safe_and_audited(admin_client, db, lists):
    response = admin_client.get("/admin/kampanja/ilmoittautumiset/kampanja.csv")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "'=HYPERLINK(1)" in body and "vera@example.test" in body
    action = db.volunteer_admin_actions.find_one({"action": "export", "source": "kampanja"})
    assert action and action["count"] == db.volunteers.count_documents({}) and "vera@example.test" not in str(action)


def test_delete_confirmation_page_names_the_row(admin_client, lists):
    page = admin_client.get(f"/admin/kampanja/ilmoittautumiset/pride/{lists['pride']}/poista")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "pat@example.test" in html and "poistetaan pysyvästi" in html
    assert admin_client.get("/admin/kampanja/ilmoittautumiset/pride/nope/poista").status_code == 404


def test_roles_show_finnish_labels(admin_client, lists):
    html = admin_client.get("/admin/kampanja/ilmoittautumiset?lahde=kampanja").get_data(as_text=True)
    assert "Tarrat, Tapahtumat" in html and "stickers" not in html


def test_delete_needs_csrf_and_reason_then_leaves_a_trace(admin_client, db, lists):
    url = f"/admin/kampanja/ilmoittautumiset/pride/{lists['pride']}/poista"
    assert admin_client.post(url, data={"reason": "pyyntö"}).status_code == 400
    token = _csrf(admin_client, "pride", lists["pride"])
    admin_client.post(url, data={"csrf_token": token, "reason": " "})
    assert db.pride_banner_volunteers.count_documents({}) == 1
    response = admin_client.post(url, data={"csrf_token": token, "reason": "Henkilö pyysi poistoa"})
    assert response.status_code == 303
    assert db.pride_banner_volunteers.count_documents({}) == 0
    action = db.volunteer_admin_actions.find_one({"action": "delete", "source": "pride"})
    assert action["reason"] == "Henkilö pyysi poistoa" and action["row_id"] == lists["pride"]
    assert "pat@example.test" not in str(action) and "Pat" not in str(action["snapshot"])


def test_old_confirm_and_edit_routes_are_gone(admin_client, lists):
    base = "/admin/kampanja/api/volunteers"
    assert admin_client.post(f"{base}/{lists['kampanja']}/confirm").status_code == 404
    assert admin_client.post(f"{base}/confirm_all").status_code == 404
    assert admin_client.put(f"{base}/{lists['kampanja']}", json={"confirmed": True}).status_code == 404


def test_counts_cover_all_three_lists(db, lists):
    counts = {c["key"]: c for c in registry.counts(db)}
    assert counts["kampanja"]["pending"] == db.volunteers.count_documents({"confirmed": {"$ne": True}}) >= 1
    assert counts["pride"]["confirmed"] == db.pride_banner_volunteers.count_documents({"status": "confirmed"}) >= 1
    assert counts["avajaiset"]["total"] == db.campaign_opening_interest.count_documents({}) >= 1


def test_delete_page_shows_legacy_phone_only_when_present(admin_client, db):
    with_phone = db.volunteers.insert_one({"name": "Vanha", "email": "ph@example.test", "phone": "0401234", "confirmed": True}).inserted_id
    without = db.volunteers.insert_one({"name": "Uusi", "email": "nph@example.test", "confirmed": True}).inserted_id
    assert "0401234" in admin_client.get(f"/admin/kampanja/ilmoittautumiset/kampanja/{with_phone}/poista").get_data(as_text=True)
    assert "Puhelin" not in admin_client.get(f"/admin/kampanja/ilmoittautumiset/kampanja/{without}/poista").get_data(as_text=True)
