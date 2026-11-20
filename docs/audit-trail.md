# Audit trail: every write leaves a trace

Maintainer request (Olivia, 20 November 2026): "every edit, no matter how it is
done, should leave a trace so no untraceable edits can happen."

## How it works

The trail sits in the MongoDB driver, not in the write handlers. Every client
the code base opens carries an audit listener:

- Python (Flask, background jobs, systemd scripts, migrations):
  `mielenosoitukset_fi/utils/audit_trail.py`. `DatabaseManager` and every
  script build their client with `audit_trail.mongo_client()`.
- Go API: `go-api/audit_trail.go`. `main.go` attaches `audit.monitor()` to the
  only client it opens.

The driver reports each command; inserts, updates, deletes, findAndModify,
`$out`/`$merge` aggregations and schema commands (create, drop, rename,
index and collMod) become one document in `audit_events` of the database
that was written:

| Field | Content |
| --- | --- |
| `v` | schema version (1) |
| `at` | UTC time |
| `db`, `collection`, `op` | where and what kind (`insert`, `update`, `delete`, `aggregate_out`, `ddl:<command>`) |
| `doc_ids` | `_id`s from the filter or the inserted documents |
| `filter` | the query; string values only under id-like keys (`_id`, `*_id`, `slug`, `status`, `type`, `kind`, `key`), other strings become `[str]` so a lookup by email does not copy the address |
| `change` | update operators (the diff that was applied) or the inserted documents; secrets redacted |
| `actor` | `user` + `user_id`, `api_token`/`mcp` + token id, `link` (organiser or confirmation link), `anonymous`, `job` + name, or `system`. Never an email, name or IP |
| `via` | `http` + service, method, endpoint and route (a long opaque path segment becomes `:token`), or `job` + name and run id, or `process` + script and thread; plus host and pid |
| `request_id` | groups the writes of one request |
| `outcome` | matched/modified counts, upserted ids, write-error codes |
| `duration_ms` | driver time |

Writes that matched nothing (polling claims, a delete of something already
gone) are not events. Failed writes are.

### Never logged

Values under secret-looking keys (password, hash, secret, token, cookie,
session, otp, recovery, api key, authorization, credential, private, signature,
nonce, challenge, seal, code), network identifiers (`ip`, `*_ip`, IP
address, remote address, user agent, forwarded headers, and IP-shaped strings,
which keep their own shorter retention elsewhere), strings shaped like password
hashes, JWTs or Fernet tokens, and every non-id value of the credential collections (`api_tokens`,
`magic_links`, `mfas`, `mfa_login_challenges`, `user_passkeys`,
`passkey_challenges`, `fresh_step_up_tokens`, `used_password_change_tokens`,
`demo_cancellation_tokens`, `demo_submission_tokens`, `contact_form_tokens`,
`hallinto_sessions`, `oauth_authorization_codes`, `erasure_identity_seals`).
Long strings are cut at 300 characters, lists at 50 items. Both services run
the same redaction vectors (`go-api/testdata/audit_redaction.json`).

### Not recorded, on purpose

High-volume counters and lock heartbeats are not edits of anyone's data, and
auditing them would double the database load:

- `audit_events`: the trail itself.
- `analytics`, `d_analytics`, `prepped_analytics`: demonstration view counters and their roll-ups.
- `site_analytics`, `site_analytics_visitors`, `web_vitals_daily`: page view and performance counters.
- `api_usage`: per-request API counters.
- `background_job_leader`: the job leader lock, refreshed every few seconds.

Inserts into append-only logs (`admin_logs`, `super_audit_logs`,
`demo_audit_logs`, `login_logs`, `board_audit_logs`, `demo_edit_history`,
`background_job_runs`, `token_usage_logs`) are records already and are not
duplicated; updates and deletes of those logs are recorded, so a log cannot be
rewritten quietly.

### Failure handling

An event is written synchronously after the write succeeds (Python, a separate
client with 2 s timeouts) or by a single writer goroutine (Go, queue of 2048,
drained at shutdown). If the database refuses the event, the full event is
written to the service journal as an `AUDIT_FALLBACK` line instead. The trail
never fails or undoes the write it describes.

### Retention and privacy

`audit_events` has a TTL index on `at`: 730 days by default
(`AUDIT_RETENTION_DAYS`, minimum 30), the same default as account erasure.
Changing the period on a running database needs `collMod` on the
`audit_retention` index; the code does not change an existing index.
The privacy notice already lists change logs ("Hallinta-, muutos- ja
virhelokit"). Account erasure (#1268) must pseudonymise `audit_events` like the
other audit logs: `actor.user_id`, the account's own `doc_ids`, and any email or
name inside `change`.

Switch: `AUDIT_TRAIL_DISABLED=1` turns the trail off for one process (logged as
a warning). It is for emergencies only.

## Keeping it complete

`tests/test_audit_trail_coverage.py` fails when:

- Python code outside `audit_trail.py` constructs `MongoClient`, an async client
  or Motor;
- Go code outside `main.go` calls `mongo.Connect`, or `main.go` loses the monitor;
- a skipped collection is not documented here, or Go and Python skip different
  collections.

So a new write path cannot skip the trail without failing CI: any new handler,
job or script that writes through the shared clients is recorded automatically.
Jobs should still name themselves with `audit_scope("job", name)` (the
background job manager does it for every registered job), and Go background
loops with `withAuditJob(ctx, name)`.

## What it cannot see

Writes made outside this code base: `mongosh` or Compass on the server, a
backup restore, another application with database credentials. Closing that
gap needs MongoDB itself (a change stream follower that flags writes without a
matching audit event, which needs a replica set, or the Enterprise audit log).
That is a follow-up.

## Inventory before this change (20 November 2026)

Counted with a regex over `insert_*`, `update_*`, `replace_one`, `delete_*`,
`find_one_and_*` and `bulk_write` calls.

- Python: 481 write calls in 96 files. 23 files also called one of the
  existing logs (`admin_logs` through `log_admin_action`, `demo_edit_history`
  and `demo_audit_logs`, `board_audit_logs`, `super_audit_logs`,
  `login_logs`), covering part of their writes. Largest: `admin_demo_bp.py`
  (48 writes, mostly logged), `users/BPs/auth.py` (46, few logged),
  `basic_routes.py` (19, partly), `admin_case_bp.py` (15, logged),
  `god_auth.py` (15, partly), `security_cases.py` (15, none),
  `admin_org_bp.py` (13, partly).
- Not logged at all before: public account flows (settings, MFA, passkeys,
  profile, organisations), the MCP endpoint and API tokens, organiser and
  cancellation links, campaign and reminder signups, city admin applications,
  notifications and email queue, every systemd script (`repeat_v2`, urgent
  review alerts, submission notifications, support tickets, auto-close,
  reminders, purges, translations), the background job manager, demonstration
  model saves (`Demonstration`, `Organization`, `Case`, `MemberShip`
  classes) and all migrations.
- Go API: about 50 write calls in 13 files (account settings, developer apps,
  public submissions, suggestions, contact, campaign and reminder signups, city
  admin applications, analytics); none wrote an audit record.

The existing logs stay: they carry human-readable intent (reasons, case
links, before/after snapshots of demonstrations). `audit_events` is the
complete, low-level trail beneath them.
