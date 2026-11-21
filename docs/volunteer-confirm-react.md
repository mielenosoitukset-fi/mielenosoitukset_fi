# Campaign volunteer confirmation: React page plus Go POST

Status (20 November 2026): built, **dark**. Flask's `GET /kampanja/confirm/<token>` is still the
live route and is unchanged.

## Why

Flask confirms the signup in the GET itself. A mail scanner, link preview or browser prefetch that
opens the link confirms a signup nobody confirmed. The maintainer decided (TODO.md, "Token pages
that write on GET") to split such pages into a React page with a button and a Go POST that does
the write, so a GET never changes anything. This is the first of three; the other two are
`/auth/confirm_email/<token>` and `/orgs/accept_invite`.

## What changed

| Part | Where | State |
| --- | --- | --- |
| Go `POST /api/campaign-volunteers/confirm` | `go-api/volunteer_confirm.go` | off unless `VOLUNTEER_CONFIRM_GO_ENABLED=true` and public submissions are configured |
| React route `/kampanja/confirm/<token>` | `frontend/src/app/VolunteerConfirmScreen.tsx`, `volunteer-confirm.ts`, `routes.tsx`, `render.tsx` | rendered by the React host, but nothing routes to it |
| Apache rules | `deploy/apache/react-ssr-routing.conf` | commented out, with the activation note |

The page reads nothing: opening it only shows a button. No Go read, no database access, no analytics
beacon. It is `private, no-store`, `noindex, nofollow`, no canonical URL, `Referrer-Policy: no-referrer`.

## The POST

`POST /api/campaign-volunteers/confirm`, JSON `{"token": "<token>"}`, same origin only.

| Case | Status | Body |
| --- | --- | --- |
| Confirmed | 200 | `{"success":true,"status":"success"}` |
| Unknown or already used token, malformed token | 400 | `{"success":false,"status":"invalid"}` |
| Link older than `confirmation_expires` | 400 | `{"success":false,"status":"expired"}` (row left as it is) |
| Wrong method | 405 | `Allow: POST` |
| Foreign or missing `Origin`, `Sec-Fetch-Site: cross-site` | 403 | |
| More than 10 a minute or 60 an hour per client | 429 | `Retry-After` |
| Panic switch (`global`/`security`) on, or database failure | 503 | never a verdict about the link |
| Not JSON, unknown field, trailing data, body over 1 KiB | 400 | `VOLUNTEER_FORMAT` |

Parity with Flask `confirm_volunteer`: the token matches the stored SHA-256
`confirmation_token_hash` or an older document's plain `confirmation_token`; success sets
`confirmed: true` and removes `confirmation_token`, `confirmation_token_hash` and
`confirmation_expires`; the expiry comparison is the same (`expires < now` is expired, so equal is valid);
the outcomes and the 200/400 codes are the same.

Differences, all on purpose:

- The check and the write are **one atomic `findOneAndUpdate`**, so two clicks at once cannot both
  succeed. Flask does a read and then a write.
- Origin check, panic switch and a rate limit, like the other Go write routes. Flask's page had none.
  The limit shares the 10/min, 60/h numbers of the other confirmation links and uses the same
  `demo_submission_tokens` counters under the key prefix `volunteer-confirm:`.
- Go sends no mail and writes no other collection. Flask wrote no audit row for this action either.
- Flask answered a stored row without `confirmation_expires` with a 500. Go answers `expired`
  and changes nothing (the expiry condition cannot match; the follow-up read finds the row).

## Tests

- `go-api/volunteer_confirm_test.go`: table of outcomes (hashed, legacy plain, expiry equal to now,
  expired, unknown, empty, operator characters, over-long), replay, expired row left untouched,
  40 concurrent clicks give exactly one success, origin/cross-site/method/rate/panic/format guards,
  store failure is 503, flag off returns the next handler. `TestVolunteerConfirmMongoStore` runs the
  real update against a disposable MongoDB (`REMINDER_TEST_MONGO`).
- `tests/test_volunteer_confirm_client.py`: the page's request helper (verdict mapping, network and
  malformed answers never read as success or as a bad link).
- `scripts/verify_public_host.mjs`: the rendered page, headers, no confirm request on GET, 404 for
  malformed paths, and that the Apache rule is still commented out.

## Activation (later maintainer go; each step is separate)

1. Deploy this code (flag still off). Nothing changes.
2. Add `VOLUNTEER_CONFIRM_GO_ENABLED=true` to `/etc/mielenosoitukset-api/environment` and restart the Go API.
   Check: `POST` with a wrong token answers 400 `invalid`; `GET` answers 405.
3. Uncomment the four rules in `deploy/apache/react-ssr-routing.conf`, check `apachectl configtest`, reload.
4. Click one real confirmation link from a test signup (a mailbox of the maintainer's).

## Revert

Re-comment the four Apache rules and reload: Flask serves the old page again at once. Remove the
environment flag and restart Go to switch the POST off. No data migration, no schema change.

## Reviews requested

An independent **security SHIP** (data/auth: token handling, rate limit, origin, atomic update) and an
independent **visual SHIP** (the button page and the three outcomes, 390 px and 1440 px, both themes).
