# Organization invitation: React page plus Go read and POST

Status (20 November 2026): built, **dark**. Flask's `GET /users/orgs/accept_invite?organization_id=<id>`
(`user_orgs_bp`) is still the live route and is unchanged, as is Flask's own
`POST /users/orgs/accept_invite/<id>` (the button on the organization page). Third layer of the
stack that starts with [volunteer-confirm-react.md](volunteer-confirm-react.md); it grants
membership, so it needs an **(approval)-grade security review** before anything is switched on.

## Why

Flask joins the organization in the GET itself, so a link scanner, a prefetch or an image tag that
makes a signed-in member request the link joins them. The React page now reads the invitation and
joins only when the button is pressed; Go does both calls with the visitor's own session.

## What changed

| Part | Where | State |
| --- | --- | --- |
| Go `GET /api/organizations/<id>/invitation` | `go-api/org_invite.go` | off unless `ORG_INVITE_GO_ENABLED=true`, and only inside the auth bridge |
| Go `POST /api/organizations/<id>/invitation/accept` | same | same |
| React route `/users/orgs/accept_invite?organization_id=<id>` | `OrgInviteScreen.tsx`, `org-invite.ts`, `routes.tsx`, `render.tsx` | rendered by the React host, nothing routes to it |
| Apache rules | `deploy/apache/react-ssr-routing.conf` | commented out |

The server renders only the shell (title, "Ladataan kutsua…") and the id from the link. The
invitation is read in the browser with the visitor's own cookie, so the page is `private, no-store`,
`noindex, nofollow`, no canonical URL, `Referrer-Policy: no-referrer`, and the server makes no
invitation request on GET.

## The read

`GET /api/organizations/<id>/invitation` (id: 24 lowercase hex). Needs a signed-in account (401
otherwise; the page then offers a login link whose `next` returns to the invitation page).
Same-origin fetch only.

| `state` | Meaning | Extra fields |
| --- | --- | --- |
| `invited` | the account's exact e-mail address holds a live invitation | `organization.name`, `csrf` |
| `not-invited` | no invitation, or expired (a dated one older than 30 days) | |
| `organization-not-found` | no such organization | |
| `protected` | a system team: ordinary joining is refused | |

The organization name and the CSRF token are given only to the invited account. A failure is 503,
never a state.

## The POST

`POST /api/organizations/<id>/invitation/accept`, JSON `{}` (the organization is in the path; a body
with anything else is a 400). Guards, the same as the other signed-in Go writes
(`developerWrites.guarded`): session through the auth bridge, `Sec-Fetch-Site: same-origin`,
`Origin`, JSON content type, the account-bound CSRF token from the read in `X-CSRF-Token`, 20 a
minute per account.

| Case | Status | Body |
| --- | --- | --- |
| Joined | 200 | `{"state":"joined","success":true,"organization":{"name":…}}` |
| No live invitation (also a lost race or a replay) | 200 | `{"state":"not-invited","success":false}` |
| Organization missing | 200 | `{"state":"organization-not-found",…}` |
| System team | 403 | `{"state":"protected",…}` |
| Missing/stale CSRF, cross-site, bad origin | 403 | |
| Signed out | 401 | |
| Database failure | 503 | never a verdict |

Parity with Flask `accept_invite` / `accept_invite_post`:

- The invitation is matched by the account's **exact** e-mail address against
  `organizations.invitations` (plain strings, or `{email, invited_at}`); a dated entry older than
  30 days is ignored (`utils/invitations.py`). Case differences do not match, as in Flask.
- Accepting removes every entry for that address (Flask drops expired ones too) and creates a
  `memberships` row with role `member` and the member default permissions
  (`INVITE_TO_ORGANIZATION`, `LIST_ORGANIZATIONS`, `VIEW_ORGANIZATION`). An existing membership is
  left alone, as `add_member` does.
- System teams refuse with the message of `forbid_ordinary_team_write`.

Differences, all on purpose:

- **Atomic claim.** The invitation list is replaced with one update that matches the exact list that
  was read, so two clicks cannot both join and a concurrent edit makes the click lose instead of
  being overwritten.
- **No role overwrite.** The membership is `$setOnInsert`; Flask's `$set` could reset the role of a
  membership created a moment earlier.
- **Rollback.** If the membership write fails, the invitation is put back (best effort, logged).
  Flask lost the invitation in that case.
- **Audit.** One `api_usage` row (`organization invitation accepted`, user, organization, IP,
  `source: go-api`), like the other Go writes. Flask wrote none.
- **System teams, belt and braces.** Besides `NATIONAL_ADMIN_ORG_ID` and `BOARD_ORG_ID`, an
  organization with any `governance_approved` membership is treated as a system team. The route
  **refuses to start unless both `NATIONAL_ADMIN_ORG_ID` and `BOARD_ORG_ID` are set and valid**.
- Flask answers with a redirect and a flash; React shows the outcome and a link to the organization.

## Open questions for the security review

1. **`BOARD_ORG_ID` is not in the Go environment today** (only `NATIONAL_ADMIN_ORG_ID` is, for
   profiles). Resolved in code: the route refuses to start without both ids. Activation must add
   `BOARD_ORG_ID`; the governance marker is a second line, not a replacement.
2. Matching by the exact stored address means an invitation sent to `Maija@x.fi` does not work for a
   user stored as `maija@x.fi`. That is Flask's behaviour and is kept; making it case-insensitive
   would widen who can use an invitation and is a separate decision.
3. The CSRF key and the rate limit live in process memory, so run one Go instance (as for the other
   signed-in writes).

## Tests

`go-api/org_invite_test.go`: invitation matching (strings, dated, expired, exact case, junk), every
read state, accept once, 15 concurrent clicks join once, refusals (non-POST, missing/foreign CSRF,
cross-site, foreign origin, signed out, non-JSON, bad bodies, system team, governance marker,
another account's invitation, expired, failed membership write restoring the invitation, existing
admin role kept, rate limit, pass-through, off by default and an invalid system team id), and
`TestInvitationMongoStore` against a disposable MongoDB (`REMINDER_TEST_MONGO`: exact-array update
with mixed entries, stale list loses, upsert never overwrites). `tests/test_org_invite_client.py`
covers the page's request helpers; `scripts/verify_public_host.mjs` the rendered shell, headers and
the active Apache rules.

## Activation (21 November 2026, maintainer's go)

Switched on through a pull request and the normal deploy, not by hand on the server (the Go
environment file is rewritten on every deploy):

1. `deploy/go-api/configure_environment.py` writes `ORG_INVITE_GO_ENABLED=true` with the auth bridge on,
   together with `NATIONAL_ADMIN_ORG_ID` and `BOARD_ORG_ID` from `config.yaml` (the maintainer confirmed
   both are set). Without both ids the flag is not written; an id that is not 24 hex characters stops
   the deploy. `ORG_INVITE_GO: false` in `config.yaml` opts out.
2. The four Apache rules are active. `deploy/release_routing.sh` installs them only after Go answers
   `GET /api/organizations/<id>/invitation/accept` with 405, so a Go API without the route keeps the
   previous routing.
3. After the deploy: invite a test account from an organization the maintainer owns, open the link,
   press the button.

## Revert

Re-comment the Apache rules and deploy (Flask serves the old route at once); set
`ORG_INVITE_GO: false` in `config.yaml` to switch the Go route off at the next deploy. Memberships created through this route are ordinary
`memberships` rows and can be removed in the usual way; no schema change.

## Reviews requested

Independent **security SHIP** (auth/privilege: session, CSRF, exact matching, system teams,
atomicity) and independent **visual SHIP** (loading, signed-out, invited, joined and the refusal
states, 390 px and 1440 px, both themes).
