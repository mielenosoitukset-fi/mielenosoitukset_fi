"""Exercise the isolated issuer against the existing Flask login owner."""

import json
from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlsplit

import pytest
from bson import ObjectId
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from werkzeug.security import generate_password_hash

ISSUER = "https://issuer.example.test/hallinto-oidc"
ORIGIN = "https://issuer.example.test"
PREFIX = "/hallinto-oidc"
CALLBACK = "https://hallinto.example.test/auth/callback"
CLIENT = "synthetic-hallinto"
SECRET = "synthetic-issuer-fixture-only-client"
PASSWORD = "synthetic-owner-fixture-only"


@pytest.fixture
def owner(app_factory, db):
    subject = db.users.insert_one({
        "username": "hallinto-fixture-user", "username_canonical": "hallinto-fixture-user",
        "email": "fixture@example.test", "password_hash": generate_password_hash(PASSWORD),
        "active": True, "confirmed": True,
    }).inserted_id
    issuer = app_factory(HALLINTO_OIDC={
        "issuer": ISSUER, "client_id": CLIENT, "client_secret": SECRET, "redirect_uri": CALLBACK,
        "key_id": "fixture", "private_key": RSAKey.generate_key(2048, private=True).as_dict(private=True),
    })
    yield issuer, str(subject)
    db.users.delete_one({"_id": subject})


def _params():
    verifier = token_urlsafe(42)
    return verifier, {
        "client_id": CLIENT, "redirect_uri": CALLBACK, "scope": "openid",
        "response_type": "code", "nonce": token_urlsafe(20), "state": token_urlsafe(20),
        "code_challenge": urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
    }


def _login(browser):
    result = browser.post("/users/auth/login", base_url=ORIGIN,
                          data={"username": "hallinto-fixture-user", "password": PASSWORD})
    assert result.status_code == 302
    assert browser.get("/api/session", base_url=ORIGIN).json["authenticated"] is True


def _authorize(browser, params):
    return browser.get(PREFIX + "/authorize", base_url=ORIGIN, query_string=params)


def _code(browser, params):
    response = _authorize(browser, params)
    assert response.status_code == 302, response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.location.startswith(CALLBACK + "?")
    callback = parse_qs(urlsplit(response.location).query)
    assert callback["state"] == [params["state"]]
    return callback["code"][0]


def _exchange(browser, code, verifier, redirect_uri=CALLBACK):
    return browser.post(PREFIX + "/token", base_url=ORIGIN, auth=(CLIENT, SECRET), data={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    })


def _identity(browser):
    verifier, params = _params()
    code = _code(browser, params)
    response = _exchange(browser, code, verifier)
    assert response.status_code == 200, response.get_data(as_text=True)
    keys = browser.get(PREFIX + "/jwks", base_url=ORIGIN).json
    assert all("d" not in key for key in keys["keys"])
    claims = jwt.decode(response.json["id_token"], KeySet.import_key_set(keys), algorithms=["RS256"]).claims
    assert claims["nonce"] == params["nonce"]
    assert claims["iss"] == ISSUER and claims["aud"] == [CLIENT]
    assert claims["auth_time"] <= claims["iat"] < claims["exp"]
    assert "amr" not in claims and "acr" not in claims
    return claims


def _opacity(owner_client, claims, auth=(CLIENT, SECRET)):
    return owner_client.post(PREFIX + "/session", base_url=ORIGIN, auth=auth,
                             json={"sub": claims["sub"], "sid": claims["sid"]})


def test_issuer_routes_are_off_by_default(app):
    assert app.test_client().get(PREFIX + "/.well-known/openid-configuration", base_url=ORIGIN).status_code == 404


def test_real_login_and_logout_only_revoke_the_linked_browser(owner):
    app, subject = owner
    first, second, backchannel = app.test_client(), app.test_client(), app.test_client()
    _, params = _params()
    not_signed_in = _authorize(first, params)
    assert not_signed_in.status_code == 302
    assert urlsplit(not_signed_in.location).path == "/users/auth/login"

    _login(first)
    _login(second)
    primary, secondary = _identity(first), _identity(second)
    assert primary["sub"] == secondary["sub"] == subject
    assert primary["sid"] != secondary["sid"]
    response = _opacity(backchannel, primary)
    assert response.json == {"active": True}
    assert response.headers["Cache-Control"] == "no-store"
    assert _opacity(backchannel, primary, (CLIENT, "wrong")).status_code == 401
    first.get("/users/auth/logout", base_url=ORIGIN)
    assert _opacity(backchannel, primary).json == {"active": True}
    token = first.get("/api/session", base_url=ORIGIN).json["logout_token"]
    assert first.get("/api/session", base_url=ORIGIN).json["authenticated"] is True
    with first.session_transaction(base_url=ORIGIN) as user_session:
        assert sha256(user_session["hallinto_owner_login"].encode()).hexdigest() == primary["sid"]
    response = first.post("/users/auth/logout", base_url=ORIGIN, data={"logout_token": token})
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/users/auth/login"
    assert first.get("/api/session", base_url=ORIGIN).json["authenticated"] is False
    with first.session_transaction(base_url=ORIGIN) as user_session:
        assert "hallinto_owner_login" not in user_session, app.view_functions["users.auth.logout"].__code__.co_filename
    assert _opacity(backchannel, primary).json == {"active": False}
    assert _opacity(backchannel, secondary).json == {"active": True}


def test_concurrent_exchange_consumes_once_and_requires_exact_redirect_and_pkce(owner):
    app, _ = owner
    browser = app.test_client()
    _login(browser)
    verifier, params = _params()
    assert _authorize(browser, {**params, "redirect_uri": CALLBACK + "/"}).status_code == 400
    downgraded = _authorize(browser, {**params, "code_challenge_method": "plain"})
    assert not downgraded.location or "code=" not in downgraded.location
    code = _code(browser, params)
    assert _exchange(browser, code, verifier, CALLBACK + "/").status_code == 400
    assert _exchange(browser, code, token_urlsafe(42)).status_code == 400
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _exchange(app.test_client(), code, verifier).status_code, range(2)))
    assert sorted(results) == [200, 400]


@pytest.mark.parametrize("change", ["epoch", "password", "factor", "disabled"])
def test_live_owner_revalidates_account_binding_and_security(owner, db, change):
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    verifier, params = _params()
    code = _code(browser, params)
    account = {"_id": ObjectId(subject)}
    if change == "epoch":
        db.users.update_one(account, {"$inc": {"auth_epoch": 1}})
    elif change == "password":
        db.users.update_one(account, {"$set": {"password_hash": generate_password_hash("changed")}})
    elif change == "factor":
        db.mfas.insert_one({"user_id": ObjectId(subject), "secret": "fixture-only"})
    else:
        db.users.update_one(account, {"$set": {"active": False}})
    assert _opacity(app.test_client(), claims).json == {"active": False}
    assert _exchange(app.test_client(), code, verifier).status_code == 400


def _production_settings(name, uri=None):
    return {
        "issuer": ISSUER, "client_id": CLIENT, "client_secret": SECRET,
        "redirect_uri": CALLBACK, "key_id": "fixture",
        "private_key": RSAKey.generate_key(2048, private=True).as_dict(private=True),
        "mongo_database": name,
        "mongo_uri": uri or f"mongodb://fixture:fixture@127.0.0.1:27017/{name}",
    }


def test_protected_config_uses_dedicated_database_and_server_https(app_factory, db, tmp_path, monkeypatch):
    from mielenosoitukset_fi.users import hallinto_issuer_config as config

    name = "hallinto_issuer_" + str(ObjectId())
    state = db.client[name]
    settings = _production_settings(name)
    path = tmp_path / "issuer.json"
    path.write_text(json.dumps(settings))
    path.chmod(0o600)
    monkeypatch.setenv("HALLINTO_ISSUER_CONFIG_FILE", str(path))
    opened = []

    def connect(uri, **kw):
        opened.append((uri, kw))
        return db.client

    monkeypatch.setattr(config, "mongo_client", connect)
    subject = db.users.insert_one({
        "username": "hallinto-fixture-user", "username_canonical": "hallinto-fixture-user",
        "email": "fixture@example.test", "password_hash": generate_password_hash(PASSWORD),
        "active": True, "confirmed": True,
    }).inserted_id
    try:
        app = app_factory()
        browser, backchannel = app.test_client(), app.test_client()
        discovery = PREFIX + "/.well-known/openid-configuration"
        assert browser.get(discovery, base_url=ORIGIN).json["issuer"] == ISSUER
        # A browser's forwarded header must not turn an untrusted HTTP
        # connection into a secure issuer request.
        assert browser.get(discovery, base_url="http://issuer.example.test",
                           headers={"X-Forwarded-Proto": "https"}).status_code == 400
        assert browser.get(discovery, base_url="https://wrong.example.test").status_code == 400
        _login(browser)
        claims = _identity(browser)
        assert _opacity(backchannel, claims).json == {"active": True}
        assert state.hallinto_login_markers.count_documents({"user_id": subject}) == 1
        assert state.hallinto_oidc_codes.count_documents({"subject": str(subject)}) == 1
        assert db.hallinto_login_markers.count_documents({"user_id": subject}) == 0
        assert db.hallinto_oidc_codes.count_documents({"subject": str(subject)}) == 0
        assert state.users.count_documents({}) == 0
        assert len(opened) == 1
        assert opened[0][1]["connect"] is False and opened[0][1]["w"] == "majority"
    finally:
        db.users.delete_one({"_id": subject})
        db.client.drop_database(name)


@pytest.mark.parametrize("file_kind", ["world-readable", "symlink", "invalid-json"])
def test_issuer_refuses_unprotected_config(app_factory, tmp_path, monkeypatch, file_kind):
    path = tmp_path / "issuer.json"
    path.write_text("synthetic-secret")
    path.chmod(0o600)
    if file_kind == "world-readable":
        path.chmod(0o644)
    elif file_kind == "symlink":
        link = tmp_path / "linked.json"
        link.symlink_to(path)
        path = link
    monkeypatch.setenv("HALLINTO_ISSUER_CONFIG_FILE", str(path))
    with pytest.raises(RuntimeError, match=r"^Hallinto issuer needs a readable, restricted JSON file$"):
        app_factory()


@pytest.mark.parametrize("name, uri", [
    ("legacy_site", "mongodb://fixture:fixture@127.0.0.1/legacy_site"),
    ("hallinto_issuer", "mongodb://fixture:fixture@127.0.0.1/other"),
    ("hallinto_issuer", "mongodb://127.0.0.1/hallinto_issuer"),
    ("hallinto_issuer", "mongodb://fixture:fixture@db.example.test/hallinto_issuer"),
    ("hallinto_issuer", "mongodb://fixture:fixture@127.0.0.1/hallinto_issuer?tls=true&tlsInsecure=true"),
    ("hallinto_issuer", "mongodb://fixture:fixture@127.0.0.1/hallinto_issuer?tls=true&tlsAllowInvalidCertificates=true"),
    ("hallinto_issuer", "mongodb://fixture:fixture@127.0.0.1/hallinto_issuer?tls=true&tlsAllowInvalidHostnames=true"),
    ("hallinto_issuer", "mongodb://fixture:fixture@db.example.test/hallinto_issuer?tls=true&TLSALLOWINVALIDHOSTNAMES=true"),
    ("hallinto_issuer", "mongodb://fixture:fixture@db.example.test/hallinto_issuer?tls=true&tlsDisableOCSPEndpointCheck=true"),
])
def test_issuer_refuses_invalid_state_database(app_factory, tmp_path, monkeypatch, name, uri):
    path = tmp_path / "issuer.json"
    path.write_text(json.dumps(_production_settings(name, uri)))
    path.chmod(0o600)
    monkeypatch.setenv("HALLINTO_ISSUER_CONFIG_FILE", str(path))
    with pytest.raises(RuntimeError, match=r"^Hallinto issuer configuration is invalid$"):
        app_factory()


def test_marker_insert_outage_does_not_break_owner_login(owner, db, monkeypatch):
    from pymongo.errors import AutoReconnect

    app, _ = owner
    collection = type(db.hallinto_login_markers)
    original = collection.insert_one

    def insert(self, *args, **kwargs):
        if self.name == "hallinto_login_markers":
            raise AutoReconnect("simulated marker outage")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(collection, "insert_one", insert)
    browser = app.test_client()
    _login(browser)
    _, params = _params()
    assert _authorize(browser, params).status_code == 403


def test_linked_logout_can_retry_a_marker_revocation_outage(owner, db, monkeypatch):
    from pymongo.errors import AutoReconnect

    app, _ = owner
    collection = type(db.hallinto_login_markers)
    original = collection.update_one

    def update(self, *args, **kwargs):
        if self.name == "hallinto_login_markers":
            raise AutoReconnect("simulated marker outage")
        return original(self, *args, **kwargs)

    browser, backchannel = app.test_client(), app.test_client()
    _login(browser)
    claims = _identity(browser)
    token = browser.get("/api/session", base_url=ORIGIN).json["logout_token"]
    with monkeypatch.context() as patch:
        patch.setattr(collection, "update_one", update)
        failed = browser.post("/users/auth/logout", base_url=ORIGIN, data={"logout_token": token})
        assert failed.status_code == 503
        assert failed.headers["Cache-Control"] == "no-store"

    assert browser.get("/api/session", base_url=ORIGIN).json["authenticated"] is True
    assert _opacity(backchannel, claims).json == {"active": True}
    assert browser.post("/users/auth/logout", base_url=ORIGIN, data={"logout_token": token}).status_code == 302
    assert _opacity(backchannel, claims).json == {"active": False}


def _accounts(owner_client, claims, path, body, auth=(CLIENT, SECRET)):
    return owner_client.post(PREFIX + "/accounts/" + path, base_url=ORIGIN, auth=auth,
                             json={"sub": claims["sub"], "sid": claims["sid"], **body})


def test_account_search_needs_a_live_admin_sign_in(owner, db):
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    member = db.users.insert_one({
        "username": "hallinto-member", "username_canonical": "hallinto-member", "displayname": "Jäsen Esimerkki",
        "email": "member@example.test", "password_hash": "x", "active": True, "confirmed": True,
    }).inserted_id
    try:
        # An ordinary account and a wrong client see nothing.
        assert _accounts(app.test_client(), claims, "search", {"q": "hallinto"}).status_code == 403
        assert _accounts(app.test_client(), claims, "search", {"q": "hallinto"}, auth=(CLIENT, "x" * 40)).status_code == 401
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        assert _accounts(app.test_client(), claims, "search", {"q": "h"}).status_code == 400
        found = _accounts(app.test_client(), claims, "search", {"q": "jäsen"}).json["items"]
        assert found == [{"sub": str(member), "username": "hallinto-member", "name": "Jäsen Esimerkki", "active": True}]
        # A regex in the query is literal text.
        assert _accounts(app.test_client(), claims, "search", {"q": ".*.*"}).json["items"] == []
        db.users.update_one({"_id": member}, {"$set": {"security_suspension": {"active": True}}})
        looked = _accounts(app.test_client(), claims, "lookup", {"ids": [str(member), "0" * 24]}).json["items"]
        assert looked == [{"sub": str(member), "username": "hallinto-member", "name": "Jäsen Esimerkki", "active": False}]
        assert _accounts(app.test_client(), claims, "lookup", {"ids": ["not-an-id"]}).status_code == 400
        assert _accounts(app.test_client(), claims, "lookup", {"ids": [str(member)] * 101}).status_code == 400
        # Signing out of Hallinto ends access.
        db.users.update_one({"_id": ObjectId(subject)}, {"$inc": {"auth_epoch": 1}})
        assert _accounts(app.test_client(), claims, "lookup", {"ids": [str(member)]}).status_code == 403
    finally:
        db.users.delete_one({"_id": member})


def test_account_invite_creates_an_ordinary_account_for_an_admin(owner, db, monkeypatch):
    from mielenosoitukset_fi.users import hallinto_issuer

    sent = []
    monkeypatch.setattr(hallinto_issuer, "_invite_sender",
                        type("Sender", (), {"queue_email": lambda self, **kw: sent.append(kw)})())
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    body = {"email": "Uusi.Admin@Example.test", "username": "uusi-admin", "name": "Uusi Admin"}
    created = None
    try:
        # Search rights are not enough: creating needs an admin with CREATE_USER.
        assert _accounts(app.test_client(), claims, "invite", body).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        assert _accounts(app.test_client(), claims, "invite", body).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"global_permissions": ["CREATE_USER"]}})
        assert _accounts(app.test_client(), claims, "invite", {**body, "email": "not-an-email"}).status_code == 400
        assert _accounts(app.test_client(), claims, "invite", {**body, "role": "global_admin"}).status_code == 201
        doc = db.users.find_one({"email": "uusi.admin@example.test"})
        created = doc["_id"]
        # An ordinary, unconfirmed account whatever the request says, with an unknown password.
        assert doc["role"] == "user" and not doc["global_admin"] and doc["confirmed"] is False
        assert doc["created_via"] == "hallinto"
        assert len(sent) == 1 and sent[0]["recipients"] == ["uusi.admin@example.test"]
        assert "/password_reset/" in sent[0]["context"]["reset_url"]
        log = db.admin_logs.find_one({"entity_id": str(created), "event": "user_created"})
        assert log["actor"]["id"] == subject and log["actor"]["via"] == "hallinto"
        # The same address again points at the existing account instead.
        again = _accounts(app.test_client(), claims, "invite", {**body, "username": "toinen"})
        assert again.status_code == 409 and again.json["account"]["sub"] == str(created)
        assert again.json["account"]["active"] is False
        taken = _accounts(app.test_client(), claims, "invite", {**body, "email": "other@example.test"})
        assert taken.status_code == 409 and taken.json["error"] == "username_in_use"
    finally:
        db.users.delete_many({"email": {"$in": ["uusi.admin@example.test", "other@example.test"]}})


def _org_requests(owner_client, claims, path, body, auth=(CLIENT, SECRET)):
    return owner_client.post(PREFIX + "/organization-requests/" + path, base_url=ORIGIN, auth=auth,
                             json={"sub": claims["sub"], "sid": claims["sid"], **body})


def test_organization_requests_for_hallinto(owner, db):
    from datetime import datetime, timezone
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    now = datetime.now(timezone.utc)
    base = {"organization": {"name": "Rauha ry", "email": "info@rauha.example", "links": ["https://rauha.example"]},
            "applicant": {"name": "Hakija", "email": "hakija@example.test"}, "represents": True,
            "access": "organization_account", "status_token_hash": "secret", "created_at": now, "updated_at": now}
    confirmed = db.organization_requests.insert_one({**base, "status": "received",
        "organization": {**base["organization"], "city": "tampere"},
        "logo": {"content_type": "image/png", "data": b"\x89PNG", "size": 4}}).inserted_id
    hidden = db.organization_requests.insert_one({**base, "status": "unconfirmed"}).inserted_id
    suggestion = db.organization_requests.insert_one({**base, "status": "received", "represents": False}).inserted_id
    try:
        assert _org_requests(app.test_client(), claims, "list", {}).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        items = _org_requests(app.test_client(), claims, "list", {}).json["items"]
        ids = {item["id"] for item in items}
        # Unconfirmed requests stay hidden and the status token never leaves Flask.
        assert str(confirmed) in ids and str(hidden) not in ids
        assert all("status_token_hash" not in item for item in items)
        assert items[0]["applicant"]["email"] == "hakija@example.test"
        listed = next(item for item in items if item["id"] == str(confirmed))
        assert listed["organization"]["city"] == "Tampere" and listed["organization"]["has_logo"] is True
        assert "logo" not in listed
        update = {"id": str(confirmed), "status": "verifying", "message": " Lähetimme viestin. ", "case": "case-1",
                  "access": "invite", "invite_email": "PJ@rauha.example"}
        item = _org_requests(app.test_client(), claims, "update", update).json["item"]
        assert item["status"] == "verifying" and item["status_message"] == "Lähetimme viestin."
        assert item["case"] == "case-1" and item["access"] == "invite" and item["invite_email"] == "pj@rauha.example"
        stored = db.organization_requests.find_one({"_id": confirmed})
        assert stored["case_pending"] is False and stored["history"][0]["by"] == subject
        # Refused: back to received, an unconfirmed request, a bad invite address, access on a suggestion.
        assert _org_requests(app.test_client(), claims, "update", {**update, "status": "received"}).status_code == 400
        assert _org_requests(app.test_client(), claims, "update", {**update, "id": str(hidden)}).status_code == 404
        assert _org_requests(app.test_client(), claims, "update", {**update, "invite_email": "nope"}).status_code == 400
        assert _org_requests(app.test_client(), claims, "update", {**update, "id": str(suggestion)}).status_code == 404
        no_access = {"id": str(suggestion), "status": "in_review"}
        assert _org_requests(app.test_client(), claims, "update", no_access).status_code == 200
    finally:
        db.organization_requests.delete_many({"_id": {"$in": [confirmed, hidden, suggestion]}})
