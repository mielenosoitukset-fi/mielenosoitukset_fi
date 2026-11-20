"""Failed password checks are counted per username and per IP, on /login and /2fa_check."""

from mielenosoitukset_fi.users.BPs import auth as auth_module


def _wrong(client, path="/users/auth/2fa_check", username="alice"):
    return client.post(path, data={"username": username, "password": "WrongPassword1!"})


def test_mfa_check_stops_password_guessing_and_logs_failures(client, db):
    for _ in range(auth_module.LOGIN_FAILURES_PER_USERNAME):
        assert _wrong(client).get_json() == {"enabled": False, "valid": False}
    assert db.login_logs.count_documents({"username": "alice", "success": False}) >= auth_module.LOGIN_FAILURES_PER_USERNAME

    throttled = client.post("/users/auth/2fa_check", data={"username": "alice", "password": "UserPass1!"})
    assert throttled.status_code == 429
    assert throttled.get_json() == {"enabled": False, "valid": False, "throttled": True}

    # Neither form of login lets the correct password through while throttled.
    response = client.post("/users/auth/login", data={"username": "alice", "password": "UserPass1!"})
    assert response.status_code == 302 and "/users/auth/login" in response.headers["Location"]
    with client.session_transaction() as login_session:
        assert "_user_id" not in login_session


def test_throttle_rows_store_no_username_or_ip_in_clear(client, db):
    _wrong(client)
    rows = list(db.mfa_login_challenges.find({"kind": "login_rate_limit"}))
    assert rows and all(row["_id"].startswith("login-limit:") for row in rows)
    assert "alice" not in repr(rows) and "127.0.0.1" not in repr(rows)


def test_unknown_usernames_count_against_the_ip(client, db, monkeypatch):
    monkeypatch.setattr(auth_module, "LOGIN_FAILURES_PER_IP", 3)
    for name in ("nobody1", "nobody2", "nobody3"):
        assert _wrong(client, username=name).get_json() == {"enabled": False, "valid": False}
    assert client.post(
        "/users/auth/2fa_check", data={"username": "alice", "password": "UserPass1!"}
    ).status_code == 429


def test_password_login_counts_failures_too(client, db, monkeypatch):
    monkeypatch.setattr(auth_module, "LOGIN_FAILURES_PER_USERNAME", 2)
    _wrong(client, path="/users/auth/login")
    _wrong(client, path="/users/auth/login")
    client.post("/users/auth/login", data={"username": "alice", "password": "UserPass1!"})
    with client.session_transaction() as login_session:
        assert "_user_id" not in login_session


def test_a_correct_password_below_the_limit_still_signs_in(client, db):
    _wrong(client)
    ok = client.post("/users/auth/2fa_check", data={"username": "alice", "password": "UserPass1!"})
    assert ok.status_code == 200 and ok.get_json()["valid"] is True
