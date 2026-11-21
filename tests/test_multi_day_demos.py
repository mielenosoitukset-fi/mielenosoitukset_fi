"""DATA-07: multi-day demonstrations store an optional end_date."""
from datetime import date, datetime, timezone

import pytest

from mielenosoitukset_fi.utils.multi_day import MAX_DEMO_DAYS, demo_last_day, normalize_end_date


@pytest.mark.parametrize("end", [None, "", "  ", "2026-12-01"])
def test_empty_or_same_day_end_is_one_day(app, end):
    with app.test_request_context():
        assert normalize_end_date("2026-12-01", end) is None


def test_valid_end_date_is_kept(app):
    with app.test_request_context():
        assert normalize_end_date("2026-12-01", " 2026-12-03 ") == "2026-12-03"
        assert normalize_end_date("2026-12-01", "2026-12-14") == "2026-12-14"


@pytest.mark.parametrize("end", ["2026-11-30", "2026-12-15", "3.12.2026", "2026-02-30", "x"])
def test_invalid_end_dates_are_refused(app, end):
    with app.test_request_context(), pytest.raises(ValueError):
        normalize_end_date("2026-12-01", end)


def test_max_length_is_fourteen_days():
    assert MAX_DEMO_DAYS == 14


def test_last_day():
    assert demo_last_day({"date": "2026-12-01"}) == "2026-12-01"
    assert demo_last_day({"date": "2026-12-01", "end_date": None}) == "2026-12-01"
    assert demo_last_day({"date": "2026-12-01", "end_date": "2026-11-01"}) == "2026-12-01"
    assert demo_last_day({"date": "2026-12-01", "end_date": "2026-12-03"}) == "2026-12-03"


def test_admin_edit_saves_and_clears_end_date(admin_client, db, seeded_data):
    demo_id = seeded_data["demo_id"]
    before = db.demonstrations.find_one({"_id": demo_id})
    form = {"title": before["title"], "date": "2099-06-01", "city": before["city"]}

    response = admin_client.post(f"/admin/demo/edit_demo/{demo_id}", data={**form, "end_date": "2099-06-03"})
    assert response.status_code == 302
    assert db.demonstrations.find_one({"_id": demo_id})["end_date"] == "2099-06-03"

    page = admin_client.get(f"/admin/demo/edit_demo/{demo_id}").get_data(as_text=True)
    assert 'name="end_date"' in page and 'value="2099-06-03"' in page

    admin_client.post(f"/admin/demo/edit_demo/{demo_id}", data={**form, "end_date": ""})
    assert db.demonstrations.find_one({"_id": demo_id}).get("end_date") is None


def test_admin_edit_refuses_too_long_demo(admin_client, db, seeded_data):
    demo_id = seeded_data["demo_id"]
    before = db.demonstrations.find_one({"_id": demo_id})
    admin_client.post(f"/admin/demo/edit_demo/{demo_id}", data={
        "title": before["title"], "date": "2099-06-01", "city": before["city"], "end_date": "2099-07-01",
    })
    assert db.demonstrations.find_one({"_id": demo_id}).get("end_date") in (None, before.get("end_date"))


def test_multi_day_demo_is_past_only_after_last_day():
    from mielenosoitukset_fi.scripts import in_past
    from mielenosoitukset_fi.utils.demo_seo import occurrence_end_at

    demo = {"date": "2026-12-01", "end_date": "2026-12-03", "end_time": "18:00"}
    assert in_past.demo_last_day(demo) == "2026-12-03"
    assert in_past.is_future_demo(date.fromisoformat(in_past.demo_last_day(demo)), date(2026, 12, 2))
    assert occurrence_end_at(demo, timezone.utc) == datetime(2026, 12, 3, 18, 0, tzinfo=timezone.utc)
    assert occurrence_end_at({"date": "2026-12-01", "end_time": "18:00"}, timezone.utc).day == 1


def test_ics_ends_on_last_day():
    from mielenosoitukset_fi.scripts.send_demo_reminders import generate_ical_event

    multi = generate_ical_event("Leiri", "2026-12-01", "10:00", "Helsinki", "Tori", end_date="2026-12-03", end_time="18:00")
    assert "DTSTART:20261201T100000" in multi and "DTEND:20261203T180000" in multi
    one = generate_ical_event("Marssi", "2026-12-01", "10:00", "Helsinki", "Tori", end_date=None, end_time="18:00")
    assert "DTEND:20261201T120000" in one


def test_ics_download_uses_end_date(app, db, seeded_data):
    db.demonstrations.update_one({"_id": seeded_data["demo_id"]}, {"$set": {"end_date": "2099-06-03", "date": "2099-06-01", "start_time": "10:00", "end_time": "18:00", "approved": True}})
    body = app.test_client().get(f"/demonstration/{seeded_data['demo_id']}/ics").get_data(as_text=True)
    assert "DTEND:20990603T180000" in body
