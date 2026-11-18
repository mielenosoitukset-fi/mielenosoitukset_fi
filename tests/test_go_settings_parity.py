"""Fixture that pins the Go general settings write to the Flask one.

``go-api/testdata/settings_parity.json`` is produced by the real Flask handler
``settings_api_v2`` (``POST /users/auth/api/v2/settings``) and the real
``auth/settings_changed.html`` template. ``go-api/account_settings_settings_test.go``
replays the same inputs against the Go handler. If Flask behaviour changes this
test fails until the fixture is regenerated and the Go port is brought along:

    UPDATE_GO_FIXTURES=1 ../../../.venv/bin/python -m pytest tests/test_go_settings_parity.py

Flask walks ``allowed_fields``, a set, so the order of the changed fields in
the mail changes between processes. The Go port uses one fixed order (city,
display_name, language, dark_mode); the fixture renders Flask's template in that
order so the comparison is deterministic.
"""

import inspect
import json
import os
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

FIXTURE = Path(__file__).resolve().parent.parent / "go-api" / "testdata" / "settings_parity.json"
EMAIL_DIR = Path(__file__).resolve().parent.parent / "mielenosoitukset_fi" / "templates" / "emails"
ORDER = ["city", "display_name", "language", "dark_mode"]

FULL = {"city": "Helsinki", "display_name": "Alice", "language": "fi", "dark_mode": True, "subscribe_newsletter": None}

SCENARIOS = [
    {"name": "no settings document, full body", "displayname": "Alice Tester", "settings": None,
     "body": {"display_name": "Uusi nimi", "city": "Helsinki", "language": "fi", "dark_mode": False}},
    {"name": "no settings document, empty body", "displayname": "Alice Tester", "settings": None, "body": {}},
    {"name": "city only", "displayname": "Alice Tester", "settings": FULL, "body": {"city": "Espoo"}},
    {"name": "nothing changed", "displayname": "Alice Tester", "settings": FULL,
     "body": {"display_name": "Alice", "city": "Helsinki", "language": "fi", "dark_mode": True}},
    {"name": "html special characters", "displayname": "A&B <x> \"q\" 'z'", "settings": FULL,
     "body": {"display_name": "<b>Ben & \"Jerry's\"</b>"}},
    {"name": "dark mode on and off", "displayname": "Alice Tester", "settings": {**FULL, "dark_mode": False}, "body": {"dark_mode": True}},
    {"name": "dark mode from none", "displayname": "Alice Tester", "settings": {**FULL, "dark_mode": None}, "body": {"dark_mode": False}},
    {"name": "keys missing from the document", "displayname": "Alice Tester", "settings": {"display_name": "Alice"},
     "body": {"city": "Tampere", "language": "fi", "dark_mode": True}},
    {"name": "empty city", "displayname": "Alice Tester", "settings": FULL, "body": {"city": ""}},
    {"name": "unicode", "displayname": "Ääkkönen", "settings": {**FULL, "display_name": "Öljy"}, "body": {"display_name": "Åsa Ünal 💖"}},
    {"name": "reserved name", "displayname": "Alice Tester", "settings": FULL, "body": {"display_name": "Admin"}},
    {"name": "reserved name with at sign and spaces", "displayname": "Alice Tester", "settings": FULL, "body": {"display_name": " @Administrator "}},
    {"name": "reserved name upper case", "displayname": "Alice Tester", "settings": FULL, "body": {"display_name": "ADMIN"}},
    {"name": "reserved name at sign and space", "displayname": "Alice Tester", "settings": FULL, "body": {"display_name": "@ Admin"}},
    {"name": "reserved name long s", "displayname": "Alice Tester", "settings": FULL, "body": {"display_name": "adminiſtrator"}},
    {"name": "fullwidth admin is not reserved", "displayname": "Alice Tester", "settings": FULL, "body": {"display_name": "Ａｄｍｉｎ"}},
    {"name": "all four change", "displayname": "Alice Tester", "settings": {**FULL, "language": None},
     "body": {"display_name": "Neljä", "city": "Oulu", "language": "fi", "dark_mode": False}},
]


def _env():
    return Environment(
        loader=FileSystemLoader(str(EMAIL_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml"), default_for_string=True),
    )


@pytest.fixture
def captured_mail(monkeypatch, user_client):
    calls = []

    def capture(template_name, subject, recipients, context, **kwargs):
        calls.append({"template": template_name, "subject": subject, "recipients": recipients, "context": context})

    # Patch the sender the handler itself resolves, through its own globals.
    app = user_client.application
    rule = next(item for item in app.url_map.iter_rules() if item.rule == "/users/auth/api/v2/settings")
    handler = inspect.unwrap(app.view_functions[rule.endpoint])
    handler_globals = handler.__globals__
    monkeypatch.setattr(handler_globals["email_sender"], "queue_email", capture, raising=False)
    return calls


def _generate(user_client, db, seeded_data, captured_mail):
    env = _env()
    user_id = seeded_data["user_id"]
    email = db.users.find_one({"_id": user_id})["email"]
    cases = []
    for scenario in SCENARIOS:
        db.users.update_one({"_id": user_id}, {"$set": {"displayname": scenario["displayname"]}})
        db.user_settings.delete_many({"user_id": user_id})
        if scenario["settings"] is not None:
            db.user_settings.insert_one({"user_id": user_id, **scenario["settings"]})
        captured_mail.clear()
        response = user_client.post("/users/auth/api/v2/settings", json=scenario["body"])
        answer = response.get_json()
        stored = db.user_settings.find_one({"user_id": user_id})
        entry = {
            "name": scenario["name"],
            "email": email,
            "displayname": scenario["displayname"],
            "settings": scenario["settings"],
            "body": scenario["body"],
            "status": response.status_code,
            "message": answer["message"],
            "changed_fields": answer.get("changed_fields", {}),
            "mail": None,
        }
        if answer.get("changed_fields"):
            assert len(captured_mail) == 1
            mail = captured_mail[0]
            assert mail["template"] == "auth/settings_changed.html"
            assert mail["recipients"] == [email]
            context = dict(mail["context"])
            context["changed_fields"] = {name: context["changed_fields"][name] for name in ORDER if name in context["changed_fields"]}
            entry["mail"] = {"subject": mail["subject"], "html": env.get_template(mail["template"]).render(context)}
            # Key order follows Flask's set iteration, so sort it.
            entry["settings_after"] = {key: stored[key] for key in sorted(stored) if key not in ("_id", "user_id")}
            entry["displayname_after"] = db.users.find_one({"_id": user_id})["displayname"]
        else:
            assert not captured_mail
        cases.append(entry)
    return {"cases": cases}


def test_go_settings_fixture_matches_flask(user_client, db, seeded_data, captured_mail):
    generated = _generate(user_client, db, seeded_data, captured_mail)
    text = json.dumps(generated, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if os.environ.get("UPDATE_GO_FIXTURES") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, "regenerate with UPDATE_GO_FIXTURES=1 and update the Go port"
