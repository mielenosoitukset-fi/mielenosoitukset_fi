#!/usr/bin/env python3
"""Regenerate the golden support-mail fixtures that the Go API compares against.

The Go contact handler (go-api/contact.go) renders the support mail itself, so its
output must stay byte-identical to Flask's Jinja render of
`emails/customer_support/new_ticket.html`. Run this from the repository root after
changing that template, then commit `go-api/testdata/contact_mail_*`.
"""
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

CASES = {
    "plain": {
        "name": "Testi Käyttäjä",
        "email": "testi@example.test",
        "subject": "Tuki",
        "message": "Hei!\n\nTämä on viesti.\näö 😀",
        "request_id": "0123456789abcdef0123456789abcdef",
    },
    "special": {
        "name": "<b>\"A&B\" 'x'</b>",
        "email": "a+b@example.test",
        "subject": "Muu",
        "message": "<script>alert(1)</script>\n&amp; &lt; \" ' + \t end\r\nline",
        "request_id": "fedcba9876543210fedcba9876543210",
    },
}


def environment():
    # Same settings as EmailSender._env.
    return Environment(
        loader=FileSystemLoader("mielenosoitukset_fi/templates/emails"),
        autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml"), default_for_string=True),
    )


def render(context):
    return environment().get_template("customer_support/new_ticket.html").render(context)


def main():
    target = Path("go-api/testdata")
    target.mkdir(exist_ok=True)
    for name, context in CASES.items():
        (target / f"contact_mail_{name}.html").write_text(render(context), encoding="utf-8", newline="")
    (target / "contact_mail_cases.json").write_text(
        json.dumps(CASES, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
