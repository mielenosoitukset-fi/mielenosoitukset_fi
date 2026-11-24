"""An admin's account deletion is a soft delete that OT reviews in Hallinto."""

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils import account_deletion as ad
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.test_flask_orders import (  # noqa: F401  (orders is a fixture)
    ORDER, _order, _send, orders,
)
from tests.test_hallinto_city_grant_api import api  # noqa: F401  (a fixture the orders fixture needs)

ORDER2 = "hallinto-order-0000000000002"


class _Actor:
    def __init__(self, uid, username="admin"):
        self._id, self.username = uid, username


def _closed(env, name="vieras", **fields):
    app, client, db, seeded, person = env[:5]
    victim = person(name, **fields)
    db.api_tokens.insert_one({"user_id": victim, "revoked_at": None})
    with app.app_context():
        review = ad.soft_delete(db, db.users.find_one({"_id": victim}), _Actor(seeded["admin_id"]), "roskaposti")
    return victim, review


def test_soft_delete_closes_the_account_and_keeps_its_data(orders):
    env = orders
    db = env[2]
    victim, review = _closed(env, role="user")
    doc = db.users.find_one({"_id": victim})
    assert doc["active"] is False and doc["soft_deleted"]["review_id"] == str(review["_id"])
    assert doc["auth_epoch"] == 1 and doc["email"] == "vieras@example.test"
    assert db.api_tokens.find_one({"user_id": victim})["revoked_at"] is not None
    row = db[ad.COLLECTION].find_one({"user_id": victim})
    assert row["status"] == "pending" and row["reason"] == "roskaposti" and "email" not in row


def test_a_second_deletion_of_a_pending_account_is_refused(orders):
    env = orders
    db, app, seeded = env[2], env[0], env[3]
    victim, _ = _closed(env)
    with app.app_context(), pytest.raises(ad.DeletionError):
        ad.soft_delete(db, db.users.find_one({"_id": victim}), _Actor(seeded["admin_id"]))
    assert db[ad.COLLECTION].count_documents({"user_id": victim}) == 1


def test_a_confirmed_order_erases_the_closed_account_and_replays(orders):
    env = orders
    db = env[2]
    victim, _ = _closed(env)
    body, digest = _order("account_deletion", account_id=str(victim), action="delete")
    response, proof = _send(env, body, digest, subject=str(victim))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["effective"] == {"deletion": "deleted"}
    assert db.users.find_one({"_id": victim}) is None
    assert db[ad.COLLECTION].find_one({"user_id": victim})["status"] == "deleted"
    assert _send(env, body, digest, subject=str(victim))[0].status_code == 200
    # A new order for an account that is already gone is a confirmed no-op.
    body2, digest2 = _order("account_deletion", order_id=ORDER2, account_id=str(victim), action="delete")
    assert _send(env, body2, digest2, key=ORDER2, subject=str(victim))[0].status_code == 200


def test_a_reversal_order_reopens_the_account_as_it_was(orders):
    env = orders
    db = env[2]
    victim, _ = _closed(env, active=True)
    body, digest = _order("account_deletion", account_id=str(victim), action="restore")
    response, _ = _send(env, body, digest, subject=str(victim))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["effective"] == {"deletion": "restored"}
    doc = db.users.find_one({"_id": victim})
    assert doc["active"] is True and "soft_deleted" not in doc and doc["auth_epoch"] == 2
    assert db[ad.COLLECTION].find_one({"user_id": victim})["status"] == "restored"
    # Once restored, a decision to erase is stale.
    body2, digest2 = _order("account_deletion", order_id=ORDER2, account_id=str(victim), action="delete")
    assert _send(env, body2, digest2, key=ORDER2, subject=str(victim))[0].status_code == 409


@pytest.mark.parametrize("action", ["delete", "restore"])
def test_an_account_that_was_never_closed_is_never_touched(orders, action):
    env = orders
    db, person = env[2], env[4]
    victim = person("tavallinen")
    body, digest = _order("account_deletion", account_id=str(victim), action=action)
    assert _send(env, body, digest, subject=str(victim))[0].status_code == 409
    assert db.users.find_one({"_id": victim}) is not None


def test_the_protected_owner_and_the_actor_are_never_deleted_by_order(orders):
    env = orders
    olivia, seeded = env[7], env[3]
    for account in (olivia, seeded["admin_id"]):
        body, digest = _order("account_deletion", account_id=str(account), action="delete")
        assert _send(env, body, digest, subject=str(account))[0].status_code in (403, 422)


def test_an_order_for_a_higher_role_needs_a_higher_actor(orders):
    env = orders
    victim, _ = _closed(env, name="jumala", role="god")
    body, digest = _order("account_deletion", account_id=str(victim), action="delete")
    assert _send(env, body, digest, subject=str(victim))[0].status_code == 403


def test_order_shape():
    from mielenosoitukset_fi.admin import flask_orders

    base = {"order_id": ORDER, "kind": "account_deletion", "decision_id": "d", "decision_revision": 1,
            "authority_attestation": "x"}
    good = {**base, "account_id": str(ObjectId()), "action": "delete"}
    assert flask_orders.order_digest(good)
    for bad in ({**good, "action": "close"}, {**good, "account_id": "nope"}, {**good, "role": "admin"}, base):
        assert flask_orders.order_digest(bad) is None


def test_hallinto_links_its_case_once_and_never_decides(orders):
    env = orders
    db = env[2]
    victim, review = _closed(env)
    row = ad.hallinto_update(db, review["_id"], "CAS-12", actor="x")
    assert row["hallinto_case"] == "CAS-12" and row["in_review"] and row["status"] == "pending"
    assert ad.hallinto_update(db, review["_id"], "CAS-12")["status"] == "pending"
    with pytest.raises(ad.HallintoConflict):
        ad.hallinto_update(db, review["_id"], "CAS-99")
    assert ad.hallinto_update(db, ObjectId(), "CAS-12") is None
    view = ad.hallinto_list(db)[0]
    assert view["account"]["id"] == str(victim) and "email" not in str(view)


def test_the_review_is_off_unless_the_flag_is_on(monkeypatch):
    monkeypatch.delenv(ad.ENV_FLAG, raising=False)
    assert ad.review_enabled() is False
    monkeypatch.setenv(ad.ENV_FLAG, "true")
    assert ad.review_enabled() is True
    monkeypatch.setenv(ad.ENV_FLAG, "false")
    assert ad.review_enabled() is False
