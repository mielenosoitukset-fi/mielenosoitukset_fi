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
| "Järjestelmävalvoja" badge | stored `global_admin` flag only (see below) | everyone |
| Bio | `users.bio` | everyone |
| Post count and post list | `user.posts` does not exist on the model, so always 0 and the empty state | everyone |
| Follower and following counts and lists (display name, username, picture, link) | `users.followers`, `users.following` (ids as strings or ObjectIds) | everyone |
| Followed organisations (name, logo, city, description cut to 120) | `users.followed_organizations` → `organizations` | the account itself only |
| Followed recurring demonstrations (title, date, city, address, link) | `users.followed_recurring_demos` → `recu_demos` | the account itself only |
| "Muokkaa profiilia" and "Jaa profiilisi" (share text with the profile URL) | none | the account itself only |
| Follow, friend request, accept, reject buttons | Flask `/users/profile/api/*` | other signed-in visitors; anonymous visitors get a login popup |

Badge difference (security review of #1195): Flask shows the badge for `user.global_admin`,
which also covers national-team members, governance-managed accounts and, for ordinary
accounts, the roles `god`, `global_admin` and `superuser`. Go uses only the stored flag
and never reads the role, so a privileged role is not revealed publicly. National-team
admins whose access comes from memberships lose the badge until Go resolves team access.
The reverse also differs: a governance-managed account with the flag set but outside the
national team shows the badge in Go and not in Flask. Both differences must be closed by a
read-only team-access lookup (like `utils/system_teams.user_team_access`) before activation.

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
