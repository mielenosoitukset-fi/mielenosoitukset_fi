"""Superadmin and god appointments decided in Hallinto (roadmap phase F)."""

from datetime import timedelta

import pyotp
import pytest
from bson import ObjectId
from werkzeug.security import generate_password_hash

from mielenosoitukset_fi.admin import elevated_grants
from mielenosoitukset_fi.admin.city_grants import ensure_operation_indexes
from mielenosoitukset_fi.admin.elevated_grants import (
    apply_elevated_v1, read_elevated_receipt, read_elevated_state, validate_elevated,
)
from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils.system_teams import clearance_approved, user_team_access
from mielenosoitukset_fi.utils.team_governance import cast_vote
from tests.conftest import _user_doc
from tests.test_system_teams import teams  # noqa: F401  (fixture)

KEY = "hallinto-elevated-000001"


def _valid_until(days=90):
    return (elevated_grants.utcnow() + timedelta(days=days)).strftime("%Y-%m-%dT22:00:00Z")


def _request(db, account, template="superadmin_v1", **overrides):
    request = {"account_id": str(account), "template": template,
               "expected_state": read_elevated_state(db, str(account))["expected_state"],
               "decision_id": "jory-decision", "decision_revision": 2,
               "authority_attestation": "attested-by-test", "valid_until": _valid_until()}
    if template == "god_v1":
        request["concurring_decision_id"] = "ot-decision"
    request.update(overrides)
    return request


def _apply(db, actor, request, key=KEY):
    return apply_elevated_v1(db, integration="hallinto", actor_id=actor, idempotency_key=key, request=request)


@pytest.fixture
def ctx(app, db, teams):  # noqa: F811
    ensure_operation_indexes(db)
    target = ObjectId()
    db.users.insert_one(_user_doc("carol", "carol@example.test", "CarolPass1!", _id=target))
    with app.app_context():
        yield {**teams, "target": target}


def test_request_shapes():
    base = {"account_id": "a" * 24, "template": "superadmin_v1", "expected_state": "x", "decision_id": "d",
            "decision_revision": 1, "authority_attestation": "p", "valid_until": "2027-01-01T22:00:00Z"}
    assert validate_elevated(base)
    assert validate_elevated({**base, "template": "god_v1"}) is None  # God needs both bodies.
    assert validate_elevated({**base, "template": "god_v1", "concurring_decision_id": "d"}) is None
    assert validate_elevated({**base, "template": "god_v1", "concurring_decision_id": "e"})
    assert validate_elevated({**base, "template": "global_admin"}) is None
    assert validate_elevated({**base, "city": "tampere"}) is None
    assert validate_elevated({**base, "decision_revision": True}) is None


def test_superadmin_from_a_decision_confirms_and_ends_at_valid_until(app, db, ctx):
    request = _request(db, ctx["target"])
    outcome = _apply(db, ctx["admin_id"], request)
    assert outcome.status_code == 200, outcome.body
    assert outcome.body["effective_role"] == "superadmin" and outcome.body["decision_id"] == "jory-decision"
    assert "national" in user_team_access(db, ctx["target"]) and User.from_OID(ctx["target"]).global_admin
    row = db.memberships.find_one({"user_id": ctx["target"], "organization_id": ctx["national"]})
    assert row["grant_source"] == "hallinto_decision"
    audit = db.board_audit_logs.find_one({"user_id": str(ctx["target"]), "source": "hallinto_decision"})
    assert audit["granted_by_id"] == str(ctx["admin_id"]) and audit["decision_revision"] == 2

    # Replay returns the same receipt and writes nothing new.
    replay = _apply(db, ctx["admin_id"], request)
    assert replay.status_code == 200 and replay.body["operation_id"] == outcome.body["operation_id"]
    assert db.board_audit_logs.count_documents({"user_id": str(ctx["target"]), "source": "hallinto_decision"}) == 1
    # The end is enforced on every read: no job runs.
    db.board_clearances.update_one({"user_id": str(ctx["target"])},
                                   {"$set": {"team_requests.national.valid_until": elevated_grants.utcnow() - timedelta(seconds=1)}})
    assert not clearance_approved(db, ctx["target"])
    assert "national" not in user_team_access(db, ctx["target"])
    assert not User.from_OID(ctx["target"]).global_admin


def test_a_routine_board_vote_cannot_replace_the_decision(app, db, ctx):
    assert _apply(db, ctx["admin_id"], _request(db, ctx["target"])).status_code == 200
    state = cast_vote(db, ctx["target"], ctx["friend_id"], "national", False)
    assert state["source"] == "hallinto_decision"
    assert clearance_approved(db, ctx["target"])


def test_executor_and_target_checks(app, db, ctx):
    # Nobody grants to their own account.
    assert _apply(db, ctx["admin_id"], _request(db, ctx["admin_id"])).status_code == 403
    # An executor without owner authority is denied.
    assert _apply(db, ctx["user_id"], _request(db, ctx["target"])).status_code == 403
    # A stale expected state is rejected without writes.
    stale = _request(db, ctx["target"], expected_state="0" * 64)
    assert _apply(db, ctx["admin_id"], stale, key="hallinto-elevated-000002").status_code == 409
    assert "national" not in user_team_access(db, ctx["target"])
    # A god appointment cannot target an ordinary account.
    god = _request(db, ctx["target"], template="god_v1")
    assert _apply(db, ctx["admin_id"], god, key="hallinto-elevated-000003").status_code == 422
    # Validity must be ahead and bounded.
    for days in (-1, 6 * 366):
        late = _request(db, ctx["target"], valid_until=_valid_until(days))
        assert _apply(db, ctx["admin_id"], late, key=f"hallinto-elevated-1{days:+07d}".replace("+", "p")).status_code == 422


def test_existing_rights_do_not_stack(app, db, ctx):
    db.users.update_one({"_id": ctx["target"]}, {"$set": {"global_admin": True}})
    assert read_elevated_state(db, str(ctx["target"]))["eligible"]["superadmin_v1"] is False
    assert _apply(db, ctx["admin_id"], _request(db, ctx["target"])).status_code == 422


def test_same_key_with_a_different_intent_conflicts(app, db, ctx):
    assert _apply(db, ctx["admin_id"], _request(db, ctx["target"])).status_code == 200
    other = _request(db, ctx["target"], decision_id="another")
    assert _apply(db, ctx["admin_id"], other).body == {"code": "idempotency_conflict"}


def _enrolled_candidate(db):
    candidate = db.users.insert_one({**_user_doc("godcandidate", "godc@example.test", "not-a-login"),
                                     "active": False, "confirmed": False, "god_candidate": True}).inserted_id
    factors = [ObjectId(), ObjectId()]
    for oid in factors:
        db.mfas.insert_one({"_id": oid, "user_id": candidate, "secret": pyotp.random_base32()})
    db.user_passkeys.insert_one({"user_id": candidate, "credential_id": "Y2FuZA", "public_key": "Y2FuZA"})
    db.users.update_one({"_id": candidate}, {"$set": {"god_security": {
        "policy": 1, "totp_ids": factors, "secret_hash": generate_password_hash("s" * 64)}}})
    return candidate


def test_god_from_both_bodies_activates_an_enrolled_candidate(app, db, ctx):
    candidate = _enrolled_candidate(db)
    outcome = _apply(db, ctx["admin_id"], _request(db, candidate, template="god_v1"))
    assert outcome.status_code == 200, outcome.body
    assert outcome.body["effective_role"] == "god" and outcome.body["concurring_decision_id"] == "ot-decision"
    user = db.users.find_one({"_id": candidate})
    assert user["role"] == "god" and user["active"] and not user["god_candidate"]


def test_god_waits_for_enrollment_and_confirms_on_lookup(app, db, ctx):
    candidate = db.users.insert_one({**_user_doc("godwaiting", "godw@example.test", "not-a-login"),
                                     "active": False, "confirmed": False, "god_candidate": True}).inserted_id
    outcome = _apply(db, ctx["admin_id"], _request(db, candidate, template="god_v1"))
    assert outcome.status_code == 202 and outcome.body["status"] == "pending"
    assert db.users.find_one({"_id": candidate})["active"] is False
    lookup = read_elevated_receipt(db, integration="hallinto", actor_id=ctx["admin_id"], idempotency_key=KEY)
    assert lookup.status_code == 202


def test_a_decision_grants_only_once(app, db, ctx):
    assert _apply(db, ctx["admin_id"], _request(db, ctx["target"])).status_code == 200
    db.board_clearances.update_one({"user_id": str(ctx["target"])},
                                   {"$set": {"team_requests.national.valid_until": elevated_grants.utcnow() - timedelta(seconds=1)}})
    again = _request(db, ctx["target"], valid_until=_valid_until(200))
    assert _apply(db, ctx["admin_id"], again, key="hallinto-elevated-000009").status_code == 422
    assert not clearance_approved(db, ctx["target"])


def test_legacy_god_votes_cannot_replace_the_decision(app, db, ctx):
    from mielenosoitukset_fi.utils.god_appointments import cast_appointment_vote
    from mielenosoitukset_fi.utils.team_governance import GovernanceConflict

    candidate = db.users.insert_one({**_user_doc("godvote", "godv@example.test", "not-a-login"),
                                     "active": False, "confirmed": False, "god_candidate": True}).inserted_id
    assert _apply(db, ctx["admin_id"], _request(db, candidate, template="god_v1")).status_code == 202
    with pytest.raises(GovernanceConflict):
        cast_appointment_vote(db, candidate, User.from_OID(ctx["friend_id"]), True)
    god = db.board_clearances.find_one({"user_id": str(candidate)})["team_requests"]["god"]
    assert god["method"] == "hallinto_decision"
