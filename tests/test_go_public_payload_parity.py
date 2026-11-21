"""Pin the admin preview payload (Python) to the public Go API payload.

``go-api/testdata/public_payload_parity.json`` is written from
``mielenosoitukset_fi.utils.public_payload``. ``go-api/public_payload_parity_test.go``
feeds the same stored documents to Go's ``publicPayload`` and expects the same
JSON, so the admin preview and the public page cannot drift apart. Regenerate
after an intended change, then bring the other side along:

    UPDATE_GO_FIXTURES=1 python -m pytest tests/test_go_public_payload_parity.py
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bson import Int64, ObjectId, json_util

from mielenosoitukset_fi.utils.public_payload import public_payload

FIXTURE = Path(__file__).resolve().parent.parent / "go-api" / "testdata" / "public_payload_parity.json"
OID = ObjectId("65f0c0ffee0000000000abcd")
ORG = ObjectId("65f0c0ffee0000000000beef")
PARENT = ObjectId("65f0c0ffee0000000000cafe")

BASE = {
    "_id": OID, "title": "Mielenosoitus", "description": "<p>Kuvaus</p>", "date": "2026-12-01",
    "start_time": "17:00", "end_time": None, "city": "Helsinki", "address": "Senaatintori",
    "slug": "mielenosoitus", "running_number": 42, "tags": ["ilmasto", " rauha ", "", 5],
    "approved": False, "rejected": False, "hide": False, "cancelled": False,
    "latitude": 60.17, "longitude": "24.94", "organizers": [],
    "submitter_email": "private@example.test",
}

CASES = [
    ({}, ""),
    ({}, "fi"),
    ({"end_date": "2026-12-03"}, ""),
    ({"default_language": "en", "translations": {"fi": {"title": " Suomeksi ", "tags": ["a"]},
                                                 "sv": {"description": "  "}, "en": {"title": "English"}}}, ""),
    ({"default_language": "xx", "translations": {"en": {"title": "English", "description": "Body"}}}, "en"),
    ({"translations": {"sv": {"tags": [" ", "taggen"]}, "en": "bad"}}, "sv"),
    ({"date": datetime(2026, 12, 1, 22, 30)}, ""),
    ({"date": datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc)}, ""),
    ({"date": 20261201, "start_time": 17, "city": None}, ""),
    ({"cancelled": True, "likes": 7, "parent": PARENT}, ""),
    ({"cancelled": "yes", "likes": -1, "parent": str(PARENT).upper()}, ""),
    ({"likes": 2.5, "parent": "not-an-id"}, ""),
    ({"likes": Int64(2**31), "running_number": "17"}, ""),
    ({"likes": "12", "running_number": True}, ""),
    ({"latitude": "nan", "longitude": "1e400"}, ""),
    ({"latitude": " 60.1", "longitude": "0x1p-2"}, ""),
    ({"latitude": "1_0", "longitude": "-.5e1"}, ""),
    ({"latitude": "1__0", "longitude": "_1"}, ""),
    ({"latitude": "1_", "longitude": "\u0663"}, ""),
    ({"latitude": "0x_1.8p1", "longitude": "1e+0_1"}, ""),
    ({"latitude": "Infinity", "longitude": "5."}, ""),
    ({"route": ["A", " B ", "", 3]}, ""),
    ({"route": '["Rautatientori", " Senaatintori "]'}, ""),
    ({"route": "['Kauppatori', \"Esplanadi\", 'It\\'s here']"}, ""),
    ({"route": "['bad' 'list']"}, ""),
    ({"route": "Kaisaniemi, Kallio\nSörnäinen,,"}, ""),
    ({"route": "null"}, ""),
    ({"route": "[1, 2]"}, ""),
    ({"route": "['\\x41\\u00e4\\101']"}, ""),
    ({"route": "['\\q']"}, ""),
    ({"gallery_images": ["a.jpg\n b.jpg ", ["c.jpg", "a.jpg"], 5], "images": ["ignored.jpg"]}, ""),
    ({"gallery_images": [], "images": "x.jpg\n\ny.jpg"}, ""),
    ({"img": "https://cdn.example.test/i.jpg", "cover_picture": "c.jpg", "cover_source": "auto",
      "preview_image": "p.png", "facebook": "https://facebook.com/events/1", "event_type": "march"}, ""),
    ({"organizers": [
        {"name": "Org", "organization_id": ORG, "website": "https://org.test", "logo": "l.png",
         "email": "org@example.test", "show_email_public": True},
        {"name": "Hidden email", "organization_id": str(ORG), "email": "no@example.test"},
        {"name": "Private", "is_private": True, "organization_id": ORG, "website": "w", "logo": "l",
         "email": "p@example.test", "show_email_public": False},
        {"name": "Private shown", "is_private": True, "show_name_public": True, "website": "w"},
        {"name": "Bad flag", "is_private": None},
        {"name": "", "is_private": False},
        {}, "string", None,
    ]}, ""),
]


def _document(update):
    doc = dict(BASE)
    doc.update(update)
    return doc


def build_fixture():
    rows = []
    for update, lang in CASES:
        doc = _document(update)
        rows.append({
            "lang": lang,
            "document": json.loads(json_util.dumps(doc, json_options=json_util.CANONICAL_JSON_OPTIONS)),
            "payload": public_payload(doc, lang),
        })
    return json.dumps({"cases": rows}, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def test_go_public_payload_fixture_matches_python():
    text = build_fixture()
    if os.environ.get("UPDATE_GO_FIXTURES") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, "regenerate with UPDATE_GO_FIXTURES=1 and update the Go port"


def test_payload_never_carries_private_fields():
    payload = public_payload(_document({"organizers": [
        {"name": "Org", "email": "hidden@example.test", "show_email_public": False}]}), "fi")
    dumped = json.dumps(payload)
    assert "private@example.test" not in dumped and "hidden@example.test" not in dumped
    assert "approved" not in payload and "submitter_email" not in payload
    assert payload["_id"] == str(OID)
