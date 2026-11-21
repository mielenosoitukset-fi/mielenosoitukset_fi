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


def test_lapsed_marker_signs_out_and_back_in_by_itself(owner, db, monkeypatch):
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
    page = _authorize(browser, params)
    body = page.get_data(as_text=True)
    assert page.status_code == 403 and page.mimetype == "text/html"
    assert 'name="logout_token"' in body and "relogin=1" in body
    # A second failure shows the plain error instead of looping.
    again = _authorize(browser, {**params, "relogin": "1"})
    assert again.status_code == 403 and again.is_json
    # The sign-out form returns to the sign-in with the authorization URL.
    token = body.split('name="logout_token" value="')[1].split('"')[0]
    nxt = body.split('name="next" value="')[1].split('"')[0].replace("&amp;", "&")
    out = browser.post("/users/auth/logout", base_url=ORIGIN, data={"logout_token": token, "next": nxt})
    assert out.status_code == 302 and urlsplit(out.location).path == "/users/auth/login"
    assert "authorize" in out.location


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


def test_organization_request_typed_in_by_a_member(owner, db):
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    body = {"name": "Rauha ry", "email": "Info@Rauha.example", "links": ["rauha.example", "https://instagram.com/rauha"],
            "city": "Tampere", "business_id": "1234567-8", "description": "Rauhanmarsseja.",
            "applicant_name": "Puhelun soittaja", "applicant_role": "puheenjohtaja",
            "represents": True, "access": "organization_account"}
    created = []
    try:
        assert _org_requests(app.test_client(), claims, "create", body, auth=("x", "y")).status_code in (401, 403)
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        for bad in ({"name": ""}, {"links": []}, {"links": ["ei osoite"]}, {"email": "nope"}, {"business_id": "123"},
                    {"city": "Ei kaupunki"}, {"access": "own_account"}, {"access": "invite"},
                    {"access": "invite", "invite_email": "nope"}, {"email": "", "access": "organization_account"}):
            assert _org_requests(app.test_client(), claims, "create", {**body, **bad}).status_code == 400, bad
        response = _org_requests(app.test_client(), claims, "create", body)
        assert response.status_code == 201
        item = response.json["item"]
        created.append(ObjectId(item["id"]))
        row = db.organization_requests.find_one({"_id": created[0]})
        assert row["status"] == "received" and row["source"] == "hallinto-manual" and row["entered_by"] == subject
        assert row["organization"]["email"] == "info@rauha.example" and row["organization"]["city"] == "tampere"
        assert row["organization"]["links"] == ["https://rauha.example", "https://instagram.com/rauha"]
        assert "status_token_hash" not in row and row["case_pending"] is False
        listed = _org_requests(app.test_client(), claims, "list", {}).json["items"]
        assert any(i["id"] == item["id"] for i in listed)
        invite = _org_requests(app.test_client(), claims, "create",
                               {**body, "access": "invite", "invite_email": "PJ@rauha.example"})
        created.append(ObjectId(invite.json["item"]["id"]))
        assert invite.status_code == 201 and invite.json["item"]["invite_email"] == "pj@rauha.example"
        suggestion = _org_requests(app.test_client(), claims, "create", {**body, "represents": False, "access": None})
        created.append(ObjectId(suggestion.json["item"]["id"]))
        assert suggestion.status_code == 201 and suggestion.json["item"]["represents"] is False
    finally:
        db.organization_requests.delete_many({"_id": {"$in": created}})


def test_organization_request_status_mail_once(owner, db, monkeypatch):
    from datetime import datetime, timezone
    from mielenosoitukset_fi.users import hallinto_issuer
    app, subject = owner
    mails = []

    class Sender:
        def queue_email(self, **kwargs):
            mails.append(kwargs)

    monkeypatch.setattr(hallinto_issuer, "invite_sender", lambda: Sender())
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
    now = datetime.now(timezone.utc)
    rid = db.organization_requests.insert_one({
        "organization": {"name": "Rauha ry", "email": "info@rauha.example"},
        "applicant": {"name": "Hakija", "email": "hakija@example.test"}, "represents": True,
        "access": "organization_account", "status": "in_review", "status_token_hash": "x",
        "created_at": now, "updated_at": now}).inserted_id
    try:
        step = {"id": str(rid), "status": "rejected", "message": "Ei sopiva.", "case": "c1"}
        for _ in range(2):
            assert _org_requests(app.test_client(), claims, "update", step).status_code == 200
        stored = db.organization_requests.find_one({"_id": rid})
        assert len(stored["history"]) == 1
        assert len(mails) == 1 and mails[0]["recipients"] == ["hakija@example.test"]
        assert mails[0]["context"]["message"] == "Ei sopiva." and mails[0]["context"]["status"] == "rejected"
    finally:
        db.organization_requests.delete_one({"_id": rid})


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


def test_create_organization_for_approved_request(owner, db, monkeypatch):
    """Slice O4: an approved request becomes an organization with its owner access."""
    import io
    from datetime import datetime, timezone

    from mielenosoitukset_fi.users import hallinto_issuer
    from mielenosoitukset_fi.utils import s3

    sent, uploads = [], []
    monkeypatch.setattr(hallinto_issuer, "_invite_sender",
                        type("Sender", (), {"queue_email": lambda self, **kw: sent.append(kw)})())
    monkeypatch.setattr(s3, "upload_image_fileobj",
                        lambda bucket, fileobj, name, kind: uploads.append((bucket, fileobj.read())) or "https://s3.test/logo.jpg")
    app, subject = owner
    app.config["S3_BUCKET"] = "bucket"
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    now = datetime.now(timezone.utc)

    def request(**extra):
        row = {"organization": {"name": "Rauhan ystävät ry", "email": "info@rauha.example",
                                "links": ["https://rauha.example"], "city": "tampere", "description": "Rauhaa."},
               "applicant": {"name": "Hakija", "email": "hakija@example.test"}, "represents": True,
               "access": "organization_account", "status": "approved", "hallinto_case": "case-1",
               "logo": {"content_type": "image/png", "data": b"\x89PNGdata", "size": 8},
               "status_token_hash": "secret", "created_at": now, "updated_at": now, **extra}
        return db.organization_requests.insert_one(row).inserted_id

    created_requests, created_orgs, created_users = [], [], []
    try:
        first = request()
        created_requests.append(first)
        call = lambda rid, case="case-1": _org_requests(app.test_client(), claims, "create-organization",
                                                         {"id": str(rid), "case": case})
        # Needs an admin with the right to create organizations, and an approved request of that case.
        assert call(first).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin", "global_permissions": ["CREATE_ORGANIZATION"]}})
        assert call(first, "other-case").status_code == 409
        in_review = request(status="in_review")
        suggestion = request(represents=False)
        created_requests += [in_review, suggestion]
        assert call(in_review).json["error"] == "not_approved" and call(suggestion).json["error"] == "not_approved"

        # An organization account: the organization, its logo, a new account as owner and one mail.
        result = call(first)
        assert result.status_code == 200 and result.json["access"] == "organization_account" and result.json["logo"] == "copied"
        org = db.organizations.find_one({"_id": ObjectId(result.json["organization_id"])})
        created_orgs.append(org["_id"])
        assert org["name"] == "Rauhan ystävät ry" and org["verified"] is True and org["logo"] == "https://s3.test/logo.jpg"
        assert org["website"] == "https://rauha.example"
        assert set(org) <= {"_id", "name", "description", "email", "website", "logo", "social_media_links", "members",
                            "verified", "invitations"}
        account = db.users.find_one({"email": "info@rauha.example"})
        created_users.append(account["_id"])
        assert account["account_kind"] == "organization" and account["created_via"] == "organization_request"
        assert db.memberships.find_one({"user_id": account["_id"], "organization_id": org["_id"]})["role"] == "owner"
        assert len(sent) == 1 and sent[0]["recipients"] == ["info@rauha.example"] and uploads == [("bucket", b"\x89PNGdata")]
        # A repeat changes nothing: one organization, one account, one mail, one upload.
        again = call(first)
        assert again.status_code == 200 and again.json["organization_id"] == str(org["_id"])
        assert db.organizations.count_documents({"_id": org["_id"]}) == 1
        assert db.users.count_documents({"email": "info@rauha.example"}) == 1
        assert db.memberships.count_documents({"organization_id": org["_id"]}) == 1
        assert len(sent) == 1 and len(uploads) == 1

        # The address already has an account: nothing is created, so the access choice can change.
        taken = request(organization={"name": "Toinen ry", "email": "info@rauha.example", "links": []})
        created_requests.append(taken)
        refused = call(taken)
        assert refused.status_code == 409 and refused.json["error"] == "email_in_use"
        assert db.organization_requests.find_one({"_id": taken}).get("organization_id") is None

        # An invitation: the owner role is marked as granted by the Hallinto case, and mailed once.
        invite = request(access="invite", invite_email="pj@vanha.example",
                         organization={"name": "Vanha ry", "email": "x@vanha.example", "links": []}, logo=None)
        created_requests.append(invite)
        sent.clear()
        result = call(invite)
        assert result.status_code == 200 and result.json["owner_state"] == "invited" and result.json["logo"] == "none"
        org2 = db.organizations.find_one({"_id": db.organization_requests.find_one({"_id": invite})["organization_id"]})
        created_orgs.append(org2["_id"])
        assert org2["invitations"][0]["role"] == "owner" and org2["invitations"][0]["role_granted_by"] == "hallinto:case-1"
        assert org2["invitations"][0]["email"] == "pj@vanha.example"
        assert len(sent) == 1 and "/accept_invite" in sent[0]["context"]["invite_url"]
        call(invite)
        assert len(db.organizations.find_one({"_id": org2["_id"]})["invitations"]) == 1 and len(sent) == 1

        # The applicant's own account becomes owner; a missing account is refused.
        own = request(access="own_account", applicant={"name": "Hakija", "email": "h@example.test", "user_id": ObjectId(subject)},
                      organization={"name": "Kolmas ry", "email": "x@kolmas.example", "links": []}, logo=None)
        lost = request(access="own_account", applicant={"name": "X", "user_id": ObjectId()},
                       organization={"name": "Neljäs ry", "email": "x@neljas.example", "links": []})
        created_requests += [own, lost]
        assert call(lost).json["error"] == "applicant_account_missing"
        assert call(own).status_code == 200
        org3 = db.organizations.find_one({"_id": db.organization_requests.find_one({"_id": own})["organization_id"]})
        created_orgs.append(org3["_id"])
        assert db.memberships.find_one({"user_id": ObjectId(subject), "organization_id": org3["_id"]})["role"] == "owner"

        # A logo store that is down is not fatal: the logo stays pending and a retry copies it.
        app.config["S3_BUCKET"] = None
        late = request(organization={"name": "Viides ry", "email": "x@viides.example", "links": []},
                       access="invite", invite_email="a@viides.example")
        created_requests.append(late)
        pending = call(late)
        assert pending.status_code == 200 and pending.json["logo"] == "pending"
        org4 = db.organizations.find_one({"_id": db.organization_requests.find_one({"_id": late})["organization_id"]})
        created_orgs.append(org4["_id"])
        assert "logo" not in org4
        app.config["S3_BUCKET"] = "bucket"
        assert call(late).json["logo"] == "copied"
        assert db.organizations.find_one({"_id": org4["_id"]})["logo"] == "https://s3.test/logo.jpg"
        # The list shows where each request stands, never the logo's bytes.
        item = next(i for i in _org_requests(app.test_client(), claims, "list", {}).json["items"] if i["id"] == str(first))
        assert item["organization_id"] == str(org["_id"]) and item["owner_state"] == "account"
    finally:
        db.organization_requests.delete_many({"_id": {"$in": created_requests}})
        db.organizations.delete_many({"_id": {"$in": created_orgs}})
        db.memberships.delete_many({"organization_id": {"$in": created_orgs}})
        db.users.delete_many({"_id": {"$in": created_users}})


def _applications(owner_client, claims, path, body, auth=(CLIENT, SECRET)):
    return owner_client.post(PREFIX + "/city-admin-applications/" + path, base_url=ORIGIN, auth=auth,
                             json={"sub": claims["sub"], "sid": claims["sid"], **body})


def test_city_admin_applications_for_hallinto(owner, db):
    from mielenosoitukset_fi.utils.time_utils import utcnow
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    now = utcnow()
    base = {"user_id": ObjectId(subject), "city_keys": ["tampere"], "about": "Haluan auttaa Tampereella.",
            "activism": {"organizations": "Paikallinen ryhmä", "experience": "some", "experience_details": ""},
            "references": "", "case_pending": True, "created_at": now, "updated_at": now}
    open_id = db.city_admin_applications.insert_one({**base, "status": "received"}).inserted_id
    withdrawn = db.city_admin_applications.insert_one({**base, "status": "withdrawn"}).inserted_id
    try:
        assert _applications(app.test_client(), claims, "list", {}).status_code == 403
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        assert _applications(app.test_client(), claims, "list", {}, auth=(CLIENT, "wrong")).status_code == 401
        items = _applications(app.test_client(), claims, "list", {}).json["items"]
        ids = {item["id"] for item in items}
        assert str(open_id) in ids and str(withdrawn) not in ids
        item = next(i for i in items if i["id"] == str(open_id))
        assert item["account"] == {"id": subject, "username": "hallinto-fixture-user", "name": "hallinto-fixture-user"}
        assert item["cities"] == [{"key": "tampere", "name": "Tampere"}] and item["experience"] == "Jonkin verran"
        assert "email" not in repr(item)

        step = {"id": str(open_id), "status": "in_review", "case": "case-1"}
        assert _applications(app.test_client(), claims, "update", step).json["item"]["case"] == "case-1"
        stored = db.city_admin_applications.find_one({"_id": open_id})
        assert stored["status"] == "in_review" and stored["case_pending"] is False
        assert stored["history"][0]["by"] == subject and stored["history"][0]["via"] == "hallinto"
        # Another case, an unknown status and a withdrawn application are refused.
        assert _applications(app.test_client(), claims, "update", {**step, "case": "case-2"}).status_code == 409
        assert _applications(app.test_client(), claims, "update", {**step, "status": "received"}).status_code == 400
        assert _applications(app.test_client(), claims, "update", {**step, "id": str(withdrawn)}).status_code == 409
        assert _applications(app.test_client(), claims, "update", {**step, "id": str(ObjectId())}).status_code == 404
        # The decision sticks; replaying it is harmless.
        declined = {**step, "status": "declined"}
        assert _applications(app.test_client(), claims, "update", declined).status_code == 200
        assert _applications(app.test_client(), claims, "update", declined).status_code == 200
        assert _applications(app.test_client(), claims, "update", {**step, "status": "approved"}).status_code == 409
        stored = db.city_admin_applications.find_one({"_id": open_id})
        assert stored["status"] == "declined" and stored["decided_by"] == subject and len(stored["history"]) == 2
    finally:
        db.city_admin_applications.delete_many({"_id": {"$in": [open_id, withdrawn]}})


def test_hallinto_sends_bans_and_precedents_to_flask(owner, db):
    app, subject = owner
    browser = app.test_client()
    _login(browser)
    claims = _identity(browser)
    items = [{"id": "ban-1", "kind": "organizer", "organizer": "Esimerkki ry", "reason": "R", "from": "2026-11-01", "until": ""},
             {"id": "pre-1", "kind": "demo", "organizer": "Mielenosoitus X", "organization_id": "0123456789abcdef01234567",
              "reason": "R", "from": "2026-11-01", "until": ""}]

    def send(body, auth=(CLIENT, SECRET)):
        return app.test_client().post(PREFIX + "/moderation-blocks/sync", base_url=ORIGIN, auth=auth,
                                      json={"sub": claims["sub"], "sid": claims["sid"], **body})

    try:
        assert send({"items": items}).status_code == 403  # Not an admin.
        db.users.update_one({"_id": ObjectId(subject)}, {"$set": {"role": "admin"}})
        assert send({"items": items}, auth=(CLIENT, "x" * 40)).status_code == 401
        assert send({"items": "no"}).status_code == 400
        assert send({"items": items}).json == {"stored": 2}
        assert send({"items": items}).json == {"stored": 2}
        assert db.moderation_blocks.count_documents({}) == 2
    finally:
        db.moderation_blocks.delete_many({})
        db.users.update_one({"_id": ObjectId(subject)}, {"$unset": {"role": ""}})

