"""Hallinto orders carried out by Flask: board roster (H12) and role removal (H17)."""

import json

import pytest
from bson import ObjectId
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mielenosoitukset_fi.admin import city_grants, flask_orders, grant_proofs
from mielenosoitukset_fi.admin.hallinto_city_grant_api import (
    AUDIENCE, attestation_message, order_attestation_message,
)
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, STATE, _b64url, _headers, _verifier, api,
)

ORDER = "hallinto-order-0000000000001"


@pytest.fixture
def orders(api):
    app, client, db, body, digest, proof, seeded, target = api
    board = ObjectId()
    db.organizations.insert_one({"_id": board, "name": "Hallitus"})
    app.config["BOARD_ORG_ID"] = str(board)
    olivia = ObjectId()
    db.users.insert_one(_user_doc("olivia", "olivia@example.test", "OliviaPass1!", _id=olivia))
    app.config["OVERRIDE_OLIVIA_USER_ID"] = str(olivia)

    def person(name, **updates):
        uid = ObjectId()
        db.users.insert_one(_user_doc(name, f"{name}@example.test", "Passw0rd!x", _id=uid, **updates))
        return uid

    def team(*members):
        db.memberships.delete_many({"organization_id": board})
        for member in members:
            db.memberships.insert_one({"user_id": member, "organization_id": board, "governance_approved": True,
                                       "role": "member", "permissions": []})

    def roster_of():
        from mielenosoitukset_fi.utils.system_teams import board_ids
        with app.app_context():
            return sorted(board_ids(db))

    yield app, client, db, seeded, person, team, roster_of, olivia, target


def _order(kind, order_id=ORDER, signer=SIGNER, message=order_attestation_message, **fields):
    body = {"order_id": order_id, "kind": kind, "decision_id": "synthetic-order", "decision_revision": 1,
            "authority_attestation": "placeholder", **fields}
    digest = flask_orders.order_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(message(digest)))
    return body, digest


def _send(env, body, digest, key=ORDER, action=grant_proofs.ACTION_FLASK_ORDER_V1, subject=None):
    app, client, db, seeded = env[:4]
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE, action=action,
                                         intent_digest=digest, factor="passkey", subject=subject)
    return client.post("/internal/hallinto/v1/orders", json=body, headers=_headers(proof=proof, key=key)), proof


def _roster(*ids):
    return sorted(str(i) for i in ids)


def test_roster_sets_the_board_team_and_replays(orders):
    env = orders
    app, client, db, seeded, person, team, roster_of = env[:7]
    a, b, c = person("aino"), person("eero"), person("kaisa")
    team(a, b)
    body, digest = _order("board_roster", roster=_roster(a, b, c))
    response, proof = _send(env, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["status"] == "confirmed" and receipt["order_id"] == ORDER and receipt["kind"] == "board_roster"
    assert receipt["effective"] == {"roster": _roster(a, b, c)} and "account_id" not in receipt
    assert roster_of() == _roster(a, b, c)
    assert db.memberships.find_one({"user_id": c})["grant_source"] == "hallinto_decision"
    again = client.post("/internal/hallinto/v1/orders", json=body, headers=_headers(proof=proof, key=ORDER))
    assert again.get_json() == receipt
    lookup = client.get("/internal/hallinto/v1/operations/by-key", headers=_headers(proof=proof, key=ORDER))
    assert lookup.get_json() == receipt


def test_roster_removes_members_who_are_not_listed(orders):
    env = orders
    app, client, db, seeded, person, team, roster_of = env[:7]
    members = [person(name) for name in ("a1", "a2", "a3", "a4")]
    team(*members)
    body, digest = _order("board_roster", roster=_roster(*members[:3]))
    response, _ = _send(env, body, digest)
    assert response.status_code == 200 and response.get_json()["effective"]["roster"] == _roster(*members[:3])
    removed = db.memberships.find_one({"user_id": members[3]})
    assert removed["governance_approved"] is False and removed["removed_by_operation"]


@pytest.mark.parametrize("case", ["empty_team_more_than_half", "olivia", "inactive", "no_board_org"])
def test_roster_safeguards(orders, case):
    env = orders
    app, client, db, seeded, person, team, roster_of, olivia = env[:8]
    a, b, c, d = person("b1"), person("b2"), person("b3"), person("b4")
    team(a, b, c, d)
    if case == "empty_team_more_than_half":
        roster = _roster(a)  # removes three of four
    elif case == "olivia":
        team(a, b, c, olivia)
        roster = _roster(a, b, c, d)  # keeps everyone but Olivia, a quarter of the team
    elif case == "inactive":
        db.users.update_one({"_id": d}, {"$set": {"active": False}})
        team(a, b)
        roster = _roster(a, b, d)
    else:
        del app.config["BOARD_ORG_ID"]
        roster = _roster(a, b, c, d)
    before = roster_of() if case != "no_board_org" else None
    body, digest = _order("board_roster", roster=roster)
    response, _ = _send(env, body, digest)
    assert response.status_code == 422, response.get_json()
    if before is not None:
        assert roster_of() == before


def test_an_empty_or_unsorted_roster_is_not_an_order(orders):
    env = orders
    person = env[4]
    a, b = person("c1"), person("c2")
    for roster in ([], sorted([str(a), str(b)], reverse=True), [str(a), str(a)]):
        body = {"order_id": ORDER, "kind": "board_roster", "decision_id": "d", "decision_revision": 1,
                "authority_attestation": "x", "roster": roster}
        assert flask_orders.validate_order(body) is None


@pytest.mark.parametrize("role,fields", [
    ("admin", {"role": "admin"}),
    ("global_admin", {"role": "user", "global_admin": True}),
    ("global_admin", {"role": "global_admin"}),
])
def test_role_removal_takes_the_role_and_the_sessions(orders, role, fields):
    env = orders
    app, client, db, seeded, person, team, roster_of = env[:7]
    victim = person("matti", **fields)
    db.api_tokens.insert_one({"user_id": victim, "revoked_at": None})
    body, digest = _order("role_removal", account_id=str(victim), role=role)
    response, proof = _send(env, body, digest, subject=str(victim))
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["effective"] == {"role": "user", "global_admin": False, "full_permissions": False,
                                    "system_team_access": []} and receipt["account_id"] == str(victim)
    doc = db.users.find_one({"_id": victim})
    assert doc["role"] == "user" and doc["governance_managed"] is True and doc.get("auth_epoch") == 1
    assert db.api_tokens.find_one({"user_id": victim})["revoked_at"] is not None
    again = client.post("/internal/hallinto/v1/orders", json=body, headers=_headers(proof=proof, key=ORDER))
    assert again.get_json() == receipt
    # A new order for a role the account no longer has is a confirmed no-op.
    body2, digest2 = _order("role_removal", order_id="hallinto-order-0000000000002", account_id=str(victim), role=role)
    response, _ = _send(env, body2, digest2, key="hallinto-order-0000000000002", subject=str(victim))
    assert response.status_code == 200 and response.get_json()["effective"]["role"] == "user"


def test_role_removal_of_a_city_admin_ends_its_grants(orders):
    env = orders
    app, client, db, seeded, person = env[:5]
    victim = person("tiina", role="city_admin")
    db.admin_scope_grants.insert_one({
        "_id": ObjectId(), "user_id": victim, "scope_type": "city", "scope_keys": ["kuopio"],
        "role": "city_reviewer", "permissions": ["LIST_DEMOS", "VIEW_DEMO"], "revoked_at": None,
        "created_at": city_grants.utcnow(), "updated_at": city_grants.utcnow()})
    body, digest = _order("role_removal", account_id=str(victim), role="city_admin")
    response, _ = _send(env, body, digest, subject=str(victim))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["effective"]["role"] == "user"
    assert db.admin_scope_grants.count_documents({"user_id": victim, "revoked_at": None}) == 0


@pytest.mark.parametrize("who", ["olivia", "unsupported_role", "self", "god_by_a_global_admin"])
def test_role_removal_refusals(orders, who):
    env = orders
    app, client, db, seeded, person, team, roster_of, olivia = env[:8]
    if who == "olivia":
        account, role, status = olivia, "admin", 422
    elif who == "unsupported_role":
        account, role, status = person("pekka", role="user"), "user", 422
    elif who == "god_by_a_global_admin":
        account, role, status = person("jumala", role="god"), "god", 403
    else:
        account, role, status = seeded["admin_id"], "global_admin", 403
    body, digest = _order("role_removal", account_id=str(account), role=role)
    response, _ = _send(env, body, digest, subject=str(account))
    assert response.status_code == status


def test_an_order_needs_its_own_signature_proof_and_key(orders):
    env = orders
    app, client, db, seeded, person = env[:5]
    victim = person("ville", role="admin")
    fields = {"account_id": str(victim), "role": "admin"}
    # An appointment-context signature, a forged one and a proof for another action.
    body, digest = _order("role_removal", message=attestation_message, **fields)
    assert _send(env, body, digest, subject=str(victim))[0].status_code == 403
    body, digest = _order("role_removal", signer=Ed25519PrivateKey.generate(), **fields)
    assert _send(env, body, digest, subject=str(victim))[0].status_code == 403
    body, digest = _order("role_removal", **fields)
    wrong = _send(env, body, digest, action=grant_proofs.ACTION_CITY_ADMIN_END_V1, subject=str(victim))[0]
    assert wrong.status_code == 403
    # The order id is the Idempotency-Key.
    assert _send(env, body, digest, key="hallinto-order-0000000000009", subject=str(victim))[0].status_code == 422
    assert db.users.find_one({"_id": victim})["role"] == "admin"


def _terms(fields):
    return _b64url(json.dumps(fields).encode())


def test_approval_page_and_proof_for_orders(orders):
    from urllib.parse import parse_qs, urlsplit
    env = orders
    app, client, db, seeded, person, team = env[:6]
    a, b = person("ritva", displayname="Ritva Esimerkki"), person("jussi")
    team(a)
    roster = _roster(a, b)
    fields = {"order_id": ORDER, "kind": "board_roster", "decision_id": "synthetic-order",
              "decision_revision": 1, "roster": roster}
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(fields), "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Vahvista hallitustiimin päivitys" in text and "Ritva Esimerkki" in text
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(fields), "state": STATE},
                             headers={"Origin": "http://localhost"})
    assert response.status_code == 303
    returned = parse_qs(urlsplit(response.headers["Location"]).fragment)
    assert set(returned) == {"state", "proof"}
    body, _ = _order("board_roster", roster=roster)
    assert client.post("/internal/hallinto/v1/orders", json=body,
                       headers=_headers(proof=returned["proof"][0], key=ORDER)).status_code == 200
    # A role-removal page names the person and the role.
    victim = person("seppo", role="admin")
    removal = {"order_id": "hallinto-order-0000000000003", "kind": "role_removal", "decision_id": "d1",
               "decision_revision": 1, "account_id": str(victim), "role": "admin"}
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(removal), "state": STATE})
    assert "Vahvista roolin poisto" in page.get_data(as_text=True)
    unknown = {**removal, "account_id": str(ObjectId())}
    assert verifier.get("/admin/hallinto/approve", query_string={"r": _terms(unknown), "state": STATE}).status_code == 400


def test_removing_global_admin_changes_only_that_role(orders):
    env = orders
    app, client, db, seeded, person = env[:5]
    # A superuser who also carries the flag keeps the superuser role.
    both = person("kahden", role="superuser", global_admin=True)
    body, digest = _order("role_removal", account_id=str(both), role="global_admin")
    response, _ = _send(env, body, digest, subject=str(both))
    assert response.status_code == 200
    after = db.users.find_one({"_id": both})
    assert after["role"] == "superuser" and after["global_admin"] is False
    assert response.get_json()["effective"]["full_permissions"] is True  # The superuser role is untouched.


def test_global_admin_from_the_national_team_is_removed_and_seen(orders):
    env = orders
    app, client, db, seeded, person = env[:5]
    national = ObjectId()
    db.organizations.insert_one({"_id": national, "name": "Kansallinen"})
    app.config["NATIONAL_ADMIN_ORG_ID"] = str(national)
    member = person("kansallinen")
    db.memberships.insert_one({"user_id": member, "organization_id": national, "governance_approved": True,
                               "grant_source": "maintainer_bootstrap", "role": "member", "permissions": []})
    body, digest = _order("role_removal", account_id=str(member), role="global_admin")
    response, _ = _send(env, body, digest, subject=str(member))
    assert response.status_code == 200, response.get_json()
    effective = response.get_json()["effective"]
    assert effective["global_admin"] is False and effective["system_team_access"] == []
    assert db.memberships.find_one({"user_id": member})["governance_approved"] is False


def test_a_god_can_be_removed_by_a_god(orders):
    env = orders
    app, client, db, seeded, person = env[:5]
    db.users.update_one({"_id": seeded["admin_id"]}, {"$set": {"role": "god"}})
    target = person("toinen", role="god")
    body, digest = _order("role_removal", account_id=str(target), role="god")
    response, _ = _send(env, body, digest, subject=str(target))
    assert response.status_code == 200, response.get_json()
    assert db.users.find_one({"_id": target})["role"] == "user"


def test_roster_additions_are_capped_and_never_include_the_actor(orders):
    env = orders
    app, client, db, seeded, person, team, roster_of = env[:7]
    old = [person(f"vanha{i}") for i in range(2)]
    team(*old)
    new = [person(f"uusi{i}") for i in range(6)]  # more than max(team size, 5)
    body, digest = _order("board_roster", roster=_roster(*old, *new))
    assert _send(env, body, digest)[0].status_code == 422
    # The approving verifier cannot add themselves.
    body, digest = _order("board_roster", order_id="hallinto-order-0000000000004",
                          roster=_roster(*old, seeded["admin_id"]))
    assert _send(env, body, digest, key="hallinto-order-0000000000004")[0].status_code == 422
    assert roster_of() == _roster(*old)


def test_re_approval_resets_an_old_membership_row(orders):
    env = orders
    app, client, db, seeded, person, team, roster_of = env[:7]
    keep, back = person("pysyy"), person("palaa")
    team(keep)
    board = next(iter(db.organizations.find({"name": "Hallitus"})))["_id"]
    db.memberships.insert_one({"user_id": str(back), "organization_id": board, "governance_approved": False,
                               "role": "owner", "permissions": ["EVERYTHING"]})
    body, digest = _order("board_roster", roster=_roster(keep, back))
    assert _send(env, body, digest)[0].status_code == 200
    rows = list(db.memberships.find({"organization_id": board, "user_id": {"$in": [back, str(back)]}}))
    assert len(rows) == 1 and rows[0]["role"] == "member" and rows[0]["permissions"] == []
    assert roster_of() == _roster(keep, back)


def test_the_page_lists_who_is_added_and_removed(orders):
    env = orders
    app, client, db, seeded, person, team = env[:6]
    stays, goes, comes = person("jaa"), person("lahtee", displayname="Lähtijä"), person("tulee", displayname="Tulija")
    team(stays, goes)
    fields = {"order_id": ORDER, "kind": "board_roster", "decision_id": "d", "decision_revision": 1,
              "roster": _roster(stays, comes)}
    page = _verifier(app, seeded["admin_id"], strong=True, age=60).get(
        "/admin/hallinto/approve", query_string={"r": _terms(fields), "state": STATE})
    text = page.get_data(as_text=True)
    assert "Lisätään: Tulija" in text and "Poistetaan tiimistä: Lähtijä" in text


def _lockdown(action, order_id=ORDER, **extra):
    return _order("site_lockdown", order_id=order_id, action=action, **extra)


def test_site_lockdown_closes_and_reopens_through_the_flask_page_case(orders):
    from mielenosoitukset_fi.utils.site_lock import site_locked
    env = orders
    app, client, db = env[:3]
    app.config["HALLINTO_LOCKDOWN_ORDERS_ENABLED"] = True
    body, digest = _lockdown("close")
    response = _send(env, body, digest)[0]
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["effective"] == {"lockdown": "closed"}
    assert site_locked(db)
    # The same lock and a security case the Flask page can reopen, with Hallinto down.
    lock = db.panic.find_one({"_id": "security-lockdown"})
    case = db.security_cases.find_one({"_id": lock["case_id"]})
    assert lock["panic"] is True and lock["name"] == "security"
    assert case["kind"] == "lockdown" and case["status"] == "locked"
    assert case["events"][0]["method"] == "hallinto_order" and case["events"][0]["action"] == "lockdown"
    # A replay is not a second change.
    assert _send(env, body, digest)[0].status_code == 200
    reopen, digest = _lockdown("reopen", order_id="hallinto-order-0000000000002")
    response = _send(env, reopen, digest, key="hallinto-order-0000000000002")[0]
    assert response.status_code == 200 and response.get_json()["effective"] == {"lockdown": "open"}
    assert not site_locked(db)
    assert db.security_cases.find_one({"_id": case["_id"]})["status"] == "restored"
    assert db.security_cases.find_one({"_id": case["_id"]})["events"][-1]["action"] == "reopen"


def test_site_lockdown_that_changes_nothing_is_stale(orders):
    env = orders
    app, db = env[0], env[2]
    app.config["HALLINTO_LOCKDOWN_ORDERS_ENABLED"] = True
    body, digest = _lockdown("reopen")
    response = _send(env, body, digest)[0]
    assert response.status_code == 409
    db.panic.insert_one({"name": "global", "panic": True})
    body, digest = _lockdown("close", order_id="hallinto-order-0000000000002")
    assert _send(env, body, digest, key="hallinto-order-0000000000002")[0].status_code == 409
    # A lock set the older way is reopened too.
    body, digest = _lockdown("reopen", order_id="hallinto-order-0000000000003")
    assert _send(env, body, digest, key="hallinto-order-0000000000003")[0].status_code == 200
    assert db.panic.find_one({"name": "global"})["panic"] is False


def test_site_lockdown_is_off_unless_the_flag_is_on(orders):
    env = orders
    app, db = env[0], env[2]
    body, digest = _lockdown("close")
    assert _send(env, body, digest)[0].status_code == 404
    app.config["HALLINTO_LOCKDOWN_ORDERS_ENABLED"] = "true"  # a string never turns it on
    assert _send(env, body, digest)[0].status_code == 404
    assert db.panic.count_documents({}) == 0
    verifier = _verifier(app, env[3]["admin_id"], strong=True, age=60)
    fields = {"order_id": ORDER, "kind": "site_lockdown", "decision_id": "d", "decision_revision": 1, "action": "close"}
    assert verifier.get("/admin/hallinto/approve", query_string={"r": _terms(fields), "state": STATE}).status_code == 400


def test_site_lockdown_shape_and_approval_page(orders):
    env = orders
    app = env[0]
    app.config["HALLINTO_LOCKDOWN_ORDERS_ENABLED"] = True
    for bad in ({"action": "open"}, {"action": "close", "account_id": str(ObjectId())}, {}):
        body = {"order_id": ORDER, "kind": "site_lockdown", "decision_id": "d", "decision_revision": 1,
                "authority_attestation": "x", **bad}
        assert flask_orders.order_digest(body) is None
    verifier = _verifier(app, env[3]["admin_id"], strong=True, age=60)
    fields = {"order_id": ORDER, "kind": "site_lockdown", "decision_id": "d", "decision_revision": 1, "action": "close"}
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(fields), "state": STATE})
    assert page.status_code == 200 and "Vahvista hätäsulku" in page.get_data(as_text=True)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms({**fields, "action": "reopen"}), "state": STATE})
    assert "Vahvista sivuston avaaminen" in page.get_data(as_text=True)
