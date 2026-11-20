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

## The open security question: the signing key

Flask signs and verifies with its `SECRET_KEY`, which also signs the session cookie. The Go API
does not have it (it receives only an HMAC-derived `PUBLIC_SUBMIT_SECRET`), and
[auth-bridge.md](auth-bridge.md) chose not to move the key. Verifying these tokens in Go needs a key
that equals the one that signed them. Options for the maintainer and the security reviewer:

1. ~~Give Go the same value as `SECRET_KEY`~~ Rejected by the security review: a leak from Go would allow forging Flask sessions. Do not use it.
2. **Chosen: mint confirmation tokens with a derived key** (HMAC of `SECRET_KEY` with a fixed label) and let
   Flask accept both old and new tokens for an hour. Go then holds only the derived key. This changes
   Flask's token issuing, so it is a separate, reviewed PR; the Go verifier needs no change besides
   the key value.
3. Keep this route in Flask.

Option 2 is a separate small draft PR touching only Flask's token issue/verify side. `EMAIL_CONFIRM_JWT_KEY` stays unset until that PR is reviewed and live, so this route stays off. Nothing here sets or reads a production secret.

## Tests

`go-api/email_confirm_test.go`: verifier against the PyJWT fixtures (valid, legacy, leeway edge,
expired, future `iat`/`nbf`, wrong key, HS512, `alg: none`, tampered payload, no `exp`, wrong
purpose), outcome table with a fake store, 30 concurrent replays, the guard set, flag/key/short-key
off-by-default checks, and `TestEmailConfirmMongoStore` (canonical and legacy lookup, `+` and `.`
literal, a regex-looking address matches nothing) against a disposable MongoDB
(`REMINDER_TEST_MONGO`). `tests/test_email_confirm_client.py` covers the page helper;
`scripts/verify_public_host.mjs` covers the rendered page, headers and that Apache stays unrouted.

## Activation (later maintainer go; separate steps)

1. Decide the key (above) and get the security SHIP.
2. Put `EMAIL_CONFIRM_GO_ENABLED=true` and `EMAIL_CONFIRM_JWT_KEY=<key>` in
   `/etc/mielenosoitukset-api/environment`, restart Go. Check: a POST with garbage answers 400
   `invalid`; GET answers 405.
3. Uncomment the four Apache rules, `apachectl configtest`, reload.
4. Register a test account with a maintainer mailbox, click the mail link, press the button, log in.

## Revert

Re-comment the Apache rules and reload (Flask serves the old route again at once); remove the two
environment variables and restart Go. No data migration, no schema change.

## Reviews requested

Independent **security SHIP** (auth/data: key handling, token verification, lookup, guards) and
independent **visual SHIP** (button page and the four outcomes, 390 px and 1440 px, both themes).
