"""Personal links to the role guides (/ohjeet/k/<token>)."""

from bson import ObjectId

from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils import guide_links
from tests.conftest import _client_for_user


def _translator(db):
    user_id = ObjectId()
    doc = User.create_user(username=f"tr-{str(user_id)[-8:]}", password="TransPass1!",
                           email=f"tr-{str(user_id)[-8:]}@example.test", displayname="Kääntäjä")
    doc.update({"_id": user_id, "confirmed": True, "active": True, "role": "translator",
                "global_admin": False, "global_permissions": ["TRANSLATE_DEMO"]})
    db.users.insert_one(doc)
    return user_id


def _plain_user(db):
    user_id = ObjectId()
    doc = User.create_user(username=f"pl-{str(user_id)[-8:]}", password="PlainPass1!",
                           email=f"pl-{str(user_id)[-8:]}@example.test", displayname="Tavallinen")
    doc.update({"_id": user_id, "confirmed": True, "active": True, "role": "user",
                "global_admin": False, "global_permissions": []})
    db.users.insert_one(doc)
    return user_id


def test_link_shows_only_the_guides_of_the_role_and_is_private(app, db):
    user_id = _translator(db)
    token = guide_links.issue_link(db, user_id)
    stored = db.guide_links.find_one({"user_id": user_id})
    assert token not in str(stored) and stored["token_hash"] == guide_links._hash(token)

    response = app.test_client().get(f"/ohjeet/k/{token}")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "e1-aloita-kaantajana.mp4" in page and "b1-moderator-first-day" not in page
    assert 'kind="captions"' in page and "default" in page
    assert "noindex" in response.headers["X-Robots-Tag"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_new_link_replaces_the_old_one(app, db):
    user_id = _translator(db)
    first = guide_links.issue_link(db, user_id)
    second = guide_links.issue_link(db, user_id)
    client = app.test_client()
    assert client.get(f"/ohjeet/k/{first}").status_code == 404
    assert client.get(f"/ohjeet/k/{second}").status_code == 200


def test_link_dies_when_the_role_ends(app, db):
    user_id = _translator(db)
    token = guide_links.issue_link(db, user_id)
    client = app.test_client()
    assert client.get(f"/ohjeet/k/{token}").status_code == 200
    db.users.update_one({"_id": user_id}, {"$set": {"role": "user", "global_permissions": []}})
    assert client.get(f"/ohjeet/k/{token}").status_code == 404
    assert db.guide_links.find_one({"user_id": user_id})["revoked_reason"] == "role_ended"
    # It stays dead even if the role comes back; a new link is needed.
    db.users.update_one({"_id": user_id}, {"$set": {"role": "translator", "global_permissions": ["TRANSLATE_DEMO"]}})
    assert client.get(f"/ohjeet/k/{token}").status_code == 404


def test_unknown_token_and_banned_account_get_the_same_answer(app, db):
    client = app.test_client()
    assert client.get("/ohjeet/k/ei-ole-olemassa").status_code == 404
    user_id = _translator(db)
    token = guide_links.issue_link(db, user_id)
    db.users.update_one({"_id": user_id}, {"$set": {"banned": True}})
    assert client.get(f"/ohjeet/k/{token}").status_code == 404


def test_code_form_accepts_a_code_or_a_whole_link(app, db):
    user_id = _translator(db)
    token = guide_links.issue_link(db, user_id)
    client = app.test_client()
    assert client.get("/ohjeet/k/").status_code == 200
    response = client.post("/ohjeet/k/", data={"code": f"https://mielenosoitukset.fi/ohjeet/k/{token}"})
    assert response.status_code == 303 and response.headers["Location"].endswith(f"/ohjeet/k/{token}")


def test_admin_page_sends_a_link_and_others_cannot_send_to_someone_else(app, db, seeded_data):
    admin = _client_for_user(app, seeded_data["admin_id"])
    page = admin.get("/admin/ohjeet/")
    assert page.status_code == 200
    with admin.session_transaction() as session:
        csrf = session["governance_csrf_token"]
    target = _translator(db)
    username = db.users.find_one({"_id": target})["username"]
    assert admin.post("/admin/ohjeet/", data={"csrf_token": csrf, "username": username}).status_code == 303
    assert db.guide_links.count_documents({"user_id": target, "revoked_at": None}) == 1
    # A second send right away is refused, so a mailbox cannot be flooded.
    admin.post("/admin/ohjeet/", data={"csrf_token": csrf, "username": username})
    assert db.guide_links.count_documents({"user_id": target}) == 1
    assert admin.post("/admin/ohjeet/", data={"username": username}).status_code == 403  # no CSRF token

    translator = _client_for_user(app, target)
    translator.get("/admin/ohjeet/")
    with translator.session_transaction() as session:
        csrf = session["governance_csrf_token"]
    assert translator.post("/admin/ohjeet/", data={"csrf_token": csrf, "username": username}).status_code == 403


def test_account_without_a_role_has_no_guides(db):
    user = User.from_OID(_plain_user(db))
    assert guide_links.audiences_for(user) == set()


def test_bulk_send_script_counts_then_sends_once(app, db):
    import importlib.util
    from pathlib import Path

    from mielenosoitukset_fi.admin import guide_links_bp as bp

    spec = importlib.util.spec_from_file_location(
        "send_guide_links", Path(__file__).resolve().parents[1] / "scripts" / "send_guide_links.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    translator = _translator(db)
    _plain_user(db)
    with app.test_request_context(base_url="https://mielenosoitukset.fi"):
        dry = script.run(db, bp._load_user, bp.send_link)
        assert dry["holders"] >= 1 and "sent" not in dry
        assert db.guide_links.count_documents({"user_id": translator}) == 0
        first = script.run(db, bp._load_user, bp.send_link, apply=True)
        again = script.run(db, bp._load_user, bp.send_link, apply=True)
    assert first["sent"] + first.get("skipped_recent", 0) == first["holders"] and first["sent"] >= 1 and again["skipped_recent"] == again["holders"]
    assert db.guide_links.count_documents({"user_id": translator, "revoked_at": None}) == 1
