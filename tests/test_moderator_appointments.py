"""National moderator appointments decided in Hallinto and written by Flask."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from bson import ObjectId

from mielenosoitukset_fi.admin import grant_proofs, moderator_appointments
from mielenosoitukset_fi.admin.hallinto_city_grant_api import (
    AUDIENCE, attestation_message, moderator_attestation_message,
)
from mielenosoitukset_fi.utils import duty_shifts
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, STATE, VALID_UNTIL, _b64url, _headers, _terms, _verifier, api,
)

KEY = "hallinto-moderator-0000000001"
URL = "/internal/hallinto/v1/moderator-appointments"


def _person(db, name, **updates):
    uid = ObjectId()
    db.users.insert_one(_user_doc(name, f"{name}@example.test", "Passw0rd!x", _id=uid, **updates))
    return uid


def _body(app, db, target, approvers, *, self_booking=False, message=moderator_attestation_message, **over):
    with app.app_context():
        state = moderator_appointments.read_state(db, str(target))
    body = {"account_id": str(target), "template": "national_moderator_v1", "decision_id": "synthetic-mod",
            "decision_revision": 1, "valid_until": VALID_UNTIL, "self_booking": self_booking,
            "shift_approvers": sorted(str(a) for a in approvers), "expected_state": state["expected_state"],
            "authority_attestation": "placeholder", **over}
    digest = moderator_appointments.intent_digest(body)
    body["authority_attestation"] = _b64url(SIGNER.sign(message(digest)))
    return body, digest


def _post(env, body, digest, key=KEY, action=grant_proofs.ACTION_MODERATOR_V1):
    app, client, db, seeded = env
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE, action=action,
                                         intent_digest=digest, factor="passkey", subject=body["account_id"])
    return client.post(URL, json=body, headers=_headers(proof=proof, key=key))


def _env(api):
    app, client, db, body, digest, proof, seeded, target = api
    return (app, client, db, seeded), target


def test_appointment_records_approvers_and_grants_nothing_until_a_shift(api):
    env, _ = _env(api)
    app, client, db, seeded = env
    moderator, approver = _person(db, "moderator"), _person(db, "approver")
    body, digest = _body(app, db, moderator, [approver], self_booking=True)
    response = _post(env, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["valid_until"] == VALID_UNTIL
    assert receipt["effective"]["city"] == ""
    assert receipt["effective"]["permissions"] == sorted(duty_shifts.NATIONAL_MODERATOR_V1)

    grant = db[duty_shifts.GRANTS].find_one({"user_id": moderator})
    assert grant["self_booking"] is True and grant["approvers"] == [approver]
    assert grant["source"]["hallinto_decision_id"] == "synthetic-mod"
    assert grant["created_by"]["id"] == str(seeded["admin_id"])
    assert db.users.find_one({"_id": moderator})["global_permissions"] == []
    assert duty_shifts.active_duty(db, moderator) is None

    # The one appointment replays without a second grant.
    again = _post(env, body, digest)
    assert again.get_json() == receipt
    assert db[duty_shifts.GRANTS].count_documents({"user_id": moderator}) == 1


def test_an_approver_in_the_appointment_approves_the_shift(api):
    env, _ = _env(api)
    app, client, db, seeded = env
    moderator, approver = _person(db, "moderator"), _person(db, "approver")
    body, digest = _body(app, db, moderator, [approver])
    assert _post(env, body, digest).status_code == 200
    grant = db[duty_shifts.GRANTS].find_one({"user_id": moderator})
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=1)
    shift = duty_shifts.add_shift(db, grant_id=grant["_id"], starts_at=start, ends_at=start + timedelta(hours=2),
                                  source={"decision": "vuoro"}, actor={"id": str(moderator)})
    during = start + timedelta(minutes=30)
    # Booked by the moderator without self-booking: nothing until an approver approves.
    assert duty_shifts.active_duty(db, moderator, during) is None
    assert duty_shifts.approve_shift(db, shift_id=shift["_id"], actor={"id": str(approver)}, now=now)
    assert duty_shifts.active_duty(db, moderator, during)


def test_signed_terms_cannot_be_changed_or_borrowed(api):
    env, _ = _env(api)
    app, client, db, seeded = env
    moderator, approver, other = _person(db, "moderator"), _person(db, "approver"), _person(db, "other")
    body, digest = _body(app, db, moderator, [approver])
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_MODERATOR_V1, intent_digest=digest,
                                         factor="passkey", subject=str(moderator))
    # Someone added to the approvers after the approval.
    swapped = {**body, "shift_approvers": sorted([str(approver), str(other)])}
    assert client.post(URL, json=swapped, headers=_headers(proof=proof, key=KEY)).status_code == 403
    assert client.post(URL, json={**body, "self_booking": True}, headers=_headers(proof=proof, key=KEY)).status_code == 403
    assert db[duty_shifts.GRANTS].count_documents({}) == 0

    # A city appointment signature or proof action never makes a moderator.
    wrong_sig, wrong_digest = _body(app, db, moderator, [approver], message=attestation_message)
    assert _post(env, wrong_sig, wrong_digest).status_code == 403
    wrong_action = _post(env, body, digest, key="hallinto-moderator-0000000002",
                         action=grant_proofs.ACTION_CITY_ADMIN_V1)
    assert wrong_action.status_code == 403
    assert db[duty_shifts.GRANTS].count_documents({}) == 0


def test_refused_targets_and_approvers(api):
    env, _ = _env(api)
    app, client, db, seeded = env
    moderator, approver = _person(db, "moderator"), _person(db, "approver")
    organization = _person(db, "orgaccount", account_type="organization")
    banned = _person(db, "banneduser", banned=True)
    for number, target in enumerate((organization, banned)):
        body, digest = _body(app, db, target, [approver])
        refused = _post(env, body, digest, key=f"hallinto-moderator-refused-{number:04d}")
        assert refused.status_code == 422, refused.get_json()
    # An approver that does not exist.
    body, digest = _body(app, db, moderator, [ObjectId()])
    assert _post(env, body, digest, key="hallinto-moderator-0000000003").status_code == 422
    # The appointee never approves their own shifts.
    assert moderator_appointments.validate_apply({**body, "shift_approvers": [str(moderator)],
                                                  "account_id": str(moderator)}) is None
    assert db[duty_shifts.GRANTS].count_documents({}) == 0


def test_a_second_live_appointment_is_refused(api):
    env, _ = _env(api)
    app, client, db, seeded = env
    moderator, approver = _person(db, "moderator"), _person(db, "approver")
    first, first_digest = _body(app, db, moderator, [approver])
    assert _post(env, first, first_digest).status_code == 200
    # The owner state changed (a live appointment now exists): the page reports not eligible.
    with app.app_context():
        assert moderator_appointments.read_state(db, str(moderator))["eligible"] is False
    second, second_digest = _body(app, db, moderator, [approver], decision_id="synthetic-mod-2")
    refused = _post(env, second, second_digest, key="hallinto-moderator-0000000004")
    assert refused.status_code == 422
    assert db[duty_shifts.GRANTS].count_documents({"user_id": moderator}) == 1


def test_approval_page_names_the_approvers_and_binds_the_proof(api):
    env, _ = _env(api)
    app, client, db, seeded = env
    moderator = _person(db, "moderator")
    approver = _person(db, "approver", displayname="Aino Approver")
    body, digest = _body(app, db, moderator, [approver], self_booking=True)
    verifier = _verifier(app, seeded["admin_id"])
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(body), "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Valtakunnallinen moderaattori" in text and "Aino Approver" in text and "Ilman hyväksyntää" in text
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(body), "state": STATE},
                             headers={"Origin": "http://localhost"})
    assert response.status_code == 303
    fragment = parse_qs(urlsplit(response.headers["Location"]).fragment)
    with app.app_context():
        # The issued proof applies exactly this intent and nothing else.
        spent = grant_proofs.consume_proof(db, fragment["proof"][0], audience=AUDIENCE,
                                           action=grant_proofs.ACTION_MODERATOR_V1,
                                           intent_digest=digest, idempotency_key=KEY)
    assert str(spent) == str(seeded["admin_id"])
    # Unknown extra terms are not accepted by the page.
    bad = {**body, "city": "kuopio"}
    assert verifier.get("/admin/hallinto/approve", query_string={"r": _terms(bad), "state": STATE}).status_code == 400
