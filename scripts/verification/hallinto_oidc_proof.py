"""Synthetic Authlib protocol probe. Run with pytest in an isolated venv.

The Flask application is fixture-only and has no real account, storage or routes.
"""

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from time import time
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import AuthorizationCodeMixin, ClientMixin
from authlib.oauth2.rfc6749.errors import InvalidRequestError, OAuth2Error
from authlib.oauth2.rfc6749.grants import AuthorizationCodeGrant
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oidc.core import UserInfo
from authlib.oidc.core.grants import OpenIDCode
from flask import Flask
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

ISSUER = "https://issuer.example.test"
CALLBACK = "https://hallinto.example.test/callback"
SUBJECT = "000000000000000000000001"
CLIENT_ID = "synthetic-hallinto"
CLIENT_SECRET = "synthetic-fixture-only"
AUTH_TIME = 1_700_000_000


class Client(ClientMixin):
    def get_client_id(self):
        return CLIENT_ID

    def get_default_redirect_uri(self):
        return None

    def get_allowed_scope(self, scope):
        return "openid" if scope == "openid" else None

    def check_redirect_uri(self, redirect_uri):
        return redirect_uri == CALLBACK

    def check_client_secret(self, client_secret):
        return compare_digest(client_secret, CLIENT_SECRET)

    def check_endpoint_auth_method(self, method, endpoint):
        return endpoint == "token" and method == "client_secret_basic"

    def check_response_type(self, response_type):
        return response_type == "code"

    def check_grant_type(self, grant_type):
        return grant_type == "authorization_code"


@dataclass
class Code(AuthorizationCodeMixin):
    redirect: str
    nonce: str
    code_challenge: str
    code_challenge_method: str

    def get_redirect_uri(self):
        return self.redirect

    def get_scope(self):
        return "openid"

    def get_nonce(self):
        return self.nonce

    def get_auth_time(self):
        return AUTH_TIME

    def get_acr(self):
        return None

    def get_amr(self):
        return None


class S256Required(CodeChallenge):
    def validate_code_challenge(self, grant, redirect_uri):
        payload = grant.request.payload.data
        if not payload.get("code_challenge") or payload.get("code_challenge_method") != "S256":
            raise InvalidRequestError("S256 required")
        super().validate_code_challenge(grant, redirect_uri)


@pytest.fixture
def provider():
    app = Flask(__name__)
    app.config["OAUTH2_SCOPES_SUPPORTED"] = ["openid"]
    auth_server = AuthorizationServer(
        app,
        query_client=lambda value: Client() if value == CLIENT_ID else None,
        save_token=lambda *_: None,
    )
    codes = {}
    nonces = set()
    keys = {"first": RSAKey.generate_key(2048, parameters={"kid": "first"}, private=True)}
    current = ["first"]

    class Grant(AuthorizationCodeGrant):
        def save_authorization_code(self, code, request):
            payload = request.payload.data
            nonces.add(payload["nonce"])
            codes[code] = Code(payload["redirect_uri"], payload["nonce"],
                               payload["code_challenge"], payload["code_challenge_method"])

        def query_authorization_code(self, code, client):
            return codes.get(code)

        def delete_authorization_code(self, authorization_code):
            key = next(key for key, value in codes.items() if value is authorization_code)
            del codes[key]

        def authenticate_user(self, authorization_code):
            return SUBJECT

    class Identity(OpenIDCode):
        def exists_nonce(self, nonce, request):
            return nonce in nonces

        def get_client_claims(self, client):
            return {"iss": ISSUER, "aud": [CLIENT_ID], "exp": int(time()) + 120}

        def get_encode_header(self, client):
            return {"alg": "RS256", "kid": current[0]}

        def resolve_client_private_key(self, client):
            return keys[current[0]]

        def generate_user_info(self, user, scope):
            return UserInfo(sub=user)

    auth_server.register_grant(Grant, extensions=[S256Required(), Identity(require_nonce=True)])

    @app.get("/.well-known/openid-configuration")
    def discovery():
        return {
            "issuer": ISSUER,
            "authorization_endpoint": ISSUER + "/authorize",
            "token_endpoint": ISSUER + "/token",
            "jwks_uri": ISSUER + "/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    @app.get("/jwks")
    def jwks():
        return {"keys": [key.as_dict(private=False) for key in keys.values()]}

    @app.get("/authorize")
    def authorize():
        # The synthetic account acts as an already authenticated test subject.
        request = auth_server.create_oauth2_request(None)
        try:
            grant = auth_server.get_consent_grant(request, end_user=SUBJECT)
        except OAuth2Error as error:
            return auth_server.handle_error_response(request, error)
        return auth_server.create_authorization_response(request, grant_user=SUBJECT, grant=grant)

    @app.post("/token")
    def token():
        return auth_server.create_token_response()

    return app.test_client(), keys, current


def request_params():
    verifier = token_urlsafe(42)
    challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, {
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK,
        "scope": "openid",
        "response_type": "code",
        "nonce": token_urlsafe(20),
        "state": token_urlsafe(20),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }


def authorize(client, params):
    return client.get("/authorize", base_url=ISSUER, query_string=params)


def exchange(client, code, verifier, redirect=CALLBACK):
    return client.post("/token", base_url=ISSUER, auth=(CLIENT_ID, CLIENT_SECRET), data={
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect,
    })


def authorization_code(response, expected_state):
    values = parse_qs(urlsplit(response.location).query)
    if not compare_digest(values.get("state", [""])[0], expected_state):
        raise ValueError("unexpected callback state")
    assert response.status_code == 302
    assert response.location.startswith(CALLBACK + "?")
    return values["code"][0]


def validate_identity(raw_token, public_keys, nonce, audience=CLIENT_ID, issuer=ISSUER):
    from joserfc.jwk import KeySet

    claims = jwt.decode(raw_token, KeySet.import_key_set(public_keys), algorithms=["RS256"]).claims
    assert claims["iss"] == issuer
    assert claims["aud"] == [audience]
    assert compare_digest(claims["nonce"], nonce)
    assert claims["iat"] <= time() < claims["exp"]
    return claims


def test_oidc_code_exchange_and_rotation(provider):
    client, keys, current = provider
    metadata = client.get("/.well-known/openid-configuration", base_url=ISSUER).json
    assert metadata["issuer"] == ISSUER and metadata["jwks_uri"] == ISSUER + "/jwks"
    public_keys = client.get("/jwks", base_url=ISSUER).json
    assert all("d" not in key for key in public_keys["keys"])
    verifier, params = request_params()
    response = authorize(client, params)
    with pytest.raises(ValueError, match="unexpected callback state"):
        authorization_code(response, "wrong")
    code = authorization_code(response, params["state"])
    assert exchange(client, code, token_urlsafe(42)).status_code == 400
    assert exchange(client, code, verifier, CALLBACK + "/").status_code == 400
    token = exchange(client, code, verifier).json["id_token"]
    claims = validate_identity(token, public_keys, params["nonce"])
    assert claims["sub"] == SUBJECT and claims["auth_time"] == AUTH_TIME
    assert exchange(client, code, verifier).status_code == 400
    for kwargs in ({"audience": "other"}, {"issuer": "https://other.example.test"}):
        with pytest.raises(AssertionError):
            validate_identity(token, public_keys, params["nonce"], **kwargs)
    with pytest.raises(AssertionError):
        validate_identity(token, public_keys, "wrong")

    keys["second"] = RSAKey.generate_key(2048, parameters={"kid": "second"}, private=True)
    current[0] = "second"
    verifier, next_params = request_params()
    code = authorization_code(authorize(client, next_params), next_params["state"])
    next_token = exchange(client, code, verifier).json["id_token"]
    with pytest.raises(JoseError):
        validate_identity(next_token, public_keys, next_params["nonce"])
    overlap = client.get("/jwks", base_url=ISSUER).json
    assert validate_identity(next_token, overlap, next_params["nonce"])["sub"] == SUBJECT
    assert validate_identity(token, overlap, params["nonce"])["sub"] == SUBJECT
    del keys["first"]
    with pytest.raises(JoseError):
        validate_identity(token, client.get("/jwks", base_url=ISSUER).json, params["nonce"])


@pytest.mark.parametrize("field", ["nonce", "code_challenge", "code_challenge_method", "redirect_uri"])
def test_incomplete_or_unsafe_authorize_request(provider, field):
    client, _, _ = provider
    _, params = request_params()
    if field == "redirect_uri":
        params[field] = CALLBACK + "/"
    elif field == "code_challenge_method":
        params[field] = "plain"
    else:
        del params[field]
    response = authorize(client, params)
    assert response.status_code == 400 or "error=" in (response.location or "")
    assert not response.location or "code=" not in response.location
