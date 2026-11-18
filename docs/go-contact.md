# Contact form in Go: `POST /contact`

Status: implemented and shipped dark on 17 November 2026. Nothing routes to it,
the flag is off, and Flask still handles every real request. Spec:
[go-write-api-proposal.md](go-write-api-proposal.md) section 1. Approved default
(maintainer, 17.11.2026): exact Flask parity, shipped off, activation per API only
with the maintainer's go.

## What it does

`go-api/contact.go` answers `POST /contact` with the same behaviour as Flask's
`contact()` in `basic_routes.py`, in the same order:

1. Quotas (Flask-Limiter parity, below). Over quota: 429.
2. Body bound: 16,384 bytes by default. Declared size over the bound, or an
   unknown-length body that reaches it: 413.
3. Form read like `request.form` (urlencoded or multipart, first value wins,
   query string ignored).
4. One read-only call to Flask `/api/session` (below) for the token, the
   enabled switch and the limits.
5. Form disabled: 302 with the "disabled" outcome, no token work.
6. Token not outstanding in the visitor's session, too early (under
   `CONTACT_MIN_FORM_SECONDS`), too old, or already used: 400.
7. The SHA-256 hash of the token is inserted into `contact_form_tokens` (`_id`
   = hash, `expires_at` = now + max age, same shape and TTL index as Flask). A
   duplicate means "already used": 400. This is what makes the token single use.
8. Honeypot `website` not blank: fake success, no mail, no counter.
9. Strip, Unicode-casefold the email, validate (name ≤ 120, email ≤ 254 and the
   ASCII regex, subject one of `Tuki`, `Yleinen kysymys`, `Palautetta`, `Muu`,
   message 10–5,000; lengths count characters). Invalid: 302 "invalid".
10. Render the support mail and insert it into `email_queue`. Only after the
    insert is acknowledged does the handler answer success. Flask's worker
    sends it; Go never sends mail and no test sends mail.
11. Best-effort `site_analytics` `contact_message` counter, only with
    `SITE_ANALYTICS_ENABLED`, no Do-Not-Track and no bot user agent.
12. 302 to `/contact` with `X-MF-Request-ID` and `Cache-Control: no-store`.

The global panic/lockdown check is the existing outer gate (any active `global`
or `security` record: 503 with `Retry-After: 60`).

### Token and session

Flask stays the session owner. The token lives in Flask's signed cookie, which Go
cannot read, so Go asks Flask through the existing auth bridge: `GET /api/session`
with the visitor's cookie and the token in the header `X-Contact-Form-Token`
(never in the URL, so it stays out of access logs). With `AUTH_BRIDGE_SCOPES` on,
Flask answers `contact_token: {valid, enabled, max_age_seconds, limits}`. The
check does not remove the token and does not change the session. Go never sets or
parses a cookie. Any bridge error is 503, never "invalid". Go ignores a token that
is not `[A-Za-z0-9_-]{1,256}` (real tokens are 43 characters) and never forwards it.

Because Go cannot change the cookie, the used token stays in the visitor's
session until it ages out. It cannot be reused: the MongoDB hash blocks it.

### Quotas

Go increments the same counters as Flask-Limiter so a switch in either direction
keeps the quotas: database `limits`, collection `counters`, document `_id`
`LIMITER/<ip>/contact/<n>/<m>/<unit>` (per address) and
`LIMITER/global/public-contact-ingress/<n>/<m>/<unit>` (global), with the same
atomic count/expiry update as the `limits` library. Defaults `3 per 10 minutes;
10 per day` per address and `25 per 10 minutes; 100 per day` global, in Flask's
order (shorter windows first, first breach stops). The address is the same trusted
client address as Flask (Cloudflare headers only from Cloudflare, last forwarded
address from the local proxy). Verified against the real `limits` library: Python
counts 1, Go continues at 2, Python continues at 3.

Not mirrored: Flask's default limits (86,400/day, 3,600/hour, 10/second), which
are looser than the contact quotas and so never fire first; and the UptimeRobot
exemption, since no monitor posts the form.

### Mail and the count

The mail is `emails/customer_support/new_ticket.html` ported to Go with Jinja's
escaping (`&amp; &lt; &gt; &#34; &#39;`). Golden fixtures in `go-api/testdata`
come from the real Jinja environment (`scripts/generate_contact_mail_fixtures.py`);
the Go test and a Python test both pin them, so changing the template fails a test
until the fixtures and Go template are updated together. The queue row has the
`EmailJob.to_dict()` shape (`status: pending`, `attempts: 0`, null sender, header
`X-MF-Request-ID`, a UUID `instance_id`); the worker claims by status, not producer.

## Deliberate differences from Flask

- **Feedback.** Flask sets a flash in its session; Go cannot write that session.
  Go redirects to `/contact?contact=sent|invalid|disabled`. The React contact screen shows the matching message from the query
  (`contact-route.ts`, same copy as the Flask flashes, minus the exclamation mark).
- **Failed queue insert** answers 503 (JSON). Flask answers success and retries
  in-process on a timer, with no receipt. The token stays consumed: the visitor
  reloads the form for a new one. An ambiguous write can still leave one mail in
  the queue; delivery is at least once, not exactly once.
- **Order edge:** an oversized body to a disabled form is 413 in Go, a redirect
  in Flask. Error bodies (400/413/429/503) are JSON, Flask's are HTML pages.
- No new schema, index or collection.

## Activation (needs the maintainer's go, typed in the activating session)

1. Merge and deploy with everything off (this change).
2. Mongo access to `limits`. Read-only check 17.11.2026 (Helsinki): production has
   `AUTH_BRIDGE_SCOPES: true`, a `limits` database with a `counters` collection and
   no contact counters yet. The Go API uses the same `MONGO_URI` as Flask
   (`configure_environment.py` copies it), and that user already holds `readWrite`
   on `limits`, `mielenosoitukset` and `mielenosoitukset_test_1`. So no grant should
   be needed; confirm with `connectionStatus` before activation. Only if the roles
   have changed, grant least privilege (the `limits` database only; run as a Mongo
   admin, part of the maintainer's activation go):

   ```javascript
   db.getSiblingDB("admin").grantRolesToUser("<go-api-user>", [{ role: "readWrite", db: "limits" }])
   ```

   Revert: `db.getSiblingDB("admin").revokeRolesFromUser("<go-api-user>", [{ role: "readWrite", db: "limits" }])`.
   Do not point `CONTACT_LIMITER_DATABASE` elsewhere: that would split the quotas from Flask.
3. Back up `config.yaml` and `/etc/mielenosoitukset-api/environment`.
4. Land the React feedback change (above).
5. `config.yaml`: `CONTACT_GO_ENABLED: true` (optionally `CONTACT_PER_IP_LIMIT`,
   `CONTACT_GLOBAL_LIMIT`, `CONTACT_MAX_REQUEST_BYTES`, which must equal Flask's).
   Re-run the deploy so `configure_environment.py` rewrites the Go environment;
   restart the API (not during the backup dumps).
6. Check with a scratch request that Go answers 302 and a queue row appears, then
   add the Apache rule:

   ```apache
   RewriteCond %{REQUEST_METHOD} "^POST$"
   RewriteRule "^/contact$" "http://127.0.0.1:8091/contact" [P,L]
   ```

## Revert

Remove the Apache rule and reload Apache: Flask handles POST again with the same
quotas, because both use the same counters. Optionally set `CONTACT_GO_ENABLED`
false and restart. Consumed token hashes and queued mail stay valid in both
directions. Nothing in the database needs restoring.

## Settings

| Setting | Where | Default |
| --- | --- | --- |
| `CONTACT_GO_ENABLED` | `config.yaml` → Go environment | off; needs the bridge |
| `CONTACT_PER_IP_LIMIT`, `CONTACT_GLOBAL_LIMIT` | same | Flask defaults |
| `CONTACT_MAX_REQUEST_BYTES` | same | 16384 |
| `CONTACT_LIMITER_DATABASE` | Go environment only | `limits` |

Name/email/message limits, the token age and the enabled switch come from Flask's
answer, so there is one source for them.

## Tests

`go test ./...` covers parity (validation matrix, quota keys and order, replay,
concurrency, honeypot, disabled, body bounds, multipart, failures, trusted
proxy address, analytics key, golden mail). With a disposable MongoDB, set
`CONTACT_TEST_MONGO` to run the counter, token and queue tests for real, and
`CONTACT_TEST_PYTHON` for the cross-check with the Python `limits` library.
`tests/test_contact_go_bridge.py` covers the Flask side.
