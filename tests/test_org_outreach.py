"""Outbound organization invitations (O6): route, limits, opt-out, privacy."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from bson import ObjectId

from mielenosoitukset_fi.admin import city_grants, grant_proofs, org_outreach
from mielenosoitukset_fi.admin.hallinto_city_grant_api import AUDIENCE, invitation_attestation_message, attestation_message
from tests.conftest import _user_doc
from tests.test_hallinto_city_grant_api import (  # noqa: F401  (api is a fixture)
    SIGNER, STATE, _b64url, _headers, _terms, _verifier, api,
)

KEY = "hallinto-invite-00000000001"
BASE = "/internal/hallinto/v1/org-invitations"
ADDRESS = "info@ympariston-ystavat.example"


class Mailbox:
    def __init__(self):
        self.sent = []

    def queue_email(self, **kwargs):
        self.sent.append(kwargs)


@pytest.fixture
def outreach(api, monkeypatch):
    app, client, db, body, digest, proof, seeded, target = api
    app.config["ORG_OUTREACH_ENABLED"] = True
    mailbox = Mailbox()
    monkeypatch.setattr(org_outreach, "_email_sender", lambda: mailbox)
    yield app, client, db, seeded, mailbox


def _invite(invitation_id=KEY, address=ADDRESS, name="Ympäristön Ystävät ry", signer=SIGNER,
            message=invitation_attestation_message, **over):
    body = {"invitation_id": invitation_id, "case_id": "case-1", "decision_id": "synthetic-decision",
            "decision_revision": 1, "organization_name": name, "address": address,
            "source_url": "https://ympariston-ystavat.example/yhteystiedot", "authority_attestation": "placeholder", **over}
    digest = org_outreach.invitation_digest(body)
    body["authority_attestation"] = _b64url(signer.sign(message(digest)))
    return body, digest


def _post(env, body, digest, key=None, action=grant_proofs.ACTION_ORG_INVITATION_V1, actor=None):
    app, client, db, seeded = env[:4]
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=actor or seeded["admin_id"], audience=AUDIENCE, action=action,
                                         intent_digest=digest, factor="passkey")
    return client.post(BASE, json=body, headers=_headers(proof=proof, key=key or body["invitation_id"]))


def _send(env, **kwargs):
    body, digest = _invite(**kwargs)
    return _post(env, body, digest)


def test_flag_off_means_no_route_and_no_page(api):
    app, client, db, body, digest, proof, seeded, target = api
    assert not app.config.get("ORG_OUTREACH_ENABLED")
    invite, invite_digest = _invite()
    assert client.post(BASE, json=invite, headers=_headers(proof="x", key=KEY)).status_code == 404
    assert client.post(BASE + "/check", json={"address": ADDRESS}, headers=_headers(proof=None, key=None)).status_code == 404


def test_invitation_is_sent_once_with_optout_and_private_receipt(outreach):
    app, client, db, seeded, mailbox = outreach
    body, digest = _invite()
    response = _post(outreach, body, digest)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    h = org_outreach.address_hash(ADDRESS)
    assert receipt["status"] == "confirmed" and receipt["invitation_id"] == KEY and receipt["case_id"] == "case-1"
    assert receipt["effective"]["status"] == "sent" and receipt["effective"]["address_sha256"] == h
    assert ADDRESS not in str(receipt) and ADDRESS not in str(db.city_grant_operations.find_one({}))
    assert len(mailbox.sent) == 1
    mail = mailbox.sent[0]
    assert mail["recipients"] == [ADDRESS] and mail["template_name"] == "org_invitation.html"
    assert mail["extra_headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mail["context"]["form_url"].startswith("https://mielenosoitukset.fi/organisaatiot/hae?kutsu=")
    stored = db.org_invitations.find_one({})
    assert "address" not in stored and stored["address_hash"] == h and len(stored["token_hash"]) == 64
    # The same command again replays the receipt and sends nothing.
    again = _post(outreach, body, digest)
    assert again.get_json() == receipt and len(mailbox.sent) == 1
    # Hallinto can read the same receipt back from the read-only status route.
    status = client.get(f"/internal/hallinto/v1/orders/{KEY}", headers=_headers(key=None))
    assert status.status_code == 200 and status.get_json() == receipt


@pytest.mark.parametrize("address", ["noreply@x.example", "abuse@x.example", "postmaster@x.example"])
def test_role_boxes_are_not_invited(outreach, address):
    app, client, db, seeded, mailbox = outreach
    receipt = _send(outreach, address=address).get_json()
    assert receipt["effective"]["status"] == "invalid_address"
    assert not mailbox.sent and db.org_invitations.count_documents({}) == 0


@pytest.mark.parametrize("address", ["Postmaster@x.example", "no at sign", "a@b", "x@y.example\n", "", 5])
def test_malformed_addresses_are_refused_before_anything_happens(outreach, address):
    app, client, db, seeded, mailbox = outreach
    body = {"invitation_id": KEY, "case_id": "c", "decision_id": "d", "decision_revision": 1,
            "organization_name": "Testi ry", "address": address, "source_url": "https://x.example/yhteys",
            "authority_attestation": "-"}
    assert org_outreach.validate_invitation(body) is None
    assert client.post(BASE, json=body, headers=_headers(proof="x", key=KEY)).status_code in {403, 422}
    assert not mailbox.sent


def test_account_addresses_are_never_invited(outreach):
    app, client, db, seeded, mailbox = outreach
    db.users.insert_one(_user_doc("orgowner", ADDRESS, "OwnerPass1!"))
    receipt = _send(outreach).get_json()
    # Same answer as a recent invitation: a send must not reveal whether an account exists.
    assert receipt["effective"]["status"] == "not_sent" and not mailbox.sent
    db.users.update_one({"email": ADDRESS}, {"$set": {"email": "a.b@elsewhere.example",
                                                       "email_canonical": "a.b@elsewhere.example"}})
    assert _send(outreach, invitation_id="hallinto-invite-00000000002",
                 address="info+kutsu@ympariston-ystavat.example", name="Eri ry").get_json()["effective"]["status"] == "sent"
    db.users.insert_one(_user_doc("alias", "owner@alias.example", "OwnerPass1!"))
    assert _send(outreach, invitation_id="hallinto-invite-00000000003", address="owner+x@alias.example",
                 name="Kolmas ry").get_json()["effective"]["status"] == "not_sent"


def test_limits_per_address_and_organization(outreach, monkeypatch):
    app, client, db, seeded, mailbox = outreach
    monkeypatch.setattr(org_outreach, "VERIFIER_PER_DAY", 50)
    assert _send(outreach).get_json()["effective"]["status"] == "sent"
    # Same address again, a different invitation id.
    second = _send(outreach, invitation_id="hallinto-invite-00000000002")
    assert second.get_json()["effective"]["status"] == "not_sent"
    # An alias of the same mailbox counts as the same address.
    alias = _send(outreach, invitation_id="hallinto-invite-00000000009", address="info+x@ympariston-ystavat.example",
                  name="Toinen nimi ry")
    assert alias.get_json()["effective"]["status"] == "not_sent"
    # Same organization (case and spacing ignored), other address.
    third = _send(outreach, invitation_id="hallinto-invite-00000000003", address="muu@ympariston-ystavat.example",
                  name="YMPÄRISTÖN   ystävät RY")
    assert third.get_json()["effective"]["status"] == "not_sent"
    assert len(mailbox.sent) == 1


def test_verifier_daily_quota(outreach):
    app, client, db, seeded, mailbox = outreach
    for number in range(3):
        assert _send(outreach, invitation_id=f"hallinto-invite-1000000000{number}", name=f"Org {number} ry",
                     address=f"a@org{number}.example").get_json()["effective"]["status"] == "sent"
    over = _send(outreach, invitation_id="hallinto-invite-20000000000", name="Kolme ry", address="c@kolme.example")
    assert over.get_json()["effective"]["status"] == "limit" and len(mailbox.sent) == 3


def test_site_wide_daily_cap(outreach, monkeypatch):
    app, client, db, seeded, mailbox = outreach
    monkeypatch.setattr(org_outreach, "SITE_PER_DAY", 1)
    assert _send(outreach).get_json()["effective"]["status"] == "sent"
    other = _send(outreach, invitation_id="hallinto-invite-00000000002", name="Toinen ry", address="x@toinen.example")
    assert other.get_json()["effective"]["status"] == "limit"


def test_panic_switch_stops_sending(outreach):
    app, client, db, seeded, mailbox = outreach
    db.panic.update_one({"name": "global"}, {"$set": {"panic": True}}, upsert=True)
    body, digest = _invite()
    with app.app_context():  # the panic page answers every HTTP request itself, so call the writer
        outcome = org_outreach.apply_invitation(db, integration="hallinto", actor_id=seeded["admin_id"],
                                                idempotency_key=KEY, request=body)
    assert outcome.body["effective"]["status"] == "paused" and not mailbox.sent


def test_optout_page_needs_a_post_and_blocks_future_invitations(outreach):
    app, client, db, seeded, mailbox = outreach
    _send(outreach)
    optout_url = mailbox.sent[0]["context"]["optout_url"]
    token = parse_qs(urlsplit(optout_url).query)["t"][0]
    visitor = app.test_client()
    page = visitor.get("/organisaatiot/ei-kutsuja", query_string={"t": token})
    assert page.status_code == 200 and "Lopeta kutsut" in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store" and page.headers["Referrer-Policy"] == "no-referrer"
    # A GET (mail scanner prefetch) changes nothing.
    assert db.outreach_optouts.count_documents({}) == 0
    done = visitor.post("/organisaatiot/ei-kutsuja", data={"t": token})
    assert done.status_code == 200 and "Kutsut on lopetettu" in done.get_data(as_text=True)
    assert db.outreach_optouts.count_documents({}) == 1
    # One-click request from the mail program: same effect, idempotent.
    assert visitor.post("/organisaatiot/ei-kutsuja?t=" + token, data={"List-Unsubscribe": "One-Click"}).status_code == 200
    assert db.outreach_optouts.count_documents({}) == 1
    # Even 100 days later the address is suppressed.
    db.org_invitations.update_many({}, {"$set": {"created_at": city_grants.utcnow() - timedelta(days=100)}})
    again = _send(outreach, invitation_id="hallinto-invite-00000000002")
    assert again.get_json()["effective"]["status"] == "suppressed" and len(mailbox.sent) == 1
    check = client.post(BASE + "/check", json={"address": ADDRESS}, headers=_headers(proof=None, key=None))
    assert check.get_json() == {"sendable": False, "reason": "suppressed"}


def test_unknown_optout_tokens_do_nothing(outreach):
    app, client, db, seeded, mailbox = outreach
    visitor = app.test_client()
    for token in ["", "x" * 30, "short"]:
        assert visitor.get("/organisaatiot/ei-kutsuja", query_string={"t": token}).status_code == 404
        assert visitor.post("/organisaatiot/ei-kutsuja", data={"t": token}).status_code == 404
    assert db.outreach_optouts.count_documents({}) == 0


def test_check_route_says_nothing_about_accounts_and_is_rate_limited(outreach, monkeypatch):
    app, client, db, seeded, mailbox = outreach
    db.users.insert_one(_user_doc("orgowner", ADDRESS, "OwnerPass1!"))
    answer = client.post(BASE + "/check", json={"address": ADDRESS}, headers=_headers(proof=None, key=None))
    assert answer.get_json() == {"sendable": True, "reason": None}
    assert client.post(BASE + "/check", json={"address": "noreply@x.example"},
                       headers=_headers(proof=None, key=None)).get_json()["reason"] == "invalid_address"
    assert client.post(BASE + "/check", json={"address": ADDRESS, "x": 1},
                       headers=_headers(proof=None, key=None)).status_code == 422
    monkeypatch.setattr(org_outreach, "CHECK_PER_MINUTE", 1)
    assert client.post(BASE + "/check", json={"address": ADDRESS},
                       headers=_headers(proof=None, key=None)).get_json()["reason"] == "rate_limited"
    # No workload identity, no answer.
    assert client.post(BASE + "/check", json={"address": ADDRESS}).status_code == 403


def test_proof_attestation_and_actor_are_all_required(outreach):
    app, client, db, seeded, mailbox = outreach
    for options in (dict(message=attestation_message), dict(signer=__import__(
            "cryptography.hazmat.primitives.asymmetric.ed25519", fromlist=["x"]).Ed25519PrivateKey.generate())):
        body, digest = _invite(**options)
        assert _post(outreach, body, digest).status_code == 403
    body, digest = _invite()
    assert _post(outreach, body, digest, action=grant_proofs.ACTION_CITY_ADMIN_V1).status_code == 403
    db.users.update_one({"_id": seeded["admin_id"]}, {"$set": {"global_admin": False, "role": "user"}})
    assert _post(outreach, body, digest).status_code == 403
    assert not mailbox.sent and db.org_invitations.count_documents({}) == 0


def test_invitation_id_must_equal_the_key_and_fields_are_exact(outreach):
    app, client, db, seeded, mailbox = outreach
    body, digest = _invite()
    with app.app_context():
        proof = grant_proofs.issue_proof(db, actor_id=seeded["admin_id"], audience=AUDIENCE,
                                         action=grant_proofs.ACTION_ORG_INVITATION_V1, intent_digest=digest,
                                         factor="passkey")
    assert client.post(BASE, json=body, headers=_headers(proof=proof, key="hallinto-other-0000000000001")).status_code in {403, 422}
    for bad in ({**body, "source_url": "http://not-https.example/x"}, {**body, "extra": 1},
                {**body, "organization_name": "x"}, {**body, "note": "a\nb"}):
        assert org_outreach.validate_invitation(bad) is None
    assert not mailbox.sent


def test_a_mail_failure_is_not_resent_by_a_retry(outreach, monkeypatch):
    app, client, db, seeded, mailbox = outreach

    def boom():
        raise RuntimeError("smtp down")

    monkeypatch.setattr(org_outreach, "_email_sender", boom)
    body, digest = _invite()
    assert _post(outreach, body, digest).status_code == 503
    monkeypatch.setattr(org_outreach, "_email_sender", lambda: mailbox)
    again = _post(outreach, body, digest)
    assert again.status_code in {202, 200} and not mailbox.sent


def _fields(body):
    return {k: v for k, v in body.items() if k != "authority_attestation"}


def test_approval_page_masks_the_address_and_issues_a_bound_proof(outreach):
    app, client, db, seeded, mailbox = outreach
    body, digest = _invite()
    verifier = _verifier(app, seeded["admin_id"], strong=True, age=60)
    page = verifier.get("/admin/hallinto/approve", query_string={"r": _terms(_fields(body)), "state": STATE})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Kutsu organisaatiolle" in text and "i***@ympariston-ystavat.example" in text
    assert ADDRESS not in text and "Vahvista ja lähetä" in text
    response = verifier.post("/admin/hallinto/approve", data={"r": _terms(_fields(body)), "state": STATE},
                             headers={"Origin": "http://localhost"})
    assert response.status_code == 303
    returned = parse_qs(urlsplit(response.headers["Location"]).fragment)
    assert "expected_state" not in returned
    sent = client.post(BASE, json=body, headers=_headers(proof=returned["proof"][0], key=KEY))
    assert sent.status_code == 200 and sent.get_json()["effective"]["status"] == "sent"


def test_approval_page_warns_for_a_suppressed_address(outreach):
    app, client, db, seeded, mailbox = outreach
    db.outreach_optouts.insert_one({"address_hash": org_outreach.address_hash(ADDRESS)})
    body, digest = _invite()
    page = _verifier(app, seeded["admin_id"], strong=True, age=60).get(
        "/admin/hallinto/approve", query_string={"r": _terms(_fields(body)), "state": STATE})
    assert "Kutsua ei voi vahvistaa" in page.get_data(as_text=True)


def test_approval_page_is_absent_when_the_flag_is_off(api):
    app, client, db, body, digest, proof, seeded, target = api
    invite, _ = _invite()
    page = _verifier(app, seeded["admin_id"], strong=True, age=60).get(
        "/admin/hallinto/approve", query_string={"r": _terms(_fields(invite)), "state": STATE})
    assert page.status_code == 404


def test_a_refused_verifier_cannot_drain_the_site_quota(outreach, monkeypatch):
    app, client, db, seeded, mailbox = outreach
    monkeypatch.setattr(org_outreach, "VERIFIER_PER_DAY", 1)
    monkeypatch.setattr(org_outreach, "SITE_PER_DAY", 2)
    assert _send(outreach).get_json()["effective"]["status"] == "sent"
    for number in range(3):
        refused = _send(outreach, invitation_id=f"hallinto-invite-3000000000{number}", name=f"Muu {number} ry",
                        address=f"x{number}@muu.example")
        assert refused.get_json()["effective"]["status"] == "limit"
    with app.app_context():
        row = db.outreach_rate.find_one({"name": "site"})
    assert row["count"] == 1


def test_format_characters_are_refused_in_visible_text(outreach):
    app, client, db, seeded, mailbox = outreach
    for field, value in (("note", "hei\u202esalaisuus"), ("organization_name", "Testi\u200b ry")):
        body = {"invitation_id": KEY, "case_id": "c", "decision_id": "d", "decision_revision": 1,
                "organization_name": "Testi ry", "address": ADDRESS, "source_url": "https://x.example/yhteys",
                "authority_attestation": "-", field: value}
        assert org_outreach.validate_invitation(body) is None


def test_the_flag_must_be_a_real_true(api):
    app, client, db, body, digest, proof, seeded, target = api
    app.config["ORG_OUTREACH_ENABLED"] = "false"
    invite, _ = _invite()
    assert client.post(BASE, json=invite, headers=_headers(proof="x", key=KEY)).status_code == 404
