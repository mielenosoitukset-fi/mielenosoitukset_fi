# Token and approval lifecycle (SEC-04)

Inventory taken from the code on 12 November 2026. "Tested" names the test in
`tests/test_token_lifecycle.py` unless another file is given.

| Flow | Token | Expiry | Revocation | Replay | Double decision |
| --- | --- | --- | --- | --- | --- |
| Demonstration preview links (`admin_demo_bp`) | Signed itsdangerous string plus a `magic_links` registry row keyed by SHA-256 hash; forged or unregistered strings are refused | 24 hours (signature) and `expires_at` in the row → 410 | `revoked` flag, security-case suspension, and every terminal decision revokes sibling preview/approve/reject/edit links | Single use: `used_at` set after the decision, later use shows "already used" (200) and is audited; bound to the first IP | `apply_demo_decision` is idempotent: same decision reuses its id, no repeated notification, history or audit. Opposite decisions: last one wins (existing behaviour) |
| Old approve / reject email links (`legacy_decision_link`) | Signature only; never decides. Since 20 Nov 2026 they redirect to `/admin/demo/<id>/decide`, which needs an admin session and opens the case (or review page). New emails carry that link directly, with no bearer token | n/a | n/a | n/a | Decisions run only from the case, review page or admin API |
| Demonstration edit link | Same registry, action `edit` | 1 h, 24 h or 7 d chosen at creation → refused when expired | Revocable from the token admin; revoked on terminal decision | Reusable until it expires or is revoked (it is an edit session, not a one-shot) | n/a |
| Organizer cancellation link (`/cancel_demonstration/<token>`) | Random token, hash stored in `demo_cancellation_tokens` | `expires_at` (`TOKEN_EXPIRY_DAYS` after creation) | None besides expiry. Not revoked when the demo is cancelled by other means | `used_at` set after the action; later use refused. The check and the mark are two separate steps, so two simultaneous POSTs can both pass (see Questions) | Not verified here |
| Password reset (`/password_reset/<token>`) | Signed JWT carrying the e-mail address | 1 hour | None. Existing sessions and older reset links stay valid after a reset | Single use through `used_password_change_tokens`, recorded after the password change; the token itself is stored there in plain text | n/a |
| E-mail confirmation (`/confirm_email/<token>`) | Signed JWT carrying the e-mail address | 1 hour | None | Reusable until expiry, harmless: it only sets `confirmed: true` | Idempotent |
| Campaign volunteer confirmation (`/kampanja/confirm/<token>`) | Random token stored in `volunteers` | 24 hours | Token fields removed on consume | Consumed: second use returns 400 | Idempotent |
| Organization invites | No token. An e-mail entry in `organizations.invitations` matched against the signed-in user's e-mail | None | Decline or accept removes the entry | Accept removes the entry first, so a second accept answers "invite not found" | Second accept cannot add the member again |
| Case approvals (`approve_cancellation`) | Admin session with `EDIT_CASE` | n/a | n/a | n/a | Already cancelled demo returns `already_cancelled` without cancelling again or rewriting the case |
| Suggestion approvals (`/suggestions/<id>/apply`, `/status`) | Admin session with `EDIT_DEMO` | n/a | n/a | An applied suggestion can be applied again; every apply writes its own edit history | Reject/close after apply overwrites the status (no guard) |

## Changed in this work

- Password reset and e-mail confirmation tokens were identical in shape, so a
  confirmation link e-mailed to an address was also a valid password reset
  link for it, and the other way round. New tokens carry a `purpose` claim and
  each verifier refuses the other purpose. Tokens issued before this change
  carry no claim and keep working until their hour is up, so no pending mail
  breaks. Revert: remove the `purpose` checks in `utils/auth.py`.

## Covered by regression tests

Cancellation link single use, expiry and unknown token; magic link expiry
(410), revocation (403), forged and unregistered signature, wrong action, and
approval replay; reset token single use, expiry and garbage; purpose
separation and legacy tokens; confirmation expiry and replay; volunteer
confirmation consume and expiry. Decision idempotency and sibling revocation
are covered in `tests/test_demo_decision_service.py`.

## Questions (not changed here)

1. Should a password reset end the user's other sessions and invalidate other
   outstanding reset links? Suggested: yes, by storing a `password_changed_at`
   and rejecting sessions and reset tokens issued before it. This changes
   session semantics and needs a schema field, so it waits for approval.
2. Should reset tokens be stored hashed in `used_password_change_tokens`?
   Suggested: yes; a database read currently reveals spent tokens (spent only,
   so low risk). Needs a one-off migration of existing rows.
3. Should the cancellation and reset consume steps become an atomic claim
   (`find_one_and_update` on `used_at: None`) before acting, with a rollback on
   failure? Suggested: yes for cancellation; it reorders the flow, so it should
   be reviewed. A unique index on the reset-token record would also fix the
   reset race and is a schema change.
4. Should an applied suggestion refuse a second apply, and a rejected one
   refuse apply? Suggested: yes, but admins may rely on re-applying other
   fields today.
5. Should organization invites expire? Suggested: 30 days, which needs an
   `invited_at` on each invitation (schema).

Related: incident #912/#913 protections (SEC-05) were not touched by this work.

## Follow-up changes (coordinator answers, 12 November 2026)

- Suggestions: decided suggestions refuse a second decision; other fields of an applied suggestion can still be applied (PR 1119).
- Organization invites: new invitations carry `invited_at` and expire after 30 days; undated ones never expire (PR 1120).
- Cancellation links: claimed atomically before acting (PR 1121).
- Password reset: claimed through a unique partial index `token_hash_unique` on `used_password_change_tokens` (revert: `db.used_password_change_tokens.dropIndex("token_hash_unique")` and the code falls back to the read-then-write check only after reverting the PR). Spent tokens are stored as SHA-256 hashes; legacy raw rows are still honoured and rewritten to hashes when met. Back up `used_password_change_tokens` before the first production start with this code. The visit log in `password_changes` also stores the hash now, not the raw token.

## Confirmation tokens use a derived key (20 November 2026)

New e-mail confirmation tokens are signed with `HMAC-SHA256(SECRET_KEY, "moso-email-confirmation-token-v1")`
(hex) instead of `SECRET_KEY`, so a service that only verifies them (the Go confirmation route in
`docs/email-confirm-react.md`) can hold the derived value without being able to forge sessions or password
reset tokens. Flask accepts both for the one-hour overlap: tokens signed with the derived key first, then
the old `SECRET_KEY` ones, with the same purpose rule as before.

- Nothing else changes: same claims, same one hour lifetime, same `/users/auth/confirm_email/<token>` URL.
- The fallback stops by itself at `CONFIRMATION_LEGACY_KEY_UNTIL` (31 January 2027, UTC), long after the
  last old link has expired. Delete the constant and the fallback afterwards; leaving them is harmless.
- **Rotating `SECRET_KEY` changes the derived key.** The key held by Go (`EMAIL_CONFIRM_JWT_KEY`) must then be
  derived again with the script below and Go restarted, or Go refuses new confirmation links.
- Revert: restore `generate_confirmation_token` to sign with `SECRET_KEY` and keep the dual verify;
  links already mailed with the derived key then fail for their remaining hour at most.
- Not changed here: the Go route and its `EMAIL_CONFIRM_JWT_KEY` stay unset until this is live and reviewed.

### Writing the derived key to the Go environment (operator, on the Flask host)

Only the derived key goes to Go, never `SECRET_KEY`. This runs once at activation of the Go route
(`docs/email-confirm-react.md`), as a user who can read `config.yaml` and write the Go environment file:

```bash
sudo /path/to/venv/bin/python -B scripts/write_confirmation_key.py
```

The key is computed inside the script from `config.yaml`, so it never appears on a command line, in the
shell history or in the output (the script prints only `written`). It replaces any existing
`EMAIL_CONFIRM_JWT_KEY` line in `/etc/mielenosoitukset-api/environment`, keeps every other line, and leaves the
file at mode 600 through a private temporary file and an atomic replace. Restart the Go API afterwards.
Use the production virtualenv's Python (`PRODUCTION_PYTHON`): it must have Flask, PyJWT and PyYAML, which the
application imports. `-B` keeps it from writing `.pyc` files. A file that cannot be parsed exits with a fixed
message that never quotes `config.yaml`. The file ends at mode 600, which is fine for a systemd `EnvironmentFile`
(systemd reads it as root). `--config` and `--env-file` override the default paths. Covered by `tests/test_confirmation_token_key.py`.

## Scoped case links (admin emails, 20 Nov 2026; same page as the case, 21 Nov 2026)

Admin moderation emails to a known admin carry a `case` link (`/admin/demo/case_link/<token>`):

- One link per recipient admin and demonstration, valid 24 h; any decision on the demonstration revokes it (`TERMINAL_TOKEN_ACTIONS`), and a decision made with it marks it used.
- A plain GET only shows an "Avaa ilmoitus" button, so mail scanners that prefetch links cannot claim it. The POST binds the link to that browser (HttpOnly cookie on `/admin`, only its hash is stored).
- It then redirects to the demonstration's case page, **the same page and buttons as in Tapaukset**. The browser keeps a `case_link` session marker, not a login (no `_user_id`): `login_manager.request_loader` (`utils/case_links.user_for_request`) lets the recipient act as `current_user` only on that case's own routes (`single_case`, `demo_action`, `demo_escalate`, the demo preview and the decision preview, and the duplicate search). Every other route, and Go reading the session, sees a signed-out browser. The page hides the menu and the submitter's contact details.
- Through the link: approve, reject (with a reason, duplicates included) and escalate. Take-down, undo, notes, linking and closing need a login.
- Rights are checked against the recipient's current account on every request, including `ACCEPT_DEMO` for that demonstration.
- Second factor: an admin with TOTP enters a code in the dialog (the login verifier; a code is not accepted twice; five wrong codes revoke the link). An admin with passkeys only must log in to decide.
- After a decision the page stays readable in that browser until the link expires (GET only), so the person sees what happened; the buttons are gone.
- The decision runs through `apply_demo_decision` with `source="case_link"` and the actor marked `authentication: case_link`.
- Recipients without a user account get the login link `/admin/demo/<id>/decide`, which also ends in the case.
