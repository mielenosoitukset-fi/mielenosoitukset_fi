"""The Go welcome mail (go-api/campaign_volunteer.go) must match Flask's template.

The Go test reads the same fixture, so a template change fails both until the
fixture and the Go template are updated together. Regenerate the fixture with
the snippet in docs/go-campaign-volunteers.md.
"""

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


def test_go_welcome_mail_fixtures_match_the_flask_template():
    env = Environment(
        loader=FileSystemLoader(ROOT / "mielenosoitukset_fi/templates/emails"),
        autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml"), default_for_string=True),
    )
    template = env.get_template("kampanja/welcome.html")
    cases = json.loads((ROOT / "go-api/testdata/campaign_volunteer_welcome.json").read_text(encoding="utf-8"))
    assert cases
    for name, case in cases.items():
        rendered = template.render({"name": case["name"], "confirm_link": case["link"]})
        assert rendered == case["html"], name
