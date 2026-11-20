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


def test_legacy_key_fallback_stops_after_the_cutoff(app, monkeypatch):
    from mielenosoitukset_fi.utils import auth

    with app.app_context():
        old = auth._generate_token({"email": "a@example.test", "purpose": "email_confirmation"}, 3600)
        assert auth.verify_confirmation_token(old) == "a@example.test"
        after = auth.CONFIRMATION_LEGACY_KEY_UNTIL + timedelta(seconds=1)
        monkeypatch.setattr(auth, "utcnow", lambda: after)
        # The derived-key token (exp is real time, so check only the fallback decision).
        assert auth._legacy_confirmation_key_allowed() is False
        assert auth.verify_confirmation_token(old) is None
        monkeypatch.setattr(auth, "utcnow", lambda: auth.CONFIRMATION_LEGACY_KEY_UNTIL - timedelta(seconds=1))
        assert auth._legacy_confirmation_key_allowed() is True


def test_derivation_accepts_bytes_and_str_secrets():
    from mielenosoitukset_fi.utils.auth import derive_confirmation_key

    assert derive_confirmation_key("k" * 40) == derive_confirmation_key(b"k" * 40) == _derive("k" * 40)
    assert derive_confirmation_key("a" * 40) != derive_confirmation_key("b" * 40)


def test_write_confirmation_key_script(tmp_path, capsys):
    import os
    import stat
    import sys

    sys.path.insert(0, "scripts")
    import write_confirmation_key as script

    secret = "s3cret-" + "x" * 40
    config = tmp_path / "config.yaml"
    config.write_text(f"SECRET_KEY: {secret}\nOTHER: 1\n")
    env = tmp_path / "environment"
    env.write_text("KEEP=1\nEMAIL_CONFIRM_JWT_KEY=old\nALSO=2")
    os.chmod(env, 0o644)
    script.write_key(str(config), str(env))
    text = env.read_text()
    assert text == f"KEEP=1\nALSO=2\nEMAIL_CONFIRM_JWT_KEY={_derive(secret)}\n"
    assert secret not in text
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    # A missing target file is created, private.
    fresh = tmp_path / "new-environment"
    script.write_key(str(config), str(fresh))
    assert fresh.read_text() == f"EMAIL_CONFIRM_JWT_KEY={_derive(secret)}\n" and stat.S_IMODE(os.stat(fresh).st_mode) == 0o600
    # A weak or missing secret writes nothing.
    config.write_text("SECRET_KEY: short\n")
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        script.write_key(str(config), str(tmp_path / "never"))
    assert not (tmp_path / "never").exists()
    # The script's own output never contains the key.
    capsys.readouterr()
    config.write_text(f"SECRET_KEY: {secret}\n")
    sys.argv = ["write_confirmation_key.py", "--config", str(config), "--env-file", str(env)]
    script.main()
    out = capsys.readouterr()
    assert out.out.strip() == "written" and _derive(secret) not in out.out + out.err
