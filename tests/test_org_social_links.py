"""Audit item 13: social links outside the known platforms keep their platform."""
from bson import ObjectId


def _org(db, seeded_data):
    return db.organizations.find_one({"_id": ObjectId(seeded_data["org_id"])})


def test_unknown_platform_is_kept_in_the_form(admin_client, db, seeded_data):
    db.organizations.update_one(
        {"_id": ObjectId(seeded_data["org_id"])},
        {"$set": {"social_media_links": {"bluesky": "https://bsky.app/profile/x"}}},
    )
    body = admin_client.get(f"/admin/organization/edit/{seeded_data['org_id']}").get_data(as_text=True)
    assert 'createSocial("bluesky", "https://bsky.app/profile/x")' in body
    assert "options.push([platform, platform])" in body


def test_save_keeps_platform_and_drops_unsafe_links(admin_client, db, seeded_data):
    org = _org(db, seeded_data)
    response = admin_client.post(
        f"/admin/organization/edit/{seeded_data['org_id']}",
        data={
            "name": org["name"], "email": org.get("email") or "org@example.test",
            "description": org.get("description") or "", "website": org.get("website") or "",
            "social_media_platform[]": ["bluesky", "muu", "facebook"],
            "social_media_url[]": ["https://bsky.app/profile/x", "https://example.test/x", "javascript:alert(1)"],
        },
        headers={"Referer": f"/admin/organization/edit/{seeded_data['org_id']}"},
    )
    assert response.status_code in (200, 302)
    assert _org(db, seeded_data)["social_media_links"] == {
        "bluesky": "https://bsky.app/profile/x", "muu": "https://example.test/x",
    }


def test_view_shows_unknown_platform_by_name_and_skips_unsafe(admin_client, db, seeded_data):
    db.organizations.update_one(
        {"_id": ObjectId(seeded_data["org_id"])},
        {"$set": {"social_media_links": {"bluesky": "https://bsky.app/profile/x", "facebook": "javascript:alert(1)"}}},
    )
    body = admin_client.get(f"/admin/organization/view/{seeded_data['org_id']}").get_data(as_text=True)
    assert "bluesky" in body and "fab fa-bluesky" not in body
    assert "javascript:alert(1)" not in body
