def test_verified_official_contact_can_cancel_demo_immediately(client, db, seeded_data):
    demo_id = seeded_data["demo_id"]
    token = seeded_data["cancel_token"]

    response = client.post(
        f"/cancel_demonstration/{token}",
        data={"reason": "Organizer confirmed cancellation."},
        follow_redirects=False,
    )

    assert response.status_code == 302

    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["cancelled"] is True
    assert demo["cancellation_requested"] is False
    assert demo["cancelled_by"]["official_contact"] is True
    assert demo["cancelled_by"]["source"] == "organizer_link"


def test_public_report_of_cancelled_demo_creates_request_but_does_not_cancel(client, db, seeded_data):
    demo_id = seeded_data["demo_id"]

    response = client.post(
        "/report",
        data={
            "type": "demonstration",
            "demo_id": str(demo_id),
            "error": "The organizer says this demo is cancelled.",
            "mark_cancelled": "1",
            "reporter_email": "observer@example.test",
        },
        headers={"Referer": "/"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    demo = db.demonstrations.find_one({"_id": demo_id})
    assert demo["cancelled"] is False
    assert demo["cancellation_requested"] is True
    assert demo["cancellation_request_source"] == "user_report"
    assert demo["cancellation_requested_by"]["official_contact"] is False
    assert demo["cancellation_requested_by"]["email"] == "observer@example.test"

    case = db.cases.find_one({"demo_id": demo_id, "type": "demo_cancellation_request"})
    assert case is not None


def test_report_get_writes_nothing(client, db):
    before = db.reports.count_documents({})
    response = client.get("/report")
    assert response.status_code == 405
    assert db.reports.count_documents({}) == before


def test_report_form_post_is_validated_and_redirects_only_on_site(client, db, seeded_data):
    before = db.reports.count_documents({})
    empty = client.post("/report", data={"type": "demonstration", "demo_id": str(seeded_data["demo_id"]), "error": " "},
                        headers={"Referer": "https://evil.example/x"})
    assert empty.status_code == 302 and empty.headers["Location"] == "/"
    assert db.reports.count_documents({}) == before
    untyped = client.post("/report", data={"error": "spam"})
    assert untyped.status_code == 302 and db.reports.count_documents({}) == before
    ok = client.post("/report", data={"type": "demonstration", "demo_id": str(seeded_data["demo_id"]), "error": "Wrong time"},
                     headers={"Referer": "http://localhost/demonstration/x?a=1"})
    assert ok.status_code == 302 and ok.headers["Location"] == "/demonstration/x?a=1"
    assert db.reports.count_documents({}) == before + 1


def test_report_from_the_flask_modal_gets_a_status_not_a_redirect(client, db, seeded_data):
    before = db.reports.count_documents({})
    refused = client.post("/report", data={"type": "demonstration", "demo_id": str(seeded_data["demo_id"]), "error": " "},
                          headers={"X-Requested-With": "XMLHttpRequest"})
    assert refused.status_code == 400
    assert db.reports.count_documents({}) == before
