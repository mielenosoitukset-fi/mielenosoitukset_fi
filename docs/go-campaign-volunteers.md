# Campaign volunteer signup in Go: `POST /kampanja/api/volunteers`

Status: implemented and shipped dark (20 November 2026). Nothing routes to it, the flag is off, and Flask handles every
real request. Spec: [go-write-api-proposal.md](go-write-api-proposal.md) section 4; form and data model:
[decisions.md](decisions.md) ("Volunteer form data model"). Pattern: [go-contact.md](go-contact.md).

## What it does

`go-api/campaign_volunteer.go` answers the signup with the behaviour of Flask's `add_volunteer()` in
`mielenosoitukset_fi/kampanja/routes.py`, in the same order:

1. Method and in-flight cap (8). The global lockdown gate is the existing outer check.
2. **Publication gate.** `campaign_settings` `_id: "public_pages"`; only `published: true` is public, a failed read is private.
   Private: a signed-in Flask account (asked through the auth bridge, `scope` none) may sign up, anyone else gets 401 with
   Flask's message. A bridge error is 503, never "allowed". Headers as Flask: `Cache-Control: private, no-store`,
   `Vary: Cookie`, `X-Robots-Tag: noindex, nofollow` while private.
3. Body: at most 16 KiB (413 over). JSON read like `get_json(silent=True)`: JSON content type, valid UTF-8, an object; anything else
   counts as no data and fails step 4 with the "required" message.
4. Fields: name and email required (400 "Nimi ja sähköpostiosoite ovat pakollisia."); format characters (category Cf) removed and
   Python-style `strip()`; email `^[^@\s]+@[^@\s]+\.[^@\s]+$` and at most 254 characters; name 120, city 100, area 200, notes 2000
   (characters); `roles` a list of `stickers`, `pride_banner`, `events`, `communications` (null, a string, unknown or non-string
   items: 400 "Tarkista korostetut tiedot ja yritä uudelleen."); roles stored in canonical order without duplicates; area kept only
   with `stickers`. Unknown keys, including a phone number, are ignored and never stored.
5. Rate limits, same documents as Flask in `volunteer_signup_attempts` (`{key, at}`, key `kind:` + SHA-256 of the lower-cased value):
   5 per client address and hour (429 "Yhteys palveluun epäonnistui. Yritä myöhemmin uudelleen."); then 2 per address and hour, then 3 per
   address and day, either of which answers a silent 201 with nothing stored or mailed. Daily key is not touched when the hourly one trips.
   Attempts older than a day are deleted.
6. Unconfirmed signups whose link expired over a week ago are deleted (only for a signup that gets stored, as in Flask).
7. Insert the `volunteers` document: `name, email, city, roles, area, notes, confirmed: false, confirmation_token_hash, confirmation_expires`
   (now + 24 hours). The token is 16 random bytes, URL-safe base64 (22 characters) like `secrets.token_urlsafe(16)`; only its SHA-256 is stored.
   Retention is unchanged: `purge_campaign_volunteers` deletes everything from 1.6.2027 00:00 Helsinki.
8. Best-effort `site_analytics` event `volunteer_signup` (resource = city, at most 200 characters) with the same rules as Flask's helper
   (only with `SITE_ANALYTICS_ENABLED`, no Do-Not-Track, no bot).
9. Render `kampanja/welcome.html` (Jinja escaping ported; golden fixture `go-api/testdata/campaign_volunteer_welcome.json` made with the real
   Jinja environment and pinned by `tests/test_campaign_volunteer_go_mail.py` and the Go test) and insert it into `email_queue` in the
   `EmailJob.to_dict()` shape. Flask's worker sends it; Go never sends mail and no test sends mail. Only then 201 `{"success": true}`.

## Deliberate differences from Flask

- **Rate counting order.** Go records the attempt and then counts (Flask counts, then records), so concurrent requests can only be counted
  too often, never let extra ones through. Flask's check-then-insert can admit a burst; Go cannot.
- **Failure handling.** A storage or queue failure answers 503 JSON. If the mail cannot be queued the stored signup is deleted again (best
  effort), because an unmailed signup is unreachable. Flask answers 201 and retries the mail on a timer. An ambiguous queue write can still
  leave one mail.
- **Body bound.** 16 KiB (413). Flask has no bound. The form's largest legal payload is under 6 KiB.
- **Flask-Limiter defaults** (86,400/day, 3,600/hour, 10/second) are not mirrored: looser than the limits above, so they never fire first.
- **Language.** Messages are Finnish; Flask would translate for other locales. The public site is Finnish-only.
- Python `str.lower()` and Go `strings.ToLower` differ for a few special characters (for example `İ`), so such an address may count under a
  different hash key than Flask would. Cosmetic: the limit still applies per runtime.
- Attempts stay counted when a signup fails with 503, as in Flask. After two failed tries in an hour, a third try for the same address
  gets the silent 201 and no mail; the visitor can try again after the hour.
- Lone surrogates written as `\ud800` in the JSON become U+FFFD in Go; Flask would fail storing them.

## Confirm link: how this interacts with `token-pages-go`

`GET /kampanja/confirm/<token>` (write on GET) is **not touched here and has no Go route in this PR.** The same file contains
`confirmCampaignVolunteer(ctx, store, token, now)` which is the whole confirm logic without HTML, for the token pages work to call
from its route (names are prefixed `campaign…`/`confirmCampaignVolunteer`; nothing else is shared):

- `invalid` for a token outside `[A-Za-z0-9_-]{1,256}` (no query), unknown, or already used; `expired` when found but `confirmation_expires`
  is strictly before now (exactly at the instant is still valid, like Flask); `success` otherwise; a database error is an error, never "invalid".
- One conditional `UpdateOne` (token hash **or** legacy plaintext `confirmation_token`, and `confirmation_expires >= now`) sets `confirmed` and
  unsets the three token fields. Two simultaneous clicks confirm once; the loser reads "invalid", as a sequential replay does in Flask.
- Both directions work: Go-created signups store the same hash Flask looks up, and Flask's old plaintext tokens still confirm.
- The caller must keep tokens out of logs and analytics (the existing Flask analytics classifier already skips confirm paths) and decide
  HEAD/prefetch behaviour; this code has none. Recommended index for the lookup (not created here):
  `db.volunteers.createIndex({confirmation_token_hash: 1}, {name: "confirmation_token_hash", sparse: true})`; check for slow scans first.

If the token pages session wants a different signature, change it freely; the Go tests for it live in `campaign_volunteer_test.go`.

## Settings

| Setting | Where | Default |
| --- | --- | --- |
| `CAMPAIGN_VOLUNTEERS_GO_ENABLED: true` | `config.yaml`; `deploy/go-api/configure_environment.py` writes it to the Go environment, only with `AUTH_BRIDGE_SCOPES: true` | off |
| `SITE_ANALYTICS_ENABLED` | existing | as today |

No new schema, index or collection. Needs: the Go API user can write `volunteers`, `volunteer_signup_attempts`, `email_queue`,
`site_analytics` in the main database (the contact and suggestion routes already write the last two; confirm `volunteers` and
`volunteer_signup_attempts` with `connectionStatus` before activation).

## Activation (needs the maintainer's go, typed in the activating session)

1. Merge and deploy with everything off (this change). Re-check that the PR's tests are green on `main`.
2. Check Go's Mongo user can write `volunteers` and `volunteer_signup_attempts` (`readWrite` on the main database is expected).
3. Back up `config.yaml` and `/etc/mielenosoitukset-api/environment`.
4. `config.yaml`: `CAMPAIGN_VOLUNTEERS_GO_ENABLED: true` (with `AUTH_BRIDGE_SCOPES: true`). Re-run the deploy so the Go environment is
   rewritten; restart the API.
5. Test with a scratch address on a private campaign and a published one, directly against `127.0.0.1:8091` (a 201 and a pending
   `email_queue` row; confirm that Flask's `/kampanja/confirm/<token>` accepts the emailed link). Delete the test rows afterwards.
6. Add the Apache rule (not active in this PR; it is not in `deploy/apache`):

   ```apache
   RewriteCond %{REQUEST_METHOD} "^POST$"
   RewriteRule "^/kampanja/api/volunteers$" "http://127.0.0.1:8091/kampanja/api/volunteers" [P,L]
   ```

   Reload Apache. The React form posts same-origin, so nothing else changes.

## Revert

Remove the Apache rule and reload: Flask handles POST again with the same limiter documents, volunteer fields and hashed tokens. Optionally
set `CAMPAIGN_VOLUNTEERS_GO_ENABLED` false and restart. Signups and queued mail made by Go stay valid in both directions. Nothing in the
database needs restoring.

## Tests

`go test ./...` covers: the validation matrix (parity with `tests/test_campaign_volunteer_*`), field cleaning, roles and area, document
shape and key order, hashed token matching the mailed link, publication gate (private anonymous/signed-in, read failure, bridge failure),
per-address and per-day limits and key hashing, silent repeats, the hourly/daily short circuit, purge behaviour, storage failures and
rollback, body bound, analytics gating, welcome mail golden, confirm statuses, concurrent signups and concurrent confirmations.
`go-api/testdata/campaign_volunteer_fields.json` holds about forty cleaning and validation cases (Unicode whitespace, format characters,
code-point lengths, email shapes, role lists); `tests/test_campaign_volunteer_go_fields.py` pins it to Flask's `_volunteer_fields()` and
`TestVolunteerFieldsMatchFlaskFixture` runs it through the Go parser. The real-store test (signup, attempts, atomic confirm, legacy token,
expiry, purge) runs whenever `TEST_MONGODB_URI` is set, as in CI (`CAMPAIGN_VOLUNTEER_TEST_MONGO` still works locally). `tests/test_go_api_environment.py` covers the flag mapping.
