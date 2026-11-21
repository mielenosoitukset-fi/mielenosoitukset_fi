# E-mail confirmation: React page plus Go POST

Status (20 November 2026): built, **dark**. Flask's `GET /users/auth/confirm_email/<token>`
(`auth_bp`, prefix `/users/auth`) is still the live route and is unchanged. Second layer of the
stack that starts with [volunteer-confirm-react.md](volunteer-confirm-react.md); it needs an
**(approval)-grade security review** before anything is switched on.

## Why

Flask sets `users.confirmed = true` in the GET itself, so a mail scanner or link preview that opens
the registration mail's link confirms an account nobody confirmed. The React page now shows a
button; Go's POST does the write.

## What changed

| Part | Where | State |
| --- | --- | --- |
| Go `POST /api/account/confirm-email` | `go-api/email_confirm.go` | off unless `EMAIL_CONFIRM_GO_ENABLED=true`, `EMAIL_CONFIRM_JWT_KEY` (32+ bytes) and public submissions are set |
| React route `/users/auth/confirm_email/<token>` | `EmailConfirmScreen.tsx`, `email-confirm.ts`, `routes.tsx`, `render.tsx` | rendered by the React host, nothing routes to it |
| Apache rules | `deploy/apache/react-ssr-routing.conf` | commented out |

The page reads nothing on GET: `private, no-store`, `noindex, nofollow`, no canonical URL,
`Referrer-Policy: no-referrer`, no Go request, no analytics beacon.

## The POST

`POST /api/account/confirm-email`, JSON `{"token": "<jwt>"}`, same origin only (same guards as the
volunteer route: POST only, `Origin`/`Sec-Fetch-Site`, panic switch, 10/min and 60/h per client under
`email-confirm:`, 4 KiB body, unknown fields refused).

| Case | Status | `status` |
| --- | --- | --- |
| Valid token, account found (also on replay inside the hour) | 200 | `confirmed` |
| Bad signature, wrong algorithm, expired, wrong purpose, malformed | 400 | `invalid` |
| Valid token whose address has no account | 404 | `user-not-found` |
| Rate limit / panic / database failure | 429 / 503 / 503 | never a verdict about the link |

Parity with Flask `confirm_email` and `utils/auth.py`:

- Token: HS256 JWT with `email`, `purpose: "email_confirmation"`, `iat`, `exp` (1 hour). A token
  with another purpose (a password reset token) is refused; one with no purpose (issued before the
  claim existed) still works until it expires. Time checks use PyJWT's 10 second leeway for `exp`,
  `iat` and `nbf`. `go-api/testdata/email_confirm_tokens.json` holds 17 tokens signed by PyJWT
  together with what Flask's verifier answers for each; the Go test asserts the same answer.
- Account: lower-cased, case-folded `email_canonical`, else a case-insensitive match on `email`
  (the address is regex-escaped), then `$set confirmed: true`. Replaying a link is harmless and
  answers success again, as in Flask.
- Expired and forged tokens give the same answer, as Flask's one warning does. The unknown-account
  answer exists only after a valid signature, so it is no account-existence oracle.

Differences, on purpose:

- A token without `exp` is refused (Flask never mints one), and so is one whose address is empty after trimming (Flask answers "user not found"; Go answers `invalid` and never queries for an empty address).
- Go cannot touch the visitor's Flask session, so `session["registration_email"]` is not cleared.
  It only prefills the "check your mail" page.
- Flask answers with a redirect and a flash message; React shows the outcome and a login link.
- Flask wrote no audit row for this action; neither does Go.

## The signing key: derived key only, never `SECRET_KEY`

Flask signs and verifies with its `SECRET_KEY`, which also signs the session cookie and password reset tokens.
The Go API must **never** hold it (the security review of 20 November 2026 rejected that option, and
[auth-bridge.md](auth-bridge.md) chose not to move the key either). Instead Flask signs new confirmation tokens with
`HMAC-SHA256(SECRET_KEY, "moso-email-confirmation-token-v1")`, accepting old and new tokens for an hour (draft PR #1241,
which changes only Flask's token issue/verify side). `EMAIL_CONFIRM_JWT_KEY` is that derived value and nothing else; a
Go process holding it can verify confirmation links but cannot forge sessions or reset tokens.

- #1241 is merged and deployed; since 21 November 2026 the deploy writes `EMAIL_CONFIRM_JWT_KEY` (see Activation).
- The operator writes the derived key into the Go environment file with `sudo python3 scripts/write_confirmation_key.py`
  (from #1241): nothing on a command line or in shell history, mode 600, output only `written`
  (see `docs/token-lifecycle.md`).
- Rotating `SECRET_KEY` changes the derived key: run the script again and restart Go.

## Tests

`go-api/email_confirm_test.go`: verifier against the PyJWT fixtures (valid, legacy, leeway edge,
expired, future `iat`/`nbf`, wrong key, HS512, `alg: none`, tampered payload, no `exp`, wrong
purpose), outcome table with a fake store, 30 concurrent replays, the guard set, flag/key/short-key
off-by-default checks, and `TestEmailConfirmMongoStore` (canonical and legacy lookup, `+` and `.`
literal, a regex-looking address matches nothing) against a disposable MongoDB
(`REMINDER_TEST_MONGO`). `tests/test_email_confirm_client.py` covers the page helper;
`scripts/verify_public_host.mjs` covers the rendered page, headers and that Apache stays unrouted.

## Activation (later maintainer go; separate steps)

Done on 21 November 2026 with the maintainer's go, through a pull request and the normal deploy:

1. #1241 (derived key on the Flask side) is merged and deployed.
2. `deploy/go-api/configure_environment.py` writes `EMAIL_CONFIRM_GO_ENABLED=true` and
   `EMAIL_CONFIRM_JWT_KEY` with public submissions on, by default (`EMAIL_CONFIRM_GO: false` in
   `config.yaml` opts out). The key is the same derived value as `derive_confirmation_key`, computed on
   every deploy from `SECRET_KEY` in `config.yaml`; `SECRET_KEY` itself is never written to the Go
   environment. The environment file is rewritten on every deploy, so a key written by hand with
   `scripts/write_confirmation_key.py` would not last; the script stays for a manual repair.
3. The four Apache rules are active. `deploy/release_routing.sh` installs them only after Go answers
   `GET /api/account/confirm-email` with 405, so a Go API without the flag or key keeps the old routing.
4. After the deploy: register a test account with a maintainer mailbox, click the mail link, press the
   button, log in.

## Revert

Re-comment the Apache rules and deploy (Flask serves the old route again at once); set
`EMAIL_CONFIRM_GO: false` in `config.yaml` to drop the two
environment variables at the next deploy. No data migration, no schema change.

## Reviews requested

Independent **security SHIP** (auth/data: key handling, token verification, lookup, guards) and
independent **visual SHIP** (button page and the four outcomes, 390 px and 1440 px, both themes).
