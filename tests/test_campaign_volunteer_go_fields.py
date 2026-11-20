"""Flask and Go clean and validate volunteer signups the same way.

go-api/testdata/campaign_volunteer_fields.json lists inputs with the result
Flask's _volunteer_fields() gives. This test pins the file to Flask, and
TestVolunteerFieldsMatchFlaskFixture in go-api/campaign_volunteer_test.go runs
the same cases through the Go parser, so a rule changed on one side fails here
or there until both agree.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = {
    "required": "Nimi ja sähköpostiosoite ovat pakollisia.",
    "format": "Tarkista korostetut tiedot ja yritä uudelleen.",
}


def test_go_field_fixture_matches_flask(app):
    from mielenosoitukset_fi.kampanja.routes import _volunteer_fields

    cases = json.loads((ROOT / "go-api/testdata/campaign_volunteer_fields.json").read_text(encoding="utf-8"))
    assert len(cases) >= 30
    with app.test_request_context():
        for name, case in cases.items():
            fields, error = _volunteer_fields(case["data"])
            if "error" in case:
                assert fields is None and str(error) == MESSAGES[case["error"]], name
            else:
                assert error is None and fields == case["fields"], name
