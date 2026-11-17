"""Fixtures that pin the Go suggestion handlers to the Flask ones.

``go-api/testdata/suggestion_parity.json`` is produced by the real Flask code
(normalisers, Markdown conversion, the two POST handlers, the two e-mail
templates). The Go tests in ``go-api/suggestion_parity_test.go`` replay the same
inputs and must produce the same outputs. If Flask behaviour changes this test
fails until the fixture is regenerated and the Go port is brought along:

    UPDATE_GO_FIXTURES=1 python -m pytest tests/test_go_suggestion_parity.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from bson import ObjectId
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mielenosoitukset_fi import basic_routes
from mielenosoitukset_fi.utils.content_formatting import html_to_markdown, markdown_to_html

FIXTURE = Path(__file__).resolve().parent.parent / "go-api" / "testdata" / "suggestion_parity.json"
EMAIL_DIR = Path(__file__).resolve().parent.parent / "mielenosoitukset_fi" / "templates" / "emails"

FIXED_TIME = datetime(2026, 11, 17, 10, 20, 30)
FIXED_IP = "203.0.113.7"
FIXED_AGENT = "Mozilla/5.0 (X11; Linux x86_64) <test> & \"quoted\" 'agent'"

MARKDOWN_CASES = [
    "Plain text", "Line one\nLine two", "Para one\n\nPara two", "**bold** and *em*", "__bold__ _em_",
    "***both***", "___both___", "# H1\n\ntext", "## H2 ##", "###### H6", "####### seven", "#NoSpace",
    "Title\n=====\n\ntext", "Sub\n---", "- a\n- b", "* a\n* b", "+ a\n+ b", "1. a\n2. b", "1) a",
    "- a\n\n- b", "- a\n  - b\n  - c\n- d", "- a\n    - b", "1. a\n   1. b", "- a\n1. b", "> quote",
    "> a\n> b", "---", "***", "___", "- - -", "[link](https://example.test)",
    "[link](https://example.test 'title')", "[x](javascript:alert(1))", "[x](mailto:a@b.fi)",
    "[x](/relative)", "[x][ref]\n\n[ref]: https://x.fi", "[ref]\n\n[ref]: https://x.fi", "<b>html</b>",
    "<script>alert(1)</script>", "a & b", "quote \"x\" 'y'", "`code`", "``a`b``", "    indented code",
    "para\n\n    code line\n    second", "```\nfence\n```", "![img](x.png)", "https://auto.link/plain",
    "<https://auto>", "\\*not em\\*", "snake_case_word", "2 * 3 * 4", "a\n\n\n\nb", "  leading spaces",
    "Ääkköset 💖", "line  \nbreak", "- item with **bold**", "1. one\n\n   para in item\n2. two",
    "Text\n- list right after text", "Text\n\n- list after blank", "&amp; entity &lt;", "&copy; &#169;",
    "a * b * c", "*a\n\n*b", "[a](http://x.fi/a_b_c)", "[a](<http://x.fi>)", "[a](http://x.fi/(x))",
    "**unclosed", "*", "_", "- ", "1.", "> ", "# ", "**a**b", "a**b**c", "_a_b", "a_b_",
    "**a *b* c**", "`<b> & x`", "``` `x` ```", "\\\\`code`", "a\\\nb", "Tab\there", "\tindented tab",
    "- a\n\n    para\n- b", "1. a\n\n    - nested\n2. b", "- a\n    - b\n    - c\n- d",
    "line1\r\nline2", "# Heading\ntext right after", "text\n# heading", "* * *\ntext", "a\n***\nb",
    "[a]\n\n[a]: <https://x.fi> (title)", "[a](https://x.fi) and [b](https://y.fi)", "[[nested]](https://x.fi)",
    "*em with `code` inside*", "**bold with [link](https://x.fi)**", "[**bold link**](https://x.fi)",
    "Päivämäärä: **12.11.** klo *18*", "- a\n- b\n\ntext\n\n1. c\n2. d", "x\n\n\n- y\n\n\n- z",
    "___", "_ _", "* a *", "word*star*word", "word_under_word", "__strong__word", "*a*b*c*",
    "**a**b**c**", "*a **b***", "***a** b*", "**a *b***", "**~~x~~**", "tags #one #two", "\\# not heading",
    "1986\\. year", "+ plus", "- [ ] task", "| a | b |\n|---|---|\n| 1 | 2 |",
]

HTML_CASES = [
    "", "  Tavallinen teksti  ", "<p>Rivi<br/>Toinen</p>", "<p>Ensimmainen kappale.</p><p><strong>Tarkea</strong> lisatieto.</p>",
    "<blockquote>Pidetaan suunta rauhallisena.</blockquote><p>Nykyinen kuvaus.</p>",
    "<h2>Otsikko</h2><p><b>Lihava</b> ja <i>kursiivi</i><br>rivi &amp; &lt;teksti&gt; &#228;.</p>",
    "<ol><li>Yksi</li><li>Kaksi<ul><li>Sisalla</li></ul></li></ol>",
    '<p><a href="https://example.test/?a=1&amp;b=2">Linkki</a> <a href="mailto:a@example.test">Viesti</a> '
    '<a href="javascript:alert(1)">Ei</a> <a href="/relative">Suhteellinen</a></p>',
]

TAG_CASES = ["a, b", "#x, #y ,, z", " # spaced , ,#", "same, same", "ääkkönen, #Öljy", "none", "NULL", ""]
ROUTE_CASES = [
    "A, B", "A\nB", "A, B\nC", "['A', 'B']", '["A", "B C"]', "[1, 2]", "['A', None, True]", "[]", "['A' 'B']",
    "[(1, 2)]", "['it\\'s']", "A,,B", "  ", "[\"a\\nb\"]", "['a', ['b']]", "['a',]", "[ 'x' ,'y' ]",
]
ORGANIZER_CASES = [
    "Järjestö Ry", "Järjestö Ry (info@x.fi, https://x.fi)", "A (a@x.fi), B (b@y.fi, www.y.fi)",
    "A <a@x.fi>", "A\nB (b@y.fi)\n  \nC", "A, B, C", "Name (a@x.fi; https://x.fi)", "Name (https://x.fi)",
    "a@x.fi", "https://x.fi", "Name a@x.fi https://x.fi", "Name (", "(only)", "Name ()", "  ", "A  B   C",
    "A (x/y)", "A (b@x.fi, c@y.fi)", "A (www.x.fi, d@x.fi)",
]
CLOCK_PAIRS = [
    ["12:00", "12:00:00"], ["9:05", "09:05"], ["12:00", "12:01"], ["24:00", "24:00"], ["12:60", "12:60"],
    ["12:00:61", "12:00:61"], ["noon", "noon"], ["12:00:00", "12:00"], ["1:2", "01:02"], ["", "12:00"],
]


def _demo_base(**overrides):
    base = {
        "title": "Ilmastomarssi",
        "date": "2026-12-01",
        "start_time": "12:00",
        "end_time": "14:00:00",
        "city": "Helsinki",
        "address": "Mannerheimintie 1",
        "facebook": "https://facebook.example/e/1",
        "tags": ["ilmasto", "rauha"],
        "route": ["Keskustori", "Hämeenkatu"],
        "organizers": [{"name": "Järjestö Ry", "email": "info@x.fi", "website": "https://x.fi", "organization_id": "abc"}],
        "description": "<p>Alkuperäinen <strong>kuvaus</strong>.</p>",
    }
    base.update(overrides)
    return base


DEMO_SCENARIOS = [
    {"name": "comment only", "demo": _demo_base(), "form": {"reporter_comment": "Vain kommentti."}},
    {"name": "empty", "demo": _demo_base(), "form": {}},
    {"name": "whitespace only", "demo": _demo_base(), "form": {"title": "  ", "reporter_comment": "   "}},
    {"name": "same values", "demo": _demo_base(), "form": {"title": "Ilmastomarssi", "city": "Helsinki", "tags": "ilmasto, rauha", "route": "Keskustori, Hämeenkatu", "start_time": "12:00:00", "end_time": "14:00", "organizers": "Järjestö Ry (info@x.fi, https://x.fi)", "reporter_comment": "x"}},
    {"name": "scalar changes", "demo": _demo_base(), "form": {"title": " Uusi otsikko ", "date": "2026-12-02", "start_time": "13:30", "end_time": "15:00", "city": "Espoo", "address": "Uusi 5", "facebook": "https://f.example/2"}},
    {"name": "none and null ignored", "demo": _demo_base(), "form": {"title": "None", "city": "NULL", "address": "null", "reporter_comment": "c"}},
    {"name": "tags and route", "demo": _demo_base(), "form": {"tags": "#ilmasto, uusi,, #rauha", "route": "A\nB, C"}},
    {"name": "route literal", "demo": _demo_base(route="['Vanha', 'Reitti']"), "form": {"route": "['Vanha', 'Uusi']"}},
    {"name": "organizers changed", "demo": _demo_base(), "form": {"organizers": "Uusi Ry (a@b.fi, www.uusi.fi)\nToinen"}},
    {"name": "organizers metadata only", "demo": _demo_base(), "form": {"organizers": "Järjestö Ry (info@x.fi, https://x.fi)", "reporter_comment": "same"}},
    {"name": "description changed", "demo": _demo_base(), "form": {"description_markdown": "Uusi **kuvaus**\n\n- yksi\n- kaksi"}},
    {"name": "description same", "demo": _demo_base(), "form": {"description_markdown": "Alkuperäinen **kuvaus**.", "reporter_comment": "x"}},
    {"name": "description lossy", "demo": _demo_base(description="<blockquote>Rauhallisesti.</blockquote><p>Nykyinen.</p>"), "form": {"description_markdown": "Rauhallisesti.\n\nNykyinen.", "address": "Uusi 5"}},
    {"name": "description html escaped", "demo": _demo_base(), "form": {"description_markdown": "<script>x</script> & [a](javascript:1) [b](https://x.fi)"}},
    {"name": "missing demo fields", "demo": {"title": "Vain otsikko"}, "form": {"title": "Toinen", "tags": "a", "route": "b", "organizers": "c", "start_time": "10:00"}},
    {"name": "reporter details", "demo": _demo_base(), "form": {"reporter_email": " me@example.test ", "reporter_comment": " kommentti <b>&</b> ", "city": "Tampere"}},
    {"name": "null stored values", "demo": _demo_base(date=None, city=None, tags=None, route=None, organizers=None), "form": {"city": "Tampere", "tags": "x", "route": "y", "organizers": "z"}},
]

ORG_SCENARIOS = [
    {"name": "all fields", "org": {"name": "Esimerkki Ry"}, "form": [["name", " Uusi nimi "], ["description", "Kuvaus <b>&</b>"], ["website", "https://x.fi"], ["email", " a@b.fi "], ["logo", "https://x.fi/l.png"], ["social_platform[]", "instagram"], ["social_url[]", "https://i.example/x"], ["social_platform[]", "facebook"], ["social_url[]", "https://f.example/x"]]},
    {"name": "empty", "org": {"name": "Esimerkki Ry"}, "form": []},
    {"name": "blank values", "org": {"name": "Esimerkki Ry"}, "form": [["name", "   "], ["description", ""], ["social_platform[]", " "], ["social_url[]", "x"]]},
    {"name": "social pairing", "org": {"name": "Esimerkki Ry"}, "form": [["social_platform[]", "a"], ["social_platform[]", "b"], ["social_platform[]", "c"], ["social_url[]", "1"], ["social_url[]", ""]]},
    {"name": "duplicate platform", "org": {"name": "Esimerkki Ry"}, "form": [["social_platform[]", "x"], ["social_url[]", "1"], ["social_platform[]", "x"], ["social_url[]", "2"]]},
    {"name": "repeated scalar", "org": {"name": "Esimerkki Ry"}, "form": [["name", "first"], ["name", "second"], ["email", "x@y.fi"]]},
    {"name": "unicode and quotes", "org": {"name": "Ääkkönen & \"Pojat\" 'Ry'"}, "form": [["name", "Öljy"], ["description", "it's \"quoted\" + plus"], ["social_platform[]", "x'y"], ["social_url[]", "https://x.fi/?a=1&b=2"]]},
]


def _repr_free(value):
    """JSON-safe copy of a BSON value (ObjectId and datetime become strings)."""
    if isinstance(value, ObjectId):
        return {"$oid": str(value)}
    if isinstance(value, datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        return {key: _repr_free(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_repr_free(item) for item in value]
    return value


def _email_env():
    return Environment(
        loader=FileSystemLoader(str(EMAIL_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml"), default_for_string=True),
    )


def _post_data(form):
    if isinstance(form, dict):
        return form
    grouped = {}
    for key, value in form:
        grouped.setdefault(key, []).append(value)
    return grouped


@pytest.fixture
def captured_mail(monkeypatch, client):
    calls = []

    def capture(template_name, subject, recipients, context, **kwargs):
        calls.append({"template": template_name, "subject": subject, "recipients": recipients, "context": context, "kwargs": kwargs})

    # Other tests reload basic_routes, so the module object that registered the
    # routes may not be the one in sys.modules. Patch the sender the handlers
    # themselves resolve, through the view function's own globals.
    handler_globals = client.application.view_functions["suggest_change"].__globals__
    monkeypatch.setattr(handler_globals["email_sender"], "queue_email", capture, raising=False)
    return calls


def _generate(client, db, seeded_data, captured_mail):
    env = _email_env()
    fixture = {
        "markdown": [{"input": text, "html": markdown_to_html(text)} for text in MARKDOWN_CASES],
        "html_to_markdown": [{"input": text, "markdown": html_to_markdown(text)} for text in HTML_CASES],
        "tags": [{"input": text, "output": basic_routes._normalize_tag_list(text.split(",")) if text.strip().lower() not in {"none", "null"} else []} for text in TAG_CASES],
        "route": [{"input": text, "output": basic_routes._normalize_route_points(text)} for text in ROUTE_CASES],
        "organizers": [{"input": text, "output": basic_routes._normalize_organizers(text)} for text in ORGANIZER_CASES],
        "clock": [{"a": a, "b": b, "same": bool((basic_routes._parse_clock(a) or a) == (basic_routes._parse_clock(b) or b))} for a, b in CLOCK_PAIRS],
        "demo_scenarios": [],
        "org_scenarios": [],
    }

    demo_id = seeded_data["demo_id"]
    for scenario in DEMO_SCENARIOS:
        db.demonstrations.replace_one({"_id": demo_id}, {"_id": demo_id, **scenario["demo"]})
        db.demo_suggestions.delete_many({})
        captured_mail.clear()
        response = client.post(
            f"/suggest_change/{demo_id}",
            data=scenario["form"],
            headers={"User-Agent": FIXED_AGENT},
            follow_redirects=False,
        )
        stored = db.demo_suggestions.find_one({})
        entry = {"name": scenario["name"], "demo": scenario["demo"], "form": scenario["form"], "status": response.status_code}
        if stored is None:
            entry["outcome"] = "rejected"
            entry["location_is_request_url"] = response.headers["Location"].endswith(f"/suggest_change/{demo_id}")
            assert not captured_mail
        else:
            entry["outcome"] = "stored"
            entry["location"] = response.headers["Location"].replace(f"/{demo_id}", "/{demo_id}")
            entry["suggested_fields"] = _repr_free(stored["suggested_fields"])
            entry["original_values"] = _repr_free(stored["original_values"])
            entry["reporter_comment"] = stored["reporter_comment"]
            entry["reporter_email"] = stored["reporter_email"]
            entry["status_value"] = stored["status"]
            entry["keys"] = sorted(stored.keys())
            assert len(captured_mail) == 1
            mail = captured_mail[0]
            assert mail["template"] == "admin_suggestion_notification.html"
            assert mail["recipients"] == ["tuki@mielenosoitukset.fi"]
            context = dict(mail["context"])
            document = dict(context["suggestion_doc"])
            document.update(created_at=FIXED_TIME, ip=FIXED_IP, user_agent=FIXED_AGENT)
            context["suggestion_doc"] = document
            demo_doc = db.demonstrations.find_one({"_id": demo_id})
            context["demo"] = demo_doc
            entry["mail_subject"] = mail["subject"]
            entry["mail_html"] = env.get_template(mail["template"]).render(context)
        fixture["demo_scenarios"].append(entry)

    org_id = seeded_data["org_id"]
    for scenario in ORG_SCENARIOS:
        db.organizations.update_one({"_id": org_id}, {"$set": scenario["org"]})
        db.org_edit_suggestions.delete_many({"organization_id": org_id})
        before = {item["_id"] for item in db.cases.find({}, {"_id": 1})}
        captured_mail.clear()
        response = client.post(
            f"/organization/{org_id}/save_suggestion",
            data=_post_data(scenario["form"]),
            headers={"User-Agent": FIXED_AGENT},
            follow_redirects=False,
        )
        assert response.status_code == 302
        stored = db.org_edit_suggestions.find_one({"organization_id": org_id})
        cases = [item for item in db.cases.find({}) if item["_id"] not in before]
        assert len(cases) == 1 and len(captured_mail) == 1
        case = cases[0]
        mail = captured_mail[0]
        assert mail["template"] == "admin_org_suggestion_notification.html"
        assert mail["context"]["review_link"].endswith(f"/admin/organization/{org_id}/suggestion/{stored['_id']}")
        context = dict(mail["context"])
        # The Go side builds its own absolute link from the configured origin.
        context.update(review_link="https://mielenosoitukset.fi/admin/organization/ORG/suggestion/SUGGESTION", submitted_at="2026-11-17 10:20 UTC", ip=FIXED_IP, user_agent=FIXED_AGENT)
        assert context["fields"] == stored["fields"]
        assert case["suggestion"]["_id"] == stored["_id"]
        fixture["org_scenarios"].append({
            "name": scenario["name"],
            "org": scenario["org"],
            "form": scenario["form"],
            "status": response.status_code,
            "location": response.headers["Location"].replace(str(org_id), "{org}"),
            "fields": [[key, _repr_free(value)] for key, value in stored["fields"].items()],
            "meta_submitter_email": stored["meta"]["submitter_email"],
            "suggestion_keys": sorted(stored.keys()),
            "suggestion_status": _repr_free({k: (None if k == "updated_at" else v) for k, v in stored["status"].items()}),
            "audit_actions": [item["action"] for item in stored["audit_log"]],
            "audit_user": stored["audit_log"][0]["user"],
            "case_keys": sorted(case.keys()),
            "case_type": case["type"],
            "case_demo_id": case["demo_id"],
            "case_organization_id": case["organization_id"],
            "case_submitter_id": case["submitter_id"],
            "case_submitter": case["submitter"],
            "case_error_report": case["error_report"],
            "case_meta": case["meta"],
            "case_action_logs": case["action_logs"],
            "case_case_history": case["case_history"],
            "case_suggestion_keys": sorted(case["suggestion"].keys()),
            "mail_subject": mail["subject"],
            "mail_recipients": mail["recipients"],
            "mail_html": env.get_template(mail["template"]).render(context),
        })
    return fixture


def test_go_suggestion_fixture_matches_flask(client, db, seeded_data, captured_mail):
    generated = _generate(client, db, seeded_data, captured_mail)
    text = json.dumps(generated, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if os.environ.get("UPDATE_GO_FIXTURES") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, "regenerate with UPDATE_GO_FIXTURES=1 and update the Go port"
