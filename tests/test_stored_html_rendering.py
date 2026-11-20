"""Stored rich text and user-chosen names never run as markup in admin views."""

from bson import ObjectId

from mielenosoitukset_fi.utils.content_formatting import sanitize_stored_html


PAYLOAD = '<img src=x onerror="window.__pwn=1"><script>alert(1)</script>'


def test_sanitize_stored_html_keeps_formatting_and_drops_active_content():
    rendered = sanitize_stored_html(
        '<p class="x">Hei <b>kaikki</b><br>'
        '<a href="javascript:alert(1)" onclick="x()">huono</a> '
        '<a href="https://mielenosoitukset.fi">hyvä</a></p>' + PAYLOAD
    )
    assert rendered == (
        '<p>Hei <b>kaikki</b><br><a>huono</a> '
        '<a href="https://mielenosoitukset.fi">hyvä</a></p>'
    )


def test_sanitize_stored_html_cannot_break_out_of_its_container():
    assert sanitize_stored_html("</div></p><a href='https://a.fi'>x") == (
        '<a href="https://a.fi">x</a>'
    )
    assert sanitize_stored_html(None) == ""
    assert sanitize_stored_html("<svg><script>1</script></svg>teksti") == "teksti"


def test_command_center_and_dashboard_do_not_execute_submitted_text(
    db, seeded_data, admin_client
):
    demo_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one(
        {"_id": demo_id},
        {"$set": {"title": "Äidit');window.__pwn=1;//", "description": PAYLOAD}},
    )

    page = admin_client.get(f"/admin/demo/command-center/{demo_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "onerror" not in html
    assert "<script>alert(1)" not in html

    listing = admin_client.get("/admin/demo/?approved=pending").get_data(as_text=True)
    assert "openModal(event, this.dataset.demoTitle" in listing
    assert "openModal(event, 'Äidit" not in listing


def test_support_ticket_html_is_sanitized(db, seeded_data, admin_client):
    case = db.cases.find_one({"_id": seeded_data["case_id"]})
    case["_id"] = ObjectId()
    case["type"] = case["case_type"] = "support_ticket"
    case.pop("demo_id", None)
    case["running_num"] = (case.get("running_num") or 0) + 500
    case["suggestion"] = {
        "message": "Hei",
        "html_message": "<p>Hei <strong>tuki</strong></p>" + PAYLOAD,
        "subject": "Apua",
    }
    db.cases.insert_one(case)

    html = admin_client.get(f"/admin/case/{case['_id']}/").get_data(as_text=True)
    assert "<p>Hei <strong>tuki</strong></p>" in html
    assert "onerror" not in html
    assert "<script>alert(1)" not in html


def test_user_table_passes_names_as_data(db, seeded_data, admin_client):
    db.users.update_one(
        {"_id": seeded_data["friend_id"]},
        {"$set": {"displayname": "Bob');window.__pwn=1;//"}},
    )
    html = admin_client.get("/admin/user/").get_data(as_text=True)
    assert "openDeleteModal(event, this.dataset.userName" in html
    assert "openDeleteModal(event, 'Bob" not in html
