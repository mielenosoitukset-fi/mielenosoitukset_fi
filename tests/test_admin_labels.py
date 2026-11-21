"""Admin pages show Finnish labels and Helsinki times instead of codes (audit #18)."""

from datetime import datetime

from mielenosoitukset_fi.utils.admin_labels import case_action_label, case_type_label, urgency_label
from mielenosoitukset_fi.utils.classes.RepeatSchedule import RepeatSchedule


def test_label_filters():
    assert case_type_label("new_demo") == "Uusi mielenosoitus"
    assert urgency_label("High") == "Kiireellinen"
    assert case_action_label("auto_close") == "Suljettu automaattisesti"
    assert case_action_label("Tapaus suljettu") == "Tapaus suljettu"
    assert case_action_label("some_code") == "Some code"


def test_repeat_schedule_in_finnish():
    assert RepeatSchedule("weekly", 2, weekday="friday").as_finnish() == "Joka 2. viikko, perjantaisin"
    assert RepeatSchedule("weekly", 1, weekday="monday").as_finnish() == "Joka viikko, maanantaisin"
    assert RepeatSchedule("monthly", 1, monthly_option="nth_weekday", nth_weekday="last",
                          weekday_of_month="saturday").as_finnish() == "Joka kuukausi, kuun viimeinen lauantai"


def test_datetimeformat_shows_helsinki_time(app):
    fmt = app.jinja_env.filters["datetimeformat"]
    assert fmt(datetime(2026, 11, 21, 5, 30)) == "21.11.2026 07:30"
    assert fmt(datetime(2026, 7, 1, 12, 0)) == "01.07.2026 15:00"
    assert fmt("2026-11-21T05:30:00Z") == "21.11.2026 07:30"
    assert fmt("2026-12-21", "%d.%m.%Y") == "21.12.2026"
    assert fmt("eilen") == "eilen"


def test_sidebar_task_is_labelled_and_links_to_case(admin_client, seeded_data):
    page = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert "WAITING_APPROVAL" not in page
    assert ">waiting_approval<" not in page
    assert f"/admin/case/{seeded_data['case_id']}/" in page


def test_command_center_case_type_is_finnish(admin_client, seeded_data):
    page = admin_client.get(f"/admin/demo/command-center/{seeded_data['pending_demo_id']}").get_data(as_text=True)
    assert ">new_demo<" not in page
    assert "Uusi mielenosoitus" in page
