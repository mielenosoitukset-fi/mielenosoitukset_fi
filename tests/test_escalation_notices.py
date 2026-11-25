"""Hallinto tells the OT members about cases waiting at OT; Flask mails them once."""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from mielenosoitukset_fi.admin import escalation_notices
from mielenosoitukset_fi.admin.hallinto_city_grant_api import escalation_notice_attestation_message
from tests.test_hallinto_city_grant_api import SIGNER, _b64url, _headers, api  # noqa: F401  (api is a fixture)
from tests.test_moderator_appointments import _env, _person

URL = "/internal/hallinto/v1/escalation-notices"


def _post(client, recipients, *, issued=None, signed=True):
    body = {"recipients": sorted(str(r) for r in recipients),
            "issued_at": (issued or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "authority_attestation": "-"}
    digest = escalation_notices.intent_digest(body) or "0" * 64
    body["authority_attestation"] = _b64url(SIGNER.sign(escalation_notice_attestation_message(digest))) if signed else "AAAA"
    return client.post(URL, json=body, headers=_headers(key=None))


def _case(db, level, *, decided=False):
    demo = ObjectId()
    db.demonstrations.insert_one({"_id": demo, "title": "Testimielenosoitus", "approved": decided, "rejected": False})
    case = ObjectId()
    db.cases.insert_one({"_id": case, "type": "new_demo", "demo_id": str(demo), "running_num": 77,
                         "meta": {"superior_needed": True, "escalation_level": level}})
    return case


def test_only_open_cases_at_ot_are_mailed_and_only_once(api):
    app, client, db, _ = _env(api)[0]
    member = _person(db, "otmember")
    waiting = _case(db, 3)
    _case(db, 2)
    _case(db, 4)
    _case(db, 3, decided=True)
    app.config["ESCALATION_OT_NOTICES_ENABLED"] = True

    response = _post(client, [member])
    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {"status": "ok", "queued": 1}
    queued = list(db.email_queue.find({"_id": {"$regex": "^escalation-ot-notice:"}}))
    assert [q["_id"] for q in queued] == [f"escalation-ot-notice:{waiting}:{member}"]
    assert queued[0]["extra_headers"]["Reply-To"] == "hallinto@mielenosoitukset.fi"
    assert "tuki@" not in str(queued[0])

    again = _post(client, [member])
    assert again.get_json() == {"status": "ok", "queued": 0}
    assert db.email_queue.count_documents({"_id": {"$regex": "^escalation-ot-notice:"}}) == 1


def test_off_by_default_and_refuses_unsigned_or_stale_orders(api):
    app, client, db, _ = _env(api)[0]
    member = _person(db, "otmember")
    _case(db, 3)
    app.config.pop("ESCALATION_OT_NOTICES_ENABLED", None)
    assert _post(client, [member]).get_json() == {"status": "off", "queued": 0}
    assert db.email_queue.count_documents({"_id": {"$regex": "^escalation-ot-notice:"}}) == 0

    app.config["ESCALATION_OT_NOTICES_ENABLED"] = True
    assert _post(client, [member], signed=False).status_code in (401, 403)
    stale = _post(client, [member], issued=datetime.now(timezone.utc) - timedelta(minutes=10))
    assert stale.status_code == 409 and stale.get_json() == {"code": "stale"}
    assert db.email_queue.count_documents({"_id": {"$regex": "^escalation-ot-notice:"}}) == 0


def test_an_unsupported_shape_is_refused():
    assert escalation_notices.intent_digest({"recipients": []}) is None
    ok = {"recipients": ["a" * 24], "issued_at": "2026-11-25T08:00:00Z", "authority_attestation": "x"}
    assert escalation_notices.intent_digest(ok)
    assert escalation_notices.intent_digest({**ok, "recipients": ["b" * 24, "a" * 24]}) is None  # not sorted
    assert escalation_notices.intent_digest({**ok, "recipients": ["a" * 24] * 2}) is None  # duplicate
    assert escalation_notices.intent_digest({**ok, "extra": 1}) is None
