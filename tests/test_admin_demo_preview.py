"""Staff preview: the public React detail page with the payload preloaded."""
import json
import re

import pytest

from mielenosoitukset_fi.utils.public_payload import public_payload


def props_from(response):
    match = re.search(r'<script type="application/json" id="demonstration-detail-props">(.*?)</script>', response.text, re.S)
    assert match, response.text[:500]
    return json.loads(match.group(1))


def assert_preview(response, demo):
    assert response.status_code == 200
    assert response.headers["X-Detail-Renderer"] == "react-preview"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert response.headers["Cache-Control"] == "no-store"
    assert "noindex" in response.headers["X-Robots-Tag"]
    assert 'src/entries/detail.tsx' in response.text or '/static/frontend/' in response.text
    props = props_from(response)
    assert props["id"] == str(demo["_id"])
    assert props["authenticated"] is False
    assert props["preview"]["event"] == json.loads(json.dumps(public_payload(demo, "fi")))
    return props


def test_admin_preview_shows_pending_demo(admin_client, db, seeded_data):
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    response = admin_client.get(f"/admin/demo/{demo['_id']}/preview")
    props = assert_preview(response, demo)
    assert props["preview"]["event"]["title"] == "Pending Demonstration"


def test_preview_leaks_no_private_fields(admin_client, db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["pending_demo_id"]}, {"$set": {
        "submitter": {"email": "submitter-secret@example.test"},
        "organizers": [{"name": "Org", "email": "hidden@example.test", "show_email_public": False}],
    }})
    response = admin_client.get(f"/admin/demo/{seeded_data['pending_demo_id']}/preview")
    assert response.status_code == 200
    assert "submitter-secret@example.test" not in response.text
    assert "hidden@example.test" not in response.text


@pytest.mark.parametrize("client_name", ["client", "user_client"])
def test_preview_requires_staff(request, seeded_data, client_name):
    response = request.getfixturevalue(client_name).get(f"/admin/demo/{seeded_data['pending_demo_id']}/preview")
    assert response.status_code in {302, 401, 403}
    assert "Pending Demonstration" not in response.get_data(as_text=True)


def test_embed_hides_site_chrome_only(admin_client, db, seeded_data):
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    plain = admin_client.get(f"/admin/demo/{demo['_id']}/preview")
    embedded = admin_client.get(f"/admin/demo/{demo['_id']}/preview?embed=1")
    assert_preview(embedded, demo)
    hide = '[data-react-island="site-header"], .public-site-footer'
    assert hide in embedded.text and hide not in plain.text
    embedded_props, plain_props = props_from(embedded), props_from(plain)
    for props in (embedded_props, plain_props):
        props["urls"].pop("login")  # carries the current address
    assert embedded_props == plain_props


def test_unknown_demo_is_404(admin_client):
    assert admin_client.get("/admin/demo/65f0c0ffee0000000000abcd/preview").status_code == 404


def test_public_url_shows_staff_the_same_preview(app, admin_client, db, seeded_data):
    app.config["PUBLIC_DETAIL_REACT_ENABLED"] = True
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    for path in (f"/demonstration/{demo['_id']}?preview=1", "/demonstration/pending-demonstration"):
        assert_preview(admin_client.get(path), demo)


def test_public_url_stays_404_for_visitors(app, client, user_client, db, seeded_data):
    app.config["PUBLIC_DETAIL_REACT_ENABLED"] = True
    # alice is an editor of the fixture; without that she may not see it.
    db.demonstrations.update_one({"_id": seeded_data["pending_demo_id"]}, {"$set": {"editors": []}})
    for visitor in (client, user_client):
        assert visitor.get(f"/demonstration/{seeded_data['pending_demo_id']}?preview=1").status_code == 404


def test_editor_sees_own_pending_demo(app, user_client, db, seeded_data):
    app.config["PUBLIC_DETAIL_REACT_ENABLED"] = True
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert_preview(user_client.get(f"/demonstration/{demo['_id']}?preview=1"), demo)


def test_old_preview_address_redirects(admin_client, seeded_data):
    response = admin_client.get(f"/demonstration/{seeded_data['pending_demo_id']}/some")
    assert response.status_code == 302
    assert response.location.endswith(f"/admin/demo/{seeded_data['pending_demo_id']}/preview")


def test_emailed_preview_link_uses_react_preview(client, db, seeded_data):
    demo = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    response = client.get(f"/admin/demo/preview_demo_with_token/{seeded_data['preview_token']}")
    assert_preview(response, demo)
    # Still single use.
    again = client.get(f"/admin/demo/preview_demo_with_token/{seeded_data['preview_token']}")
    assert again.status_code != 200 or again.headers.get("X-Detail-Renderer") != "react-preview"
