"""E-mail confirmation tokens are signed with a key derived from SECRET_KEY.

Old links (signed with SECRET_KEY itself) keep working for their hour. The derived
key lets a verify-only service hold it without being able to forge sessions or
password reset tokens.
"""
import hashlib
import hmac
from datetime import timedelta

import jwt


def _derive(secret):
    return hmac.new(secret.encode(), b"moso-email-confirmation-token-v1", hashlib.sha256).hexdigest()


def test_new_confirmation_tokens_use_the_derived_key(app):
    from mielenosoitukset_fi.utils.auth import confirmation_signing_key, generate_confirmation_token

    with app.app_context():
        secret = app.config["SECRET_KEY"]
        key = confirmation_signing_key()
        assert key == _derive(secret) and key != secret
        token = generate_confirmation_token("a@example.test")
        claims = jwt.decode(token, key, algorithms=["HS256"])
        assert claims["email"] == "a@example.test" and claims["purpose"] == "email_confirmation"
        # It is not signed with SECRET_KEY, so session or reset verification cannot accept it.
        try:
            jwt.decode(token, secret, algorithms=["HS256"])
        except jwt.InvalidSignatureError:
            pass
        else:
            raise AssertionError("a confirmation token must not verify under SECRET_KEY")


def test_both_token_generations_confirm_during_the_overlap_hour(app):
    from mielenosoitukset_fi.utils.auth import _generate_token, generate_confirmation_token, verify_confirmation_token

    with app.app_context():
        assert verify_confirmation_token(generate_confirmation_token("a@example.test")) == "a@example.test"
        old = _generate_token({"email": "a@example.test", "purpose": "email_confirmation"}, 3600)
        assert verify_confirmation_token(old) == "a@example.test"
        legacy = _generate_token({"email": "a@example.test"}, 3600)
        assert verify_confirmation_token(legacy) == "a@example.test"


def test_confirmation_verifier_still_refuses_what_it_refused(app):
    from mielenosoitukset_fi.utils.auth import (
        _generate_token, confirmation_signing_key, generate_confirmation_token, generate_reset_token,
        verify_confirmation_token, verify_reset_token,
    )
    from mielenosoitukset_fi.utils.time_utils import utcnow

    with app.app_context():
        key = confirmation_signing_key()
        # Another purpose, signed with the derived key.
        assert verify_confirmation_token(_generate_token({"email": "a@example.test", "purpose": "password_reset"}, 3600, key=key)) is None
        # A reset token, and a confirmation token offered as a reset token.
        assert verify_confirmation_token(generate_reset_token("a@example.test")) is None
        assert verify_reset_token(generate_confirmation_token("a@example.test")) is None
        # Expired, forged and garbage.
        expired = jwt.encode({"email": "a@example.test", "purpose": "email_confirmation", "exp": utcnow() - timedelta(minutes=5)}, key, algorithm="HS256")
        assert verify_confirmation_token(expired) is None
        forged = jwt.encode({"email": "a@example.test", "purpose": "email_confirmation", "exp": utcnow() + timedelta(hours=1)}, "x" * 40, algorithm="HS256")
        assert verify_confirmation_token(forged) is None
        assert verify_confirmation_token("not.a.token") is None
        # alg none is never accepted.
        unsigned = jwt.encode({"email": "a@example.test", "purpose": "email_confirmation"}, None, algorithm="none")
        assert verify_confirmation_token(unsigned) is None
