"""Regenerates email_confirm_tokens.json with PyJWT, the library Flask signs and verifies with
(mielenosoitukset_fi/utils/auth.py). Run: python3 go-api/testdata/make_email_confirm_tokens.py
The key is a test value; no production secret is involved."""
import calendar, json, pathlib, time
import jwt

KEY = "test-key-test-key-test-key-test-key-0123"
NOW = calendar.timegm((2026, 11, 20, 10, 0, 0))
JAN = calendar.timegm((2026, 1, 1, 0, 0, 0))
FAR = calendar.timegm((2100, 1, 1, 0, 0, 0))

def sign(claims, key=KEY, algorithm="HS256"):
    return jwt.encode(claims, key, algorithm=algorithm)

good = {"email": "Maija@Example.com", "purpose": "email_confirmation", "iat": JAN, "exp": FAR}
tokens = {
    "valid": sign(good),
    "legacy_no_purpose": sign({"email": "maija@example.com", "iat": JAN, "exp": FAR}),
    "password_reset_purpose": sign({**good, "purpose": "password_reset"}),
    "expired": sign({**good, "exp": NOW - 3600}),
    "inside_leeway": sign({**good, "exp": NOW - 5}),
    "outside_leeway": sign({**good, "exp": NOW - 11}),
    "future_iat": sign({**good, "iat": NOW + 3600}),
    "not_before_future": sign({**good, "nbf": NOW + 3600}),
    "wrong_key": sign(good, key="another-key-another-key-another-key-00"),
    "hs512": jwt.encode(good, KEY, algorithm="HS512"),
    "no_exp": sign({"email": "maija@example.com", "purpose": "email_confirmation", "iat": JAN}),
    "no_email": sign({"purpose": "email_confirmation", "iat": JAN, "exp": FAR}),
    "email_not_string": sign({**good, "email": 5}),
    "unknown_user": sign({**good, "email": "nobody@example.com"}),
}
head, payload, sig = tokens["valid"].split(".")
tokens["tampered_payload"] = ".".join([head, tokens["unknown_user"].split(".")[1], sig])
import base64
tokens["alg_none"] = ".".join([base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode(), payload, ""])

def flask_verdict(token):
    """What utils/auth.py verify_confirmation_token returns, with the clock fixed at NOW."""
    try:
        data = jwt.decode(token, KEY, algorithms=["HS256"], leeway=10, options={"verify_exp": False, "verify_iat": False, "verify_nbf": False})
        # Time checks are repeated below against the fixed clock instead of the real one.
    except jwt.InvalidTokenError:
        return None
    exp, iat, nbf = data.get("exp"), data.get("iat"), data.get("nbf")
    if exp is not None and exp < NOW - 10: return None
    if iat is not None and iat > NOW + 10: return None
    if nbf is not None and nbf > NOW + 10: return None
    if data.get("purpose") not in (None, "email_confirmation"): return None
    return data.get("email")

expected = {name: flask_verdict(token) for name, token in tokens.items()}
pathlib.Path(__file__).with_name("email_confirm_tokens.json").write_text(json.dumps({"key": KEY, "now": NOW, "tokens": tokens, "flask_email": expected}, indent=1) + "\n")
