# Account pages in React and Go: public profile and settings

Status: plan, 17 November 2026. The maintainer asked for this on 17 November 2026; it is
part of the TODO item "Account pages" (approval: authentication flows). Builds on
[auth-bridge.md](auth-bridge.md) and the `/developer` pattern in
[developer-react-write-controls.md](developer-react-write-controls.md). Flask stays the
session owner. Login, registration, password reset, MFA verification at login and the
forced password or identity change are out of scope.

Each step ships dark: its own flag, off by default, and the Apache rule commented out.
Activation needs the maintainer's go, a backup and a revert note.

## 1. Pages in scope (verified from the code)

| Page | Flask route | Real URL |
| --- | --- | --- |
| Public profile | `users/BPs/profile.py` `profile()` | `/users/profile/<username>` and `/users/profile/` (own profile) |
| Settings | `users/BPs/auth.py` `settings()` | `/users/auth/settings` |

Both blueprints sit under `user_bp` (`url_prefix="/users/"`). `/users/profile/edit` only
redirects to the settings page.

## 2. Public profile: what it shows and who sees it

Anyone, signed in or not, can open any profile by username (exact match on
`users.username`). An unknown username flashes "Käyttäjäprofiilia ei löytynyt." and
redirects to the front page. Suspended, banned or inactive accounts are not hidden.

| Shown | Source | Visible to |
| --- | --- | --- |
| Display name (falls back to username) | `users.displayname` | everyone |
| Profile picture (default `cdn2…/default-pfp.jpg`) | `users.profile_picture` | everyone |
| "Järjestelmävalvoja" badge | stored `global_admin` flag or national-team access (see below) | everyone |
| Bio | `users.bio` | everyone |
| Post count and post list | `user.posts` does not exist on the model, so always 0 and the empty state | everyone |
| Follower and following counts and lists (display name, username, picture, link) | `users.followers`, `users.following` (ids as strings or ObjectIds) | everyone |
| Followed organisations (name, logo, city, description cut to 120) | `users.followed_organizations` → `organizations` | the account itself only |
| Followed recurring demonstrations (title, date, city, address, link) | `users.followed_recurring_demos` → `recu_demos` | the account itself only |
| "Muokkaa profiilia" and "Jaa profiilisi" (share text with the profile URL) | none | the account itself only |
| Follow, friend request, accept, reject buttons | Flask `/users/profile/api/*` | other signed-in visitors; anonymous visitors get a login popup |

Badge (security review of #1195): Flask shows the badge for `user.global_admin`. For a
governance-managed account that is national-team access only; for other accounts it is the
stored flag, a role of `god`, `global_admin` or `superuser`, or national-team access. Go
computes the same, read-only, with one deliberate exception: it never reads `role`, so a
privileged role is not revealed publicly. The one remaining difference is therefore an
account that is not governance-managed and has a privileged role but neither the flag nor
national access: Flask shows the badge, Go does not.

National access follows `utils/system_teams.user_team_access`: `NATIONAL_ADMIN_ORG_ID`
(passed by `deploy/go-api/configure_environment.py` when `PROFILE_READS` is on; invalid or
missing means no national access, so the badge falls back to the flag), an existing
organisation, a `governance_approved` membership, and either `grant_source` of
`maintainer_bootstrap` or an approved `board_clearances` record (all three paths of
`clearance_record_approved`, including the god-appointment consent checks, ported as pure
functions). The account must also be active, not banned, confirmed and not under an
active security suspension; Flask's `User` model does not test the suspension, so Go is
slightly stricter there (no badge). Any lookup error fails the profile read like any other
store error; unrecognised record shapes count as not approved.

Banned, deactivated and security-suspended accounts answer 404 (maintainer decision of 18 November 2026), in Go and in Flask. Reviewer note on #1195 about suspended profiles is closed by this.

Existing defects found while reading, kept out of this work: the "Poista ystävä" button
posts to `/users/profile/api/remove_friend/`, which does not exist; the follow and friend
JSON endpoints have no CSRF token and rely on `SameSite=Lax`.

### Go read: `GET /api/profile/<username>`

- Public part, no session needed: the fields marked "everyone" above, nothing more. The
  follower and following lists keep Flask's fields (`username`, `displayname`,
  `profile_picture`) and are capped at 500 each. No email, role name, id, flags or dates.
- Own part: only when the request carries a cookie, the bridge resolves it, and the
  principal's account id equals the profile's `_id`. Then `self: true` and the followed
  organisations and recurring demonstrations. A bridge failure never fails the page: the
  public part is returned with `self: false`.
- Viewer part for the follow buttons: when a different account is signed in, `viewer`
  gives `following` and `friend_state` with Flask's rules (`friends`, `incoming_request`,
  `outgoing_request`, `none`), so React does not need the two Flask reads.
- Unknown or malformed username: 404. Usernames are matched exactly, at most 64 bytes.
- `Cache-Control: no-store` and `Vary: Cookie` when a cookie is present; the anonymous
  answer may be cached briefly (`public, max-age=60`).
- Flag: `PROFILE_READS: true` in `config.yaml` → `PROFILE_READS_ENABLED=true` for Go. The
  own and viewer parts additionally need the bridge (`AUTH_BRIDGE_SCOPES`).

### React page

- Route `/users/profile/<username>`; `/users/profile/` (own profile, needs the username)
  stays in Flask. Copy is the Flask msgids. Follow and friend buttons keep posting to the
  unchanged Flask JSON endpoints; the broken "Poista ystävä" action is not offered.
- An unknown profile renders a 404 page with "Käyttäjäprofiilia ei löytynyt." instead of
  a redirect (deliberate: a missing page should not answer 302 to the front page).
- Apache: only `^/users/profile/[a-z0-9_-]{3,30}/?$` for GET/HEAD, excluding `edit`, so
  `/users/profile/api/*`, `/users/profile/edit`, legacy usernames outside the current
  rules and every POST stay in Flask.

## 3. Settings: what it shows and does

The Flask view renders a shell; the four tabs read and write through JSON endpoints. All
need a signed-in session (`login_required`).

| Tab | Shows | Reads | Writes (all JSON, same origin) | Extra guard | Side effects |
| --- | --- | --- | --- | --- | --- |
| Yleinen | display name, city, language (Finnish only), dark mode | `GET /users/auth/api/v2/settings` (inserts a default `user_settings` row on first read) | `POST /users/auth/api/v2/settings` | reserved admin names refused | email "Käyttäjäasetusten muutokset"; display name copied to `users.displayname` |
| Turvallisuus: MFA | status and devices | `GET /users/auth/api/v2/mfa_status` | `POST …/api/v2/mfa` (QR, verify), `…/mfa_device_revoke`, `…/mfa_device_rename` | revoke and rename need step-up (`sudo_required`) | MFA flag on the account |
| Turvallisuus: password | form | none | `POST …/api/v2/change_password` | step-up; current password checked | `password_changes` row, email |
| Turvallisuus: passkeys | list | `GET …/api/v2/passkeys` | register, rename, delete under `/users/auth/api/v2/passkeys/*` | step-up for changes | WebAuthn ceremony in the browser |
| Turvallisuus: login history | last 30 attempts | `GET …/api/v2/login_logs` | none | | |
| Profiili | bio, picture upload | `GET …/api/v2/user_profile` | `POST …/api/v2/user_profile` (base64 picture to S3) | | S3 upload |
| API-avaimet | lock state, tokens | `…/api_tokens/status`, `…/api_tokens/list` | create, revoke, request access | admin scope needs global admin plus step-up | access request emails admins |

Not on the page today: email change, account deletion, notification preferences (the
newsletter box is hidden). They are not added here.

Step-up (`sudo_required`, `/users/auth/api/v2/step-up/*`) is a record in the Flask
session cookie. Go cannot see it, so anything behind it stays in Flask.

## 4. Who owns what after this work

| Action | Owner | Reason |
| --- | --- | --- |
| Profile and settings reads | Go | read-only, own data through the bridge |
| Bio | Go write (step 3a) | own document, no email, no step-up |
| Display name, city, language, dark mode | Go write (step 3b) | needs the change email; Go renders the same message into the existing email queue (`email_queue` job shape), decided in that PR |
| API tokens | Go, already live | the settings tab reuses `/api/developer/*`; admin-scope tokens stay on the old page as on `/developer` |
| Profile picture | Flask | S3 upload; Go has no S3 client |
| Password, MFA devices, passkeys, step-up | Flask | credential changes; need the Flask step-up record and Werkzeug hashes. Moving them is a separate approval (decisions log, auth bridge) |
| API access request | Flask | sends the admin email, as on `/developer` |
| Follow and friend actions | Flask | not part of the settings families; the profile page keeps calling them |
| Login, MFA at login, reset, forced changes | Flask | out of scope |

## 5. CSRF and session handling

- Reads: Go resolves the session through the bridge (`RequireAuthenticated`), answers
  `no-store`, `Vary: Cookie`, and refuses `Sec-Fetch-Site` other than `same-origin` or
  `none`. Go never parses, copies or sets the Flask cookie.
- Go writes copy the `/developer` guards: `Sec-Fetch-Site: same-origin`, `Origin` equal to
  `AUTH_BRIDGE_ORIGIN` when present, `application/json` bodies with a size cap, an
  account-bound HMAC token from the read answer sent back in `X-CSRF-Token`, a per-account
  rate limit and an audit row with `source: go-api`.
- React calls to the Flask JSON endpoints keep today's protection (same-origin `fetch`
  with a JSON body and `SameSite=Lax`); nothing is weakened or added there.

## 6. Pull requests (stacked with `gh stack`)

1. Go `GET /api/profile/<username>` and the React profile page (`PROFILE_READS`).
2. Go `GET /api/account/settings` (general settings without the insert-on-read, bio,
   picture URL, MFA status and devices, login history, API token lock state) and the
   React settings screen; writes still go to the Flask endpoints (`ACCOUNT_SETTINGS_READS`).
3. Go writes, one family per PR behind `ACCOUNT_SETTINGS_WRITES`: (a) bio, (b) general
   settings with the change email. Nothing that changes credentials moves in this work.

Each PR gets an independent security reviewer SHIP; the UI PRs also a visual SHIP.

## 7. Activation and revert (per step, with the maintainer's go)

1. Back up `config.yaml` and `/etc/mielenosoitukset-api/environment`.
2. Set the step's flag in `config.yaml`, re-run the deploy (it writes the Go
   environment), restart the API outside the backup dump windows.
3. Uncomment the step's Apache lines and reload Apache.

Revert: re-comment the Apache lines and reload; remove the flag and restart the API.
Flask still serves every page and endpoint, so no data needs restoring.

## 8. Step 2 as built (settings reads)

- Go: `GET /api/account/settings` (`go-api/account_settings.go`), only with the auth bridge on and
  `ACCOUNT_SETTINGS_READS: true` → `ACCOUNT_SETTINGS_READS_ENABLED=true`. GET/HEAD, same-origin, `no-store`,
  signed-in only (401/503). Own account only, never writes: a missing `user_settings` row returns defaults
  (display name from the account) instead of Flask's insert-on-read. Returns `general`, `profile` (bio, picture
  URL), `mfa` (status, devices without secrets), `passkeys` (no credential ids or keys), the last 30
  `login_history` rows and the `cities` list. No email, hashes or account id.
- React: `/users/auth/settings` with four tabs (hash links `#tab-general` and so on, arrow-key navigation).
  Yleinen and Profiili post to the unchanged Flask `/users/auth/api/v2/settings` and `…/user_profile`
  and show Flask's own error message. API-avaimet links to `/developer`. Turvallisuus is read-only.
- Missing in the Security tab: changing the password, MFA devices and passkeys (step-up stays in Flask).
- Activation precondition: the Apache rules stay commented until those controls exist in React, because the
  page rule makes the Flask page that changes them unreachable. Activation needs the maintainer's go.

## 9. Step 3a as built (bio write)

- Go: `POST /api/account/profile` with `{"bio": "<text>"}` (`go-api/account_settings_write.go`). It exists only
  with `ACCOUNT_SETTINGS_WRITES: true` → `ACCOUNT_SETTINGS_WRITES_ENABLED=true` on top of the reads flag and the
  auth bridge; otherwise the path is an unknown path. `configure_environment.py` sets it only when the bridge and
  `ACCOUNT_SETTINGS_READS` are on.
- Guards are the `/developer` ones, run by the same code (`developerWrites.guarded`): signed-in through the bridge
  (401/503), `Sec-Fetch-Site: same-origin`, `Origin` equal to `AUTH_BRIDGE_ORIGIN` when present, `application/json`,
  body at most 40 KiB (5000 characters of up to 4 bytes), an account-bound HMAC token in `X-CSRF-Token` (403), 20 writes per minute per account (429),
  `Cache-Control: no-store`, `nosniff`. Run one Go instance: the key and the counters are in memory.
- `GET /api/account/settings` adds `writes: true` and `csrf` only when the write flag is on.
- Effect: `$set` of `users.bio` for the signed-in account and nothing else; an empty or blank bio stores `null`.
  Answer `{"status":"success","bio":…}`. Audit row in `api_usage` (`profile bio updated`, user, IP, time,
  `source: go-api`); the text is never logged or audited.
- Limits (decision: Flask has no server limit; 5000 is the length the read already returns and the old textarea allowed, so nothing readable becomes unsavable): the text is trimmed, CRLF becomes LF, the bidi override and isolate characters U+202A-202E and U+2066-2069 are removed, at most 5000 Unicode
  characters, no control characters except newline and tab. Flask accepts any length. Longer existing bios stay
  readable (the read cuts at 5000) and can still be saved through Flask or shortened.
- React: Profiili posts a bio without a new picture to Go with the token and shows a "x / 5000" counter and, near the limit, a screen-reader announcement "x merkkiä jäljellä"; with a
  picture chosen it posts both to the unchanged Flask endpoint (S3). Go's 400 text is shown, 429 uses the rate-limit
  copy, 401 and a bad token say "Istunto vanhentui." with a "Lataa sivu uudelleen" button. Messages appear next to the save button.
- Activation: back up, set `ACCOUNT_SETTINGS_WRITES: true`, deploy, restart the API; uncomment the
  `/api/account/profile` rule only after the settings rules (maintainer's go). Revert: re-comment the rule, remove
  the flag, restart. The React page then falls back to the Flask endpoint with no data to restore.

## 10. Step 3b as built (general settings write)

- Go: `POST /api/account/settings` (`go-api/account_settings_general.go`) with any of `display_name`, `city`,
  `language`, `dark_mode`; `subscribe_newsletter` stays in Flask. Same flags and guards as the bio write (section 9),
  no new flag. The path answers GET as before; POST exists only when the write flag is on.
- Validation (a 400 with a Finnish text): unknown field or `null` value; `display_name` trimmed, 1-40 characters, no
  control characters, and not a reserved identity name (`admin`, `administrator`, optional leading `@`; Flask's text
  "Admin-nimitys on varattu palvelun sisäiseen käyttöön."); `city` at most 100 characters and, when not empty, one of
  the city list's names; `language` only `fi`; `dark_mode` a JSON boolean. Stricter than Flask on purpose: Flask
  accepts any value and JSON-parses strings (so a display name `"123"` becomes a number there).
- Effect: reads `user_settings` (a missing document counts as Flask's defaults: all `null`, display name = account's
  displayname or username), compares old and new per field, and `$set`s only the changed ones with an upsert
  (`$setOnInsert` carries the other defaults, the shape Flask inserts). A changed display name is also copied to
  `users.displayname`, as `process_settings_change` does. Nothing changed: no write, no mail, answer
  `Ei muutoksia asetuksissa.` (Flask also inserts the default document on first access; Go does not).
- Mail: when something changed and the account has an email, Go inserts the same document Flask's `queue_email`
  inserts into `email_queue` (subject "Käyttäjäasetusten muutokset", HTML of `auth/settings_changed.html`, `status`
  pending, `instance_id` of this process). Flask's worker sends it; Go never sends mail. A failed insert is logged
  without the address and does not fail the write, as in Flask. The greeting uses the stored displayname from before
  the change (as Flask); where Flask would print "None" for an account without a displayname, Go falls back to the
  username.
- Field order: Flask walks a set, so the order in the mail and the answer changes between processes. Go fixes it:
  city, display_name, language, dark_mode.
- Parity: `tests/test_go_settings_parity.py` drives the real Flask handler and template and writes
  `go-api/testdata/settings_parity.json`; `TestSettingsParityWithFlask` replays the cases (answer, byte-exact mail HTML
  with escaping, None and bool values, stored values). Regenerate with
  `UPDATE_GO_FIXTURES=1 ../../../.venv/bin/python -m pytest tests/test_go_settings_parity.py`.
- Audit: `api_usage` row `settings updated` with the changed field names only, never values, `source: go-api`.
- React: the Yleinen tab posts to the Go route with `X-CSRF-Token` when the read answer carries a token, otherwise to
  the Flask endpoint; errors are handled like the bio save.
- Activation: this is the first real mail written by Go, so it needs the maintainer's go. Back up, then it comes live
  with the write flag (section 9) and the `/api/account/settings` POST rule; watch the first queued mail reach the
  worker. Revert: remove the write flag and restart; the page falls back to Flask, queued mails are ordinary Flask-shaped
  documents and need no cleanup.

## Step 4a as built (password change)

- Moved: the "Vaihda salasana" form now lives in the React Turvallisuus tab, above the read-only MFA card
  (`PasswordCard` in `AccountSettingsScreen.tsx`). Stays in Flask: the write. The form posts JSON
  `{current, new, confirm}` to the unchanged `POST /users/auth/api/v2/change_password`
  (`api_change_password`, guarded by `@sudo_required()`). No Go or Flask code changed.
- Step-up flow (reusable for the MFA and passkey steps): `useStepUp().withStepUp(() => fetch(...))` in
  `StepUpDialog.tsx`, logic in `account-stepup.ts`. A request answered `403` with JSON `{"error": "step_up_required"}`
  (what `sudo_required` sends; a plain 403 or a 401 is not step-up) opens a native `<dialog>`. The dialog asks
  `/users/auth/api/v2/step-up/options`, offers a passkey (options, `navigator.credentials.get`, verify) when the
  account has one and the browser supports it, and always the password, plus the MFA code when `requires_totp`.
  On success the original request is sent once more; Peruuta or Escape resolves `null` and nothing is retried.
  Errors show inside the dialog. The base64url helpers are ported from `static/js/webauthn.js`.
- Client checks: all fields filled, new equals confirm, at least 12 characters. The server stays the authority and
  its messages are shown as returned. Fields are cleared after success; values are never logged or stored.
- New Finnish strings (the rest are Flask's msgids verbatim): "Vaihdetaan…" (button while busy),
  "Vahvistetaan…" (step-up button while busy), "Vahvistustapoja ei saatu haettua. Yritä salasanalla." The
  Turvallisuus notice now reads "Kaksivaiheisen tunnistuksen ja passkeyjen muuttaminen ei ole vielä tällä sivulla…".
- Tests: `tests/test_account_stepup.py` (detection, retry and cancel flow, request body, message mapping, base64url).
- Activation: nothing to activate; ships with the settings page. Revert: revert the pull request.
