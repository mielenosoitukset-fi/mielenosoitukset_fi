# Hallinto issuer production opt-in

The existing Flask account database remains the read-only identity source for
issuer decisions. The issuer's authorization codes and browser revocation
markers use a separate authenticated MongoDB client. The new client has bounded
operation timeouts and does not contact MongoDB during default application
startup. A missing opt-in starts Flask without issuer routes or login hooks.

## Secret and namespace boundary

Set `HALLINTO_ISSUER_CONFIG_FILE` in the Flask service environment to explicitly
enable the issuer. The file is protected JSON with these fields:

| Field | Value |
| --- | --- |
| `issuer` | Canonical HTTPS account origin plus `/hallinto-oidc` |
| `redirect_uri` | Exact HTTPS Hallinto origin plus `/auth/callback` |
| `client_id`, `client_secret` | Dedicated confidential client ID and secret (minimum 32 characters) |
| `key_id`, `private_key` | Key ID and private RSA JWK (minimum 2048 bits) |
| `mongo_database` | Dedicated issuer database, for example `hallinto_issuer` |
| `mongo_uri` | Authenticated `mongodb://` URI with a matching database path and TLS for non-loopback hosts |

Use mode `0600` and ownership by the Flask service account. The code rejects
symlinks, group/world permissions and non-regular files. Required secrets are
loaded as a file and never embedded in commands, logs or this repository.
Configuration failure stops opt-in rather than falling back to any other identity
store. Removing the environment setting and restarting returns to default-off
behavior.

Provision these state collections and indexes separately using approved Hallinto
maintenance credentials. The Flask issuer does not create them or read any
account data from them.

| Collection | Required indexes and service data privileges |
| --- | --- |
| `hallinto_login_markers` | Default unique `_id`, TTL on `expires_at` with `expireAfterSeconds: 0`. Find, insert and update. |
| `hallinto_oidc_codes` | Default unique `_id` (nonce digest), unique `code_digest`, TTL on `expires_at` with `expireAfterSeconds: 0`. Find, insert and update. |

The issuer's client is built with the audited factory, so each state write
also inserts a redacted record into `audit_events` in the issuer database.
Grant the issuer role insert on `audit_events` there, or the audit records go
to the Flask service journal instead (`AUDIT_FALLBACK`). The `audit_events`
indexes are created by the audit sink when its role allows it.

Do not use either the existing website account database or Hallinto's registry
collections for issuer state. Writes use majority and journal acknowledgment.
The backchannel uses direct state revalidation, so an unavailable owner or issuer
state database prevents Hallinto requests from being authorized. A failed marker
issue does not block existing first-party login. For an already linked browser,
logout must finish revocation before clearing the browser binding so the user
can retry safely on a temporary state-store outage.

## TLS and activation checks

The isolated Flask blueprint requires the configured account host and an HTTPS
scheme from the trusted WSGI hop, before Werkzeug's ProxyFix. Ensure the
canonical TLS virtual host overwrites `X-Forwarded-Proto` and forwards only through
the trusted Gunicorn socket, and confirm that Gunicorn exposes
`wsgi.url_scheme=https`. Do not broaden forwarding trust across the site. Verify
a direct untrusted forwarded header cannot satisfy the issuer check.

Prepare routing, protected file permissions, index readiness and query/Referer
log redaction for authorization and callback paths before activating. Canary the
issuer discovery and authenticated owner session validation over the actual TLS
route. Test a disposable account through login, code exchange and browser-specific
logout. Confirm regular Flask login and native logout behavior when the feature
is disabled. A planned rollback pauses Hallinto access, removes the opt-in and
restarts Flask. Deny requests while the owner is unavailable. Keep issuer state
under restricted credentials and handle removal under the approved retention
procedure.

This change implements opt-in only. It provisions no production credentials,
state, routing, service or user grant.
