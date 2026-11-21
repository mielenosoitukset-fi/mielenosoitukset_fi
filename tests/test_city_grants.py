"""Shared city grant writer (C02): legacy parity and the city_admin_v1 operation."""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import city_grants
from mielenosoitukset_fi.admin.city_grants import (
    CITY_ADMIN_V1_PERMISSIONS, OPERATIONS, apply_city_admin_v1, ensure_operation_indexes,
    read_city_grant_state, read_operation_receipt, sync_city_scope_grant, validate_appointment,
)
from mielenosoitukset_fi.utils.city_settings import enabled_city_keys, upsert_city_setting
from tests.conftest import _user_doc
from tests.test_admin_user_history import _profile

FIXTURES = json.loads((Path(__file__).resolve().parents[1] / "docs/hallinto-city-grant-fixtures.json").read_text())
KEY = "hallinto-op-0000000001"
# Exclusive end: the Helsinki midnight after the last valid day, as UTC.
VALID_UNTIL = (city_grants.utcnow() + timedelta(days=90)).strftime("%Y-%m-%dT22:00:00Z")
NINE = ["ACCEPT_DEMO", "CREATE_DEMO", "EDIT_CASE", "EDIT_DEMO", "GENERATE_EDIT_LINK",
        "LIST_CASES", "LIST_DEMOS", "VIEW_CASE", "VIEW_DEMO"]


@pytest.fixture
def ctx(app, db, seeded_data):
    ensure_operation_indexes(db)
    target = ObjectId()
    db.users.insert_one(_user_doc("carol", "carol@example.test", "CarolPass1!", _id=target))
    upsert_city_setting(db, "kuopio", True)
    upsert_city_setting(db, "lahti", False)
    with app.app_context():
        yield SimpleNamespace(db=db, target=target, admin=seeded_data["admin_id"], data=seeded_data)


def _request(ctx, city="kuopio", **overrides):
    state = read_city_grant_state(ctx.db, str(ctx.target))
    request = {**FIXTURES["request"], "account_id": str(ctx.target), "city": city,
               "expected_state": state["expected_state"], "valid_until": VALID_UNTIL}
    request.update(overrides)
    return request


def _apply(ctx, request, key=KEY, actor=None, integration="hallinto"):
    return apply_city_admin_v1(ctx.db, integration=integration, actor_id=actor or ctx.admin,
                               idempotency_key=key, request=request)


def _grants(ctx):
    return list(ctx.db.admin_scope_grants.find({"user_id": ctx.target, "revoked_at": None}))


def test_approved_permissions_match_hallinto():
    assert CITY_ADMIN_V1_PERMISSIONS == NINE


# --------------------------------------------------------------------------- #
# Step 1: the legacy views call the shared writer
# --------------------------------------------------------------------------- #
def test_legacy_edit_and_shared_writer_produce_the_same_grant(app, admin_client, db, seeded_data):
    ensure_operation_indexes(db)
    upsert_city_setting(db, "lahti", False)
    legacy, shared = ObjectId(), ObjectId()
    for user_id, name in ((legacy, "legacy"), (shared, "shared")):
        db.users.insert_one(_user_doc(name, f"{name}@example.test", "Pass1word!", _id=user_id))
    payload = _profile(db.users.find_one({"_id": legacy}), **{
        "admin_scope_cities[]": ["lahti"], "admin_scope_permissions[city][]": ["EDIT_DEMO"],
    })
    assert admin_client.post(f"/admin/user/edit_user/{legacy}", data=payload).status_code == 302
    assert "lahti" in enabled_city_keys(db)

    upsert_city_setting(db, "lahti", False)
    admin = db.users.find_one({"_id": seeded_data["admin_id"]})
    actor = SimpleNamespace(_id=admin["_id"], username=admin["username"], role=admin["role"])
    with app.app_context():
        result = sync_city_scope_grant(db, shared, ["Lahti"], ["EDIT_DEMO"], actor)
    assert result.enabled_cities == ["lahti"]

    def shape(user_id):
        grant = db.admin_scope_grants.find_one({"user_id": user_id})
        return ({k: grant[k] for k in ("scope_type", "scope_keys", "role", "permissions", "revoked_at",
                                       "granted_by", "updated_by")},
                db.users.find_one({"_id": user_id})["role"])

    assert shape(legacy) == shape(shared)
    assert shape(shared)[0]["permissions"] == ["EDIT_DEMO", "LIST_DEMOS", "VIEW_DEMO"]
    assert shape(shared)[1] == "city_admin"
    logs = list(db.admin_logs.find({"entity_id": str(shared)}))
    assert logs and {log["actor_id"] for log in logs} == {str(seeded_data["admin_id"])}

    with app.app_context():
        sync_city_scope_grant(db, shared, [], [], actor)
    assert db.users.find_one({"_id": shared})["role"] == "user"
    assert db.admin_scope_grants.find_one({"user_id": shared})["revoked_by"] == str(seeded_data["admin_id"])


def test_shared_writer_refuses_disallowed_enablement_and_replacement(ctx):
    actor = SimpleNamespace(_id=ctx.admin, username="admin", role="admin")
    with pytest.raises(city_grants.CityGrantConflict):
        sync_city_scope_grant(ctx.db, ctx.target, ["lahti"], NINE, actor, allow_city_enablement=False)
    assert "lahti" not in enabled_city_keys(ctx.db) and not _grants(ctx)
    sync_city_scope_grant(ctx.db, ctx.target, ["kuopio"], ["EDIT_DEMO"], actor)
    with pytest.raises(city_grants.CityGrantConflict):
        sync_city_scope_grant(ctx.db, ctx.target, ["kuopio"], NINE, actor, additive_only=True)
    assert _grants(ctx)[0]["permissions"] == ["EDIT_DEMO", "LIST_DEMOS", "VIEW_DEMO"]


# --------------------------------------------------------------------------- #
# Step 2: city_admin_v1
# --------------------------------------------------------------------------- #
def test_contract_fixture_is_a_valid_request_and_bad_shapes_are_rejected():
    intent = validate_appointment(FIXTURES["request"])
    assert intent["city"] == "tampere" and intent["permissions"] == NINE
    assert "authority_attestation" not in intent
    for bad in ({"template": "city_admin_v2"}, {"city": "Tampere"}, {"city": "atlantis"},
                {"decision_revision": 0}, {"decision_revision": True}, {"allow_city_enablement": 1},
                {"account_id": "XYZ"}, {"permissions": ["EDIT_USER"]}, {"authority_attestation": ""},
                {"valid_until": "2027-01-01"}, {"valid_until": "2027-01-01T22:00:00+02:00"},
                {"valid_until": "2027-02-30T22:00:00Z"}, {"valid_until": None}):
        assert validate_appointment({**FIXTURES["request"], **bad}) is None


def test_appointment_confirms_exactly_the_approved_grant(ctx):
    outcome = _apply(ctx, _request(ctx))
    assert outcome.status_code == 200, outcome.body
    body = outcome.body
    assert set(body) == {"operation_id", "account_id", "decision_id", "decision_revision", "valid_until",
                         "status", "checked_at", "effective", "resulted_in_city_enablement"}
    assert body["status"] == "confirmed" and body["account_id"] == str(ctx.target)
    assert body["valid_until"] == VALID_UNTIL
    assert body["effective"] == {"city": "kuopio", "permissions": NINE, "valid_until": VALID_UNTIL}
    assert body["resulted_in_city_enablement"] is False
    [grant] = _grants(ctx)
    assert grant["scope_keys"] == ["kuopio"] and grant["permissions"] == NINE
    assert grant["granted_by"] == str(ctx.admin)
    assert grant["expires_at"].strftime("%Y-%m-%dT%H:%M:%SZ") == VALID_UNTIL
    assert ctx.db.users.find_one({"_id": ctx.target})["role"] == "city_admin"
    operation = ctx.db[OPERATIONS].find_one({"idempotency_key": KEY})
    assert [t["status"] for t in operation["transitions"]] == ["pending", "confirmed"]
    assert "authority_attestation" not in json.dumps(operation, default=str)
    state = read_city_grant_state(ctx.db, str(ctx.target))
    assert state["eligible"] is False
    assert state["scopes"] == [{"city": "kuopio", "permissions": NINE, "valid_until": VALID_UNTIL}]


def test_replay_returns_the_original_receipt_and_never_reapplies(ctx):
    request = _request(ctx)
    first = _apply(ctx, request)
    grant = _grants(ctx)[0]
    # A renewed proof does not change the intent.
    replay = _apply(ctx, {**request, "authority_attestation": "renewed-proof"})
    assert replay.status_code == 200 and replay.body == first.body
    assert _grants(ctx) == [grant]
    # Later revocation does not change the committed receipt; it is not reapplied.
    actor = SimpleNamespace(_id=ctx.admin, username="admin", role="admin")
    sync_city_scope_grant(ctx.db, ctx.target, [], [], actor)
    assert _apply(ctx, request).body == first.body
    assert _grants(ctx) == []


def test_same_key_with_a_different_intent_conflicts(ctx):
    request = _request(ctx)
    _apply(ctx, request)
    outcome = _apply(ctx, {**request, "decision_revision": 3})
    assert (outcome.status_code, outcome.body) == (409, {"code": "idempotency_conflict"})


def test_key_is_scoped_by_integration_and_actor(ctx):
    request = _request(ctx)
    assert _apply(ctx, request).status_code == 200
    other = ObjectId()
    ctx.db.users.insert_one(_user_doc("dora", "dora@example.test", "DoraPass1!", _id=other,
                                      role="global_admin", global_admin=True))
    # A different actor with the same key is a new operation, now against stale state.
    assert _apply(ctx, request, actor=other).body == {"code": "stale_state"}
    assert _apply(ctx, request, integration="other").body == {"code": "stale_state"}
    assert ctx.db[OPERATIONS].count_documents({"idempotency_key": KEY}) == 3


def test_stale_expected_state_is_rejected_without_writes(ctx):
    request = _request(ctx)
    # Profile text is not an owner input; global permissions are.
    ctx.db.users.update_one({"_id": ctx.target}, {"$set": {"displayname": "Renamed"}})
    assert read_city_grant_state(ctx.db, str(ctx.target))["expected_state"] == request["expected_state"]
    ctx.db.users.update_one({"_id": ctx.target}, {"$set": {"global_permissions": ["EDIT_USER"]}})
    outcome = _apply(ctx, request)
    assert (outcome.status_code, outcome.body) == (409, {"code": "stale_state"})
    assert not _grants(ctx)
    receipt = read_operation_receipt(ctx.db, integration="hallinto", actor_id=ctx.admin, idempotency_key=KEY)
    assert receipt.body["status"] == "rejected" and receipt.body["error"] == {"code": "stale_state"}
    # Enabling another city also changes the expected state.
    ctx.db.users.update_one({"_id": ctx.target}, {"$set": {"global_permissions": []}})
    request = _request(ctx)
    upsert_city_setting(ctx.db, "lahti", True)
    assert _apply(ctx, request, key="hallinto-op-0000000002").body == {"code": "stale_state"}


def test_city_enablement_requires_the_decisions_allowance(ctx):
    outcome = _apply(ctx, _request(ctx, city="lahti"))
    assert (outcome.status_code, outcome.body) == (422, {"code": "unsupported_scope"})
    assert "lahti" not in enabled_city_keys(ctx.db) and not _grants(ctx)

    outcome = _apply(ctx, _request(ctx, city="lahti", allow_city_enablement=True), key="hallinto-op-0000000003")
    assert outcome.status_code == 200, outcome.body
    assert outcome.body["resulted_in_city_enablement"] is True
    assert "lahti" in enabled_city_keys(ctx.db)


@pytest.mark.parametrize("change", [
    {"role": "admin"}, {"active": False}, {"banned": True}, {"confirmed": False},
    {"global_admin": True}, {"global_permissions": ["API_READ"]}, {"security_suspension": {"active": True}},
    {"role": "god"},
])
def test_ineligible_targets_are_rejected(ctx, change):
    ctx.db.users.update_one({"_id": ctx.target}, {"$set": change})
    outcome = _apply(ctx, _request(ctx))
    assert outcome.status_code in {403, 422}
    assert not _grants(ctx)


def test_existing_city_grant_makes_the_target_ineligible(ctx):
    actor = SimpleNamespace(_id=ctx.admin, username="admin", role="admin")
    sync_city_scope_grant(ctx.db, ctx.target, ["kuopio"], ["EDIT_DEMO"], actor)
    ctx.db.users.update_one({"_id": ctx.target}, {"$set": {"role": "user"}})
    outcome = _apply(ctx, _request(ctx, city="tampere"))
    assert outcome.body == {"code": "unsupported_scope"}
    assert _grants(ctx)[0]["scope_keys"] == ["kuopio"]


def test_actor_checks(ctx, seeded_data):
    request = _request(ctx)
    # Self-appointment.
    self_request = {**request, "account_id": str(ctx.admin)}
    assert _apply(ctx, self_request).body == {"code": "denied"}
    # An administrator who may not manage scope grants.
    assert _apply(ctx, request, actor=seeded_data["friend_id"]).body == {"code": "denied"}
    # A suspended global admin.
    ctx.db.users.update_one({"_id": ctx.admin}, {"$set": {"active": False}})
    assert _apply(ctx, request).body == {"code": "denied"}
    assert ctx.db[OPERATIONS].count_documents({}) == 0
    # Unknown target is concealed as denied.
    ctx.db.users.update_one({"_id": ctx.admin}, {"$set": {"active": True}})
    missing = {**request, "account_id": str(ObjectId())}
    assert _apply(ctx, missing, key="hallinto-op-0000000004").body == {"code": "denied"}


def test_receipt_lookup_revalidates_visibility(ctx, seeded_data):
    _apply(ctx, _request(ctx))
    lookup = dict(integration="hallinto", idempotency_key=KEY)
    assert read_operation_receipt(ctx.db, actor_id=ctx.admin, **lookup).status_code == 200
    assert read_operation_receipt(ctx.db, actor_id=ctx.admin, integration="hallinto",
                                  idempotency_key="hallinto-op-unknown00000").status_code == 404
    other = ObjectId()
    ctx.db.users.insert_one(_user_doc("dora", "dora@example.test", "DoraPass1!", _id=other,
                                      role="global_admin", global_admin=True))
    assert read_operation_receipt(ctx.db, actor_id=other, **lookup).status_code == 404
    ctx.db.users.update_one({"_id": ctx.admin}, {"$set": {"global_admin": False, "role": "user"}})
    assert read_operation_receipt(ctx.db, actor_id=ctx.admin, **lookup).body == {"code": "denied"}


def test_unverified_result_needs_reconciliation_and_keeps_its_key(ctx, monkeypatch):
    monkeypatch.setattr(city_grants, "verify_effective_grant", lambda db, account, city, valid_until: {"ok": False})
    request = _request(ctx)
    outcome = _apply(ctx, request)
    assert outcome.status_code == 202
    assert outcome.body["status"] == "reconciliation_required"
    monkeypatch.undo()
    # The replay never guesses success, even though the grant now verifies.
    assert _apply(ctx, request).body == outcome.body


def test_write_failure_after_the_first_effect_needs_reconciliation(ctx, monkeypatch):
    original = city_grants.commit_profile_change

    def fail(*args, **kwargs):
        raise city_grants.CityGrantConflict("synthetic competing save")

    monkeypatch.setattr(city_grants, "commit_profile_change", fail)
    outcome = _apply(ctx, _request(ctx))
    assert outcome.status_code == 202 and outcome.body["status"] == "reconciliation_required"
    monkeypatch.setattr(city_grants, "commit_profile_change", original)
    # The grant was written but the role was not: never reported as confirmed.
    assert len(_grants(ctx)) == 1
    assert ctx.db.users.find_one({"_id": ctx.target})["role"] == "user"


def test_interrupted_operation_is_reported_after_its_lease(ctx):
    request = _request(ctx)
    intent = validate_appointment(request)
    now = city_grants.utcnow()
    ctx.db[OPERATIONS].insert_one({
        "_id": ObjectId(), "integration": "hallinto", "actor_id": str(ctx.admin), "idempotency_key": KEY,
        "intent_digest": city_grants._digest(intent), "intent": intent, "account_id": str(ctx.target),
        "status": "pending", "phase": "applying", "created_at": now, "updated_at": now,
        "transitions": [{"status": "pending", "at": now}],
    })
    assert _apply(ctx, request).body["status"] == "pending"
    ctx.db[OPERATIONS].update_one({"idempotency_key": KEY}, {"$set": {"updated_at": now - timedelta(minutes=6)}})
    outcome = _apply(ctx, request)
    assert outcome.status_code == 202 and outcome.body["status"] == "reconciliation_required"
    assert not _grants(ctx)


def test_writes_are_attributed_to_the_actor_in_the_audit_trail(ctx):
    outcome = _apply(ctx, _request(ctx))
    events = list(ctx.db.audit_events.find({"op": {"$exists": True}, "via.operation_id": outcome.body["operation_id"]}))
    if not events:
        pytest.skip("audit trail disabled in this environment")
    assert {event["actor"].get("user_id") for event in events} == {str(ctx.admin)}


def test_a_held_account_fence_blocks_both_writers_before_any_write(ctx, admin_client):
    request = _request(ctx)
    with city_grants.account_fence(ctx.db, ctx.target):
        outcome = _apply(ctx, request)
        assert (outcome.status_code, outcome.body) == (409, {"code": "stale_state"})
        payload = _profile(ctx.db.users.find_one({"_id": ctx.target}), **{
            "admin_scope_cities[]": ["kuopio"], "admin_scope_permissions[city][]": ["EDIT_DEMO"],
        })
        assert admin_client.post(f"/admin/user/edit_user/{ctx.target}", data=payload).status_code == 409
        assert not _grants(ctx)
    # Released: the next writer proceeds.
    assert _apply(ctx, request, key="hallinto-op-0000000005").status_code == 200
    assert ctx.db[city_grants.FENCES].count_documents({}) == 0


def test_two_appointments_for_different_cities_cannot_both_land(ctx, monkeypatch):
    upsert_city_setting(ctx.db, "tampere", True)
    first, second = _request(ctx, city="kuopio"), _request(ctx, city="tampere")
    original = city_grants.verify_effective_grant
    results = {}

    def interleave(db, account, city, valid_until):
        # While the first appointment is still fenced, the second one runs.
        if "second" not in results:
            results["second"] = _apply(ctx, second, key="hallinto-op-0000000006")
        return original(db, account, city, valid_until)

    monkeypatch.setattr(city_grants, "verify_effective_grant", interleave)
    assert _apply(ctx, first).status_code == 200
    assert results["second"].body == {"code": "stale_state"}
    assert [grant["scope_keys"] for grant in _grants(ctx)] == [["kuopio"]]


def test_an_expired_fence_is_taken_over(ctx):
    ctx.db[city_grants.FENCES].insert_one({"_id": str(ctx.target), "token": ObjectId(),
                                           "expires_at": city_grants.utcnow() - timedelta(seconds=1)})
    assert _apply(ctx, _request(ctx)).status_code == 200


def test_a_writer_that_lost_its_fence_never_confirms(ctx, monkeypatch):
    original = city_grants.verify_effective_grant

    def taken_over(db, account, city, valid_until):
        db[city_grants.FENCES].update_one({"_id": str(account)}, {"$set": {"token": ObjectId()}})
        return original(db, account, city, valid_until)

    monkeypatch.setattr(city_grants, "verify_effective_grant", taken_over)
    outcome = _apply(ctx, _request(ctx))
    assert outcome.status_code == 202 and outcome.body["status"] == "reconciliation_required"


@pytest.mark.parametrize("valid_until", [
    "2020-01-01T22:00:00Z",
    (city_grants.utcnow() + timedelta(days=5 * 366 + 3)).strftime("%Y-%m-%dT22:00:00Z"),
])
def test_validity_must_be_ahead_and_bounded(ctx, valid_until):
    outcome = _apply(ctx, _request(ctx, valid_until=valid_until))
    assert (outcome.status_code, outcome.body) == (422, {"code": "unsupported_scope"})
    assert not _grants(ctx)
    lookup = read_operation_receipt(ctx.db, integration="hallinto", actor_id=ctx.admin, idempotency_key=KEY)
    assert lookup.body["valid_until"] == valid_until


def test_expired_grant_stops_working_without_a_job(ctx):
    from mielenosoitukset_fi.users.models import User

    assert _apply(ctx, _request(ctx)).status_code == 200
    user = User.from_db(ctx.db.users.find_one({"_id": ctx.target}))
    assert user.has_scoped_permission("ACCEPT_DEMO", scope_type="city", scope_key="kuopio")
    ctx.db.admin_scope_grants.update_one({"user_id": ctx.target}, {"$set": {
        "expires_at": city_grants.utcnow() - timedelta(seconds=1)}})
    user = User.from_db(ctx.db.users.find_one({"_id": ctx.target}))
    assert not user.has_scoped_permission("ACCEPT_DEMO", scope_type="city", scope_key="kuopio")
    assert not user.has_city_admin_scope_grants() and not user.has_permission("LIST_CASES")
    assert read_city_grant_state(ctx.db, str(ctx.target))["scopes"] == []
    from mielenosoitukset_fi.utils.city_assignment import active_city_admins_for_city
    assert str(ctx.target) not in {str(doc["_id"]) for doc in active_city_admins_for_city(ctx.db, "kuopio")}


def test_legacy_save_keeps_the_appointment_expiry(ctx, admin_client):
    assert _apply(ctx, _request(ctx)).status_code == 200
    payload = _profile(ctx.db.users.find_one({"_id": ctx.target}), **{
        "admin_scope_cities[]": ["kuopio"], "admin_scope_permissions[city][]": NINE,
        "role": "city_admin",
    })
    assert admin_client.post(f"/admin/user/edit_user/{ctx.target}", data=payload).status_code == 302
    [grant] = _grants(ctx)
    assert grant["expires_at"].strftime("%Y-%m-%dT%H:%M:%SZ") == VALID_UNTIL
