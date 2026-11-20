# SEC-01: MFA login challenge acceptance (#901)

Recorded 20 November 2026 against `main` at `cce1bbac`, which is the code the
production deploy workflow ships. Read-only review plus a local test run; no
production data was read.

## What #901 promised, and where it is now

| Property | Code (`mielenosoitukset_fi/users/BPs/auth.py`) | Test (`tests/`) |
|---|---|---|
| The browser holds only an opaque handle, never the password | `_set_pending_mfa_login`: `secrets.token_urlsafe(32)` in the session, SHA-256 of it as the MongoDB `_id` | `test_mfa_login_uses_short_lived_server_side_pending_state` |
| Five-minute challenge | `MFA_PENDING_LOGIN_TTL_SECONDS = 300`, `expires_at` checked on every read and consume | `test_expired_pending_mfa_login_requires_password_again` |
| At most five code attempts, shared across copies of the cookie | `MFA_PENDING_LOGIN_MAX_ATTEMPTS = 5`, `$inc` on the server row | `test_pending_mfa_attempt_limit_cannot_be_reset_by_cookie_replay` |
| One successful use | `_consume_pending_mfa_login` sets `used: true` in one `find_one_and_update` | `test_copied_pending_cookie_cannot_replay_successful_mfa`, `test_parallel_pending_challenge_consumption_has_one_winner` |
| A password change revokes a verified first factor | `credential_version` (digest of the password hash) compared on read, then a compare-and-set on `users` before login | `test_password_change_revokes_pending_first_factor` and the four `test_reset_*` interleaving tests |
| Disabled, banned, unconfirmed or MFA-off accounts cannot finish | same filter on read and in the final compare-and-set | `test_password_and_mfa_login_reject_disabled_account` (`test_account_access.py`) |
| No-JavaScript login reaches the same MFA step | password form renders the code step | `test_password_login_without_javascript_renders_mfa_step` |
| The legacy route does not trust a session flag | `/verify_mfa` goes through the same proof | `test_legacy_mfa_flag_does_not_replace_first_factor_proof`, `test_verify_mfa_route_does_not_500` |

Added after #901: failed password checks on `/login` and `/2fa_check` are
limited per username and per address (#1261), in the same collection under
`login-limit:` ids. Those rows have no `used` field, so they can never match a
challenge lookup.

## Test run

```
python -m pytest -q tests/test_account_access.py tests/test_god_auth.py \
  tests/test_god_emergency.py tests/test_login_throttle.py tests/test_mfa_settings.py \
  tests/test_username_validation.py tests/test_account_mfa.py \
  tests/test_step_up_authentication.py tests/test_auth_login_redirects.py tests/test_login_page.py
180 passed, 3 skipped
```

MongoDB 7 in Docker. The 3 skips are browser redirect tests in
`test_auth_login_redirects.py` that need a Playwright Chromium build the test
container did not have; they do not cover the MFA challenge. CI runs the full
suite on every pull request.

## Not covered here

- A real authenticator app and a real browser session against production.
  That is a manual check: sign in to a test account with MFA on, enter one wrong
  code, then the right one, and confirm a second use of the same tab fails.
