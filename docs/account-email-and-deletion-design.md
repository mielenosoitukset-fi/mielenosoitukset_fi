# Design: email change and account deletion (proposal, nothing built)

Status: **proposal for the maintainer's decision.** Neither feature exists on today's Flask
settings page or anywhere in the user-facing code (an admin can edit an email and delete a user in
`admin_user_bp.py`; a user cannot). Section 12 of `account-pages-react.md` asks for a design first.
This page is that design. No code ships with it; each part becomes its own dark PR after approval.
It also lists the questions only the maintainer can answer.

## 1. Email change

### Facts
- `users.email` is shown and mailed to; `users.email_canonical` is the casefolded form used for
  uniqueness and login lookup (written by the admin routes, see `admin_user_bp.py`).
- Mail is queued in `email_queue` and sent by the Flask worker. Go may queue it too.
- Step-up (`sudo_required`) exists; password changes write `password_changes`.

### Flow (three steps, no change until the new address proves itself)
1. **Request.** Signed-in user, fresh step-up, enters a new address. Server: normalise and
   casefold, refuse if it equals the current one or `email_canonical` is taken (answer the same
   neutral text either way, so the form does not reveal which addresses have accounts), rate limit
   (3 requests per hour per account, 10 per hour per IP). Store a pending record
   `{user_id, new_email, new_canonical, token_hash, created_at, expires_at (1 h)}` in a new
   collection `email_changes`. Only the hash of a 32-byte random token is stored. A new request
   replaces the previous pending record.
2. **Confirm.** Mail to the **new** address with a link carrying the token. Opening it shows a
   page asking for one click (GET never changes anything). The POST needs the signed-in session of
   the same account **and** a fresh step-up, checks token hash, expiry, that the account still
   exists and `new_canonical` is still free, then in one update sets `email` and `email_canonical`,
   deletes the pending record, bumps the session version so other sessions sign in again, and
   writes an `email_changes` audit row (old and new address hashed, time, IP).
3. **Notice.** Mail to the **old** address, sent on request and again on completion: "your email
   was changed to ‹masked›; if this was not you, use this one-time link to undo within 7 days and
   reset your password". The undo link restores the old address, signs every session out and
   forces a password reset. Without it a hijacked session could take over an account by moving the
   email.

### Decisions needed
1. Is the undo link wanted (recommended) or is the notice enough?
2. Does a changed email also need re-verification of MFA (recommended: step-up already covers it)?
3. Accounts that signed up with a now-dead address: is there a support path, or admins only?

### Build order
Flask endpoints and mails first (they own the session), tests for token expiry, reuse, replay,
race on a taken address and cross-account use; then the React form. Go stays out: it cannot see
the step-up record.

## 2. Account deletion

### Facts
- Admin deletion today: step-up, `flush_user_history`, `users.delete_one`. It does not clean
  memberships, messages, follow lists, tokens, MFA devices or passkeys (open PR #597 is about
  personal-data cleanup after deletion; this design should reuse, not duplicate, that work).
- Demonstrations, organisations and recurring events keep user ids as authors and members.
- Moderation and audit rows must survive for abuse handling.

### Flow
1. **Request.** Signed-in user, fresh step-up, types their username to confirm and reads a list of
   what happens. The account is marked `deletion_requested_at`, sessions other than this one are
   revoked, API tokens and passkey login are disabled, and the profile page answers "not found"
   (the hidden-account rule). A mail to the address says when it will happen and how to cancel.
2. **Grace period (30 days, proposed).** Signing in with the password cancels the request and
   shows a notice. A reminder mail goes out 3 days before the end.
3. **Erase (a job, not a request handler).** Anonymise instead of delete the user document so
   references stay valid: remove email, `email_canonical`, display name, bio, picture, city,
   language, password hash, MFA devices, passkeys, tokens, follows, notifications and queued
   mail; set username to a neutral `deleted-‹short id›` and `deleted: true`. Delete messages the
   user wrote only if the maintainer chooses so below. Keep: moderation records, audit rows,
   demonstrations and organisation content the user created (shown as "deleted user").
   Organisation sole-owner accounts cannot be erased until ownership is handed on or the
   organisation is deleted: the request is refused with the names of the organisations.
4. **Proof.** The job writes a `user_erasures` row with the id, time and the list of collections
   touched, and no personal data, so a later request can be answered.

### Decisions needed (maintainer)
1. Grace period length: 30 days, 7 days or none?
2. Messages sent to other people: keep (anonymised sender) or remove?
3. Content the user authored (demonstrations, organisation pages): keep as "deleted user"
   (recommended) or hand to an admin?
4. Sole organisation owners: refuse until handed over (recommended) or auto-assign to an admin?
5. Backups: erased data stays in backups until they rotate; state that period in the notice.
6. Should banned or suspended accounts be able to request deletion?

### Build order
Erase job and its tests against a seeded account first (nothing user-facing), then the Flask
endpoints and mails, then the React screen. A dry-run mode that lists what would change comes
before the first real erasure, and the first production erasure needs the maintainer's go.

## 3. What stays out
Nothing here touches login, registration, password reset or the Apache rules, and nothing runs
until the questions above are answered.
