# Decisions log

Maintainer decisions and the reasoning behind them, so that people and agents do
not have to rediscover them from chat history or old PRs. Read this before
starting work. When the maintainer decides something new, add it here in the same
PR that implements it, newest first within each section. Keep each entry short:
the decision, why it was made, where it lives, and anything still open.

Related standing rules live in [AGENTS.md](../AGENTS.md),
[the development workflow](development-workflow.md) and
[the design system](design-system/README.md).

## Process and approvals

- **Demo geocoding must not write stale moderation state (2026-11-15).** Detail-page
  geocoding persists only coordinates and `last_modified`, atomically, and only
  when the stored coordinate snapshot still matches what the request loaded.
  Model construction/loading does not persist normalization by default; any
  normalization write must be explicit. This prevents a delayed geocode or
  normalization save from undoing a concurrent email moderation decision. See
  issue #1172.

- **Finnish language skill (2026-11-15).** The maintainer requires agents to use
  `suomi-finnish` for Finnish writing, editing and review across public/admin UI,
  emails and documentation. Load its relevant references; install it or read
  its source when the harness does not expose it. Repository entry point:
  `AGENTS.md`; skill source: https://github.com/akunikkola/suomi-finnish-skill.

- **Verified production revisions, native coalescing (2026-11-13).** The maintainer
  approved retaining queue 8/8, ALLGREEN, minimum 1 and wait 0 while hardening
  deployments. One active production run finishes; native single-pending
  concurrency coalesces bursts without a scheduler. Reuse trusted full Tests
  evidence for the exact SHA instead of rerunning it on main pushes. Publish
  immutable artifacts once, and deploy Flask, Go, React and routing in one locked
  transaction. Preserve health checks, monotonic successful-release identification
  and visible failures. Normal deployment checks migration readiness; pending
  data changes require a separately approved migration procedure under the lock.
  The worker marker prevents parallel startup migrations. No destructive migration
  or database rollback is authorized by this pipeline change.

- **GitHub handles the merge backlog (2026-11-13).** For 20–40 independent PRs,
  agents arm auto-merge as each is ready and continue other work. Do not serialize
  them by watching each merge. GitHub owns waiting and order; agents intervene for
  failed checks, conflicts and review feedback. A lone PR keeps minimum 1 and
  wait 0, with required queue tests intact. Dependent PRs enter only after the
  parent merges and their base is `main`. This avoids an AI traffic-police role
  without weakening protections.

- **Size the merge queue for 40 waiting pull requests (2026-11-13).** Raise both
  `max_entries_to_build` and `max_entries_to_merge` from 3 to 8. Keep MERGE,
  ALLGREEN, minimum 1, wait 0 and the 60-minute timeout. With roughly 13-minute
  checks, 40 ready PRs need about 14 waves at concurrency 3 (over three hours)
  versus 5 waves at concurrency 8 (about 65 minutes), assuming full utilization
  and no failures. These are parallel waves, not five total CI runs: ALLGREEN
  still checks each entry. Merge size is a separate limit. A failed entry
  invalidates later groups that include it, so do not go higher without evidence. The script exposes both limits as
  CLI flags and preserves every other rule, condition and bypass actor. Roll back
  by setting both flags to 3 after reviewing `--dry-run`.

- **Agents arm auto-merge (2026-11-13).** Auto-merge is enabled on the repository, so
  a session should run `gh pr merge --auto --merge` on a pull request whose checks are
  running and end, instead of watching CI and spending tokens on it. It bypasses
  nothing: the required checks, review threads and the merge queue still decide, and an
  armed pull request is enqueued when they pass. Do not arm a draft, or a pull request
  whose conflicts are unresolved.
- **Stack dependent work with `gh stack` (2026-11-13).** When a pull request depends on
  another that has not merged, agents build a stack with the installed `github/gh-stack`
  extension and merge it from the bottom, instead of chaining branches by hand and
  re-targeting them one by one. Merges still go through the queue.
- **Merge queue on `main` (2026-11-13).** Enabled after the move to the organization:
  merge commits, ALLGREEN, at most 3 entries built and merged, minimum 1, no grouping
  wait, 60-minute check timeout; `python-tests` runs on `merge_group`. Auto-merge stays
  allowed, so an armed pull request is enqueued instead of merging directly. The
  green/yellow/red traffic-light routine is retired because the queue serialises merges
  and rebuilds groups. Settings and rollback are in
  [github-organization-migration.md](github-organization-migration.md).
- **Repository ownership moves to the `mielenosoitukset-fi` organization (2026-11-13).**
  The canonical URL is https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/;
  the old `botsarefuture/mielenosoitukset_fi` links and remotes keep working through
  GitHub's redirect. `python-tests` now also runs on `merge_group` so it can stay the
  required check once the merge queue is switched on. Deployment hosts, credentials,
  paths and environment variables are unchanged. The reference audit, the external and
  server-side follow-ups and the post-transfer checks are in
  [github-organization-migration.md](github-organization-migration.md).
  Merge queue: prepared but off. The intended settings (merge commits, ALLGREEN,
  batches of 3) and the script that applies them only after explicit approval are
  in that document.
- **Changelog notes (2026-11-12).** New user-facing changes use separate files in
  `changelog.d/` to avoid pull requests conflicting in `CHANGELOG.md`. Render the
  notes with the existing history using `scripts/render_changelog.py`; release
  numbering and publication remain open.
- **Merging (2026-11-11).** The maintainer approves merging and deploying once the
  required tests pass, plus an independent visual SHIP for UI changes. No separate
  question is needed per PR.
- **Production changes need a revert path (2026-11-11).** Production data,
  permission and operations changes are approved when they can be undone. Before the
  change, take a backup or record the prior state. Report where the backup is and how
  to revert. Anything without a revert path (permanent deletes without an export,
  real emails to real people) still needs an explicit yes.
- **Review threads block merging (historical; auto-merge policy superseded 2026-11-13).** The `main` ruleset requires every review thread
  to be resolved and the `python-tests` check to pass. Auto-merge is turned off.
  Resolve bot review threads by fixing the issue, or by replying with the reason it
  does not apply.
- **CI runner choice.** The repository variable `MOSO_CI_RUNS_ON` decides where
  same-repository CI runs. Pull requests from forks always run on GitHub-hosted
  runners, because the repository is public.
- **Questions to the maintainer** are asked as a numbered list, with a suggested
  answer under each question.

## Authentication and sessions

- **Isolated Hallinto issuer and browser revocation (2026-11-19).** Approved
  to build and test with synthetic accounts. Keep the issuer under the
  existing Flask account origin so sign-in and passkey ceremonies stay there.
  Bind the Hallinto session to a per-login owner marker so ordinary sign-out
  revokes that browser and account-wide security changes still apply. Live
  routes, data provisioning and production activation retain their own
  approval gates. See [Hallinto owner markers](hallinto-owner-markers.md).

- **Auth bridge: Go and React ask Flask who is signed in (2026-11-12).** The
  maintainer approved starting the move of authentication towards React and Go,
  keeping login on Flask if possible. Chosen after comparing four options: Go asks
  Flask's read-only `/api/session` (with an optional ≤5 s cache, default off).
  Rejected: validating the cookie in Go and Flask-minted tokens (revocation
  through `auth_epoch`, suspension and password resets would lag or be
  reimplemented), and full auth in Go (no rollback). Two off-by-default flags,
  `AUTH_BRIDGE_SCOPES` (Flask) and `AUTH_BRIDGE_ENABLED` (Go); rollback is removing
  either. Login, MFA and password pages are not moved; that needs a new approval.
  Details and the evidence: [auth-bridge.md](auth-bridge.md).
- **Signing out is a POST with a token (2026-11-13).** Sign-out was a plain GET
  link, so a prefetch, crawler or stray hover could end a session (the Apache log
  showed single and triple `GET /users/auth/logout` requests). Now a GET only shows
  a confirmation page (and answers 204 to prefetch/prerender), and only a POST with a
  stateless signed token tied to the account signs out. The account menu opens on
  click or keyboard, not hover. The token is not single-use and not bound to the
  session cookie (stateless by design; it only needs to be unguessable for an
  attacker page, and the worst case is a forced sign-out); it expires after 12 h and
  an expired or wrong one sends the visitor to the confirmation page. After a
  failed server read is confirmed through the bridge the menu has no token and
  links to that confirmation page. Revert: restore the old route in
  `users/BPs/auth.py`; no data changes.
- **A failed session read is "unknown", not "signed out" (2026-11-13).** The React
  host marks a visitor whose cookie it could not read as `unverified`; the page asks
  the bridge and shows an error with retry instead of a sign-in prompt. Known edges:
  `unverified` is set for any failed read with a cookie, and the campaign and
  fundraising pages still treat an unverified visitor as a guest and redirect
  (reviewer findings 3 and 4 on #1145); fix them if they show up in practice.
- **Anonymous organization flash message (2026-11-12).** Shown through the bridge:
  an anonymous visitor only learns that a message is waiting, nothing about
  memberships.

## Public site

- **Finnish wording review (2026-11-14).** The maintainer requested an inventory
  and tracked language review of all public and regular-user UI, followed by
  user emails; admin is excluded. Changes concern text only, preserving layout,
  styling and behavior. Track source coverage and proposed replacements in
  `finnish-copy-inventory.md` and `finnish-copy-audit.md`. Backlog FI-COPY-02
  records the requested Claude workflow: use `gpt-6.1-sol`, when available,
  to check Finnish before merging. The routine is
  [finnish-premerge-review.md](finnish-premerge-review.md): admin excluded, user
  emails only after the UI pass, an unavailable model is never a pass, and it is
  not a required check.

- **React plus Go only (1 November 2026).** See AGENTS.md: there is no new Flask
  public surface, and the target is a standalone React app by January 2027.
- **Copy is Finnish-only.** New strings go in the TypeScript catalog
  (`frontend/src/app/i18n.ts`) and URLs in `config.urls`. English and Swedish are
  left to human translators; the catalog falls back to Finnish until then.
- **No icons in page headings.** Icons go beside labels, tiles and cards. Cancel
  buttons use the danger style.
- **`/developer` waits for a session scope (2026-11-12).** The pages are per-user and
  private, so React needs a new signed-in read; proposal in
  [developer-react-proposal.md](developer-react-proposal.md). `/download_material/<id>`
  stays in Flask as a download redirect.
- **Home page continuation (2026-11-12).** The Pride banner after the event grid was
  mistaken for "more events", so it now follows a continuation of the listing (#1098).
  Open: research suggests replacing the scroll auto-load with "Tänään" and
  "Viikonloppuna" shortcut chips and one clear button into `/demonstrations`.

## City admins

- **Target of at least two city admins per city, never enforced (2026-11-11).**
  Coverage counts distinct active accounts whose unrevoked city grant includes
  `ACCEPT_DEMO`, the same rule as `active_city_admins_for_city`.
- **Helsinki is moderated by the national admin team (2026-11-11).** Helsinki has a
  very large amount of activism and a small, fragmented scene in which people in
  different organisations may not cooperate because of conflicts. A local admin
  could therefore not review everyone's events neutrally. Helsinki shows
  "Valtakunnallinen tiimi", never recruits, and does not accept applications. The
  list is hardcoded in `admin_city_bp.py`, `go-api/city_admin_status.go` and
  `frontend/src/app/city-admin-route.ts`. Open: make it a `city_settings` flag if
  another city needs the same treatment.
  The public React city page shows the "Valtakunnallinen tiimi" note for the `national`
  band (2026-11-12), with no recruiting link; copy lives in `i18n.ts` `cityAdmin`.
- **The `/cities` list keeps its rule (2026-11-11).** It shows places that have had
  or will have demonstrations, plus enabled cities. Enabling a city used to be a
  workaround for appointing a city admin. The accounts dashboard can create a city
  admin for any of the 309 cities, and saving enables the city.
- **Public recruiting note (2026-11-11).** City pages with no city admin, or only one,
  invite visitors to help. A city without an admin says the national team reviews its
  events. Only a coverage band (`none`, `single`, `team`, `national`) is public, never
  names or counts.
- **Applications cover every city (2026-11-12).** The application form offers all
  catalog cities except Helsinki, whether or not they have demonstrations or are
  enabled; the Go handler already accepted any catalog key. The `/cities` list rule
  is unchanged. The form groups cities by coverage band under "Tarvitsemme adminia"
  (`none`), "Tarvitsemme lisää admineja" (`single`) and "Admintiimi on jo olemassa"
  (`team`), so applicants see where help is needed. Go serves all bands in one
  cached read, `GET /api/cities/admin-status`; still only the band, never names or
  counts.
  Cities with a full team stay selectable: backups are needed and admins rotate.
- **Applications (2026-11-11, #1091).** Signed-in users with a confirmed account
  apply at `/kaupunkiadmin`.
  - Go stores the application. A Flask job turns it into a `city_admin_application`
    case and notifies the national team in the inbox.
  - Local admins of the same cities can read and comment, but only global admins
    decide. Approving never grants rights; the role is granted in the accounts
    dashboard.
  - A user can have only one open application.
  - Retention and privacy notice: see the entry after this one.
  - **Unique index against simultaneous submissions (proposed, awaiting maintainer
    approval).** Partial unique index on `user_id` for open applications; a
    duplicate key returns 409 `APPLY_OPEN_EXISTS`. Not created yet. Details, the
    pre-check script and rollback are in
    [city-admin-applications.md](city-admin-applications.md#duplicate-submissions-proposed-awaiting-maintainer-approval).
- **Application retention and privacy notice (2026-11-12, approved by the
  maintainer 2026-11-12).** Open applications are kept until decided. After a
  decision, the free text (`about`, `activism`, `references`, `consent_at`) is erased
  from declined and withdrawn applications after 90 days and from approved ones after
  365 days. The row stays with user, cities, status and dates, so the one-open-
  application rule and the applicant's status list keep working. The erasing job
  `purge_city_admin_applications` (daily) does nothing unless
  `CITY_ADMIN_APPLICATION_RETENTION_ENABLED` is set; it ships off. The Finnish
  notice is shown on the form (`cityAdminApplyCopy.privacy*` in `i18n.ts`), and the
  numbers there must match `RETENTION_DAYS`. The flag may be switched on after a
  backup of `city_admin_applications`, with the dry-run count checked first. The
  same numbers are stated in `PrivacyContent.tsx` (`/privacy`, data list and
  retention section); change all three places together.
- **Case access (2026-11-11).** City admins' grants include `LIST_CASES`,
  `VIEW_CASE` and `EDIT_CASE`. The 2026-11-11 update is tagged
  `case_access_granted_for: issue-778`, so it can be reverted in one update.

## Preview images

- **New generated design (2026-11-11, #1090).** Generated previews are 1200×630 and
  use the site's brand style. An organizer's own uploaded picture is preferred over
  the generated one (#1092). All existing previews were regenerated, with a backup
  and a restore script kept on the production host. Old CDN objects are never
  overwritten.
- **Organizer design templates (PREVIEW-02, backlog).** Organizers can upload their
  own templates, saved in the organization panel and reused for recurring
  demonstrations. Admins can upload them for a specific series. See `TODO.md`.

## Operations

- **Recover page identity from available Apache logs (2026-11-16).** The maintainer
  requested a historical recount after generic pages had been combined into
  "Muu sivu". Known fixed routes now get distinct page IDs. Because origin
  requests cannot recreate browser pageviews exactly, recovered counts are
  displayed separately and never replace/add to existing analytics totals.
  Import is manual, backed up and reversible; production execution remains
  unverified after SSH stopped responding and further connections were prohibited.
  Procedure: [analytics-page-recovery.md](analytics-page-recovery.md).

- **Backups (2026-11-11).** A nightly core dump skips `super_audit_logs` and
  `admin_logs`, which are most of the database. A weekly full dump includes them.
  The Mongo tools authenticate the same way PyMongo does. Restoring from a backup
  is still unproven until the recovery drill (OPS-02) is done. Open: an off-site
  copy and its encryption key holder, and the alert channel. See
  [backups.md](backups.md).
- **Heavy database jobs make the Go API's startup fragile (2026-11-11).** A full
  dump loaded the database enough that a Go API restart failed its startup database
  check, causing about 35 seconds of 503s. Do not restart the public services while
  a full dump or another heavy database job runs.
- **Never run a dump by hand (2026-11-17).** A manual core dump during the day saturated the database network and the public API answered 503 (the lockdown gate fails closed when reads time out). Runbooks use the newest nightly core archive (02:32 UTC timer) as the backup and copy `config.yaml` and the environment file only; restart the public services only when no dump process is running (`pgrep -a mongodump`).
- **Analytics beacons (2026-11-11).** `POST /api/analytics/event` is served by Go,
  with `SITE_ANALYTICS_ENABLED=true` matching Flask. Pageviews and vitals stay in
  Flask. Roll back by removing the routing rule.

- **Hash developer app `client_secret` last (2026-11-13).** Flask and Go both store
  `client_secret` in plaintext; token secrets are already hashed. The maintainer
  pre-approved hashing it (a `client_secret_hash` marker, Flask and Go changed together,
  existing apps rotate) but only after everything else has moved to React and the Flask
  templates are retired. Needs a backup and an independent reviewer SHIP at that time.

## Fundraising page

- **Pre-launch mode (2026-11-13).** `/keräys` is public without login or campaign publication and is not linked from anywhere (no nav, footer, sitemap or front page; the existing link on `/kampanja/` shows only once the campaign is published). Until Helsinki's date reaches `start_date` it shows "Pienkeräys aukeaa", hides IBAN/BIC and payment links (in the page, the embedded bootstrap and `GET /api/fundraising/details`) and is `noindex, follow`. The opening day is the existing `start_date`, so no extra hardcoded instant. The account block needs `details_confirmed`, `publication_state: active`, organiser and recipient to be filled in; nothing is invented.
- **Opening reminder signup (2026-11-13, approved by the maintainer 2026-11-13).** Visitors may give their e-mail address on the pre-launch page to get one reminder when the collection opens (16.11.2026 00:00 Helsinki). Legal basis is consent; purpose is limited to that one reminder (plus one results message if an optional separate box is ticked, which is not implemented). Double opt-in with a 7-day single-use token stored hashed; data is only the address, consent time, consent text version and language. Deleted 30 days after the collection ends (17.3.2027), unconfirmed signups after two weeks, and at once through the link in any mail. Go stores signups, Flask mints tokens and sends through the e-mail queue. The reminder is queued once, only inside the send window and only with a separate approval flag; the first real bulk send needs the maintainer's go. Everything ships off. Details, switches, activation and revert: [fundraising-reminder.md](fundraising-reminder.md). The notice is in `PrivacyContent.tsx` and the form copy in `fundraising-reminder.ts`; change them together.

## Banner carriers on the Pride page

- **Call for banner carriers (2026-11-13).** The maintainer approved the mockup and collecting signups in principle. The goal is that every Pride in Finland has a Mielenosoitukset.fi banner, so the page calls for carriers ("Tarvitsemme banderollin kantajia jokaiseen Prideen"; "banderollin" added 2026-11-16 after the maintainer asked for better Finnish, since "kantajia" alone did not say what is carried). Signing up promises nothing binding, can be cancelled, and details are agreed later. The page names no events, dates, banner totals or per-event amounts. The form asks for first name or nickname, email, optional area, optional Pride, optional note and consent; no phone number, no address. Part 1 is the screen behind `pride_banner.json` (`volunteer_form`, off). Part 2 reuses the fundraising reminder system (#1146) as a second list type `banner_volunteer`: double opt-in, one-click removal and deletion after the Pride season unless re-consented. It starts after #1146 is on main and needs a separate security/privacy reviewer SHIP. The visual gate for Part 1 is a separate reviewer SHIP.
- **Banner carrier signup storage (2026-11-13).** Part 2 stores the signups as a second list on the fundraising reminder system (collection `pride_banner_volunteers`, own routes under `/api/pride-banner/volunteer`), not a second system: same guards, hashed single-use tokens, double opt-in and one-click removal. The only mail is the confirmation, and it never repeats text from the form. Contacting carriers is manual from a read-only CSV export. All data is deleted from 31.10.2027, after the Pride season, unless a later `retain_until` is set by a new explicit consent; unconfirmed signups go after two weeks. Everything ships off; activation needs the maintainer's go (`docs/pride-banner-volunteers.md`) and the privacy/security reviewer SHIP before merge.
## Fundraising bar

- **Slim bar above the header, off by default (2026-11-13).** The maintainer chose the cream look with an orange edge and no pill. It is controlled by `show_bar` in the fundraising configuration, takes its dates from the same configuration (shown dates, so the opening can be announced before confirmation), hides after the end date and on `/keräys`, and replaces the header link. Setting `show_bar` is the first public link to `/keräys` (pre-launch mode otherwise links it from nowhere), so it is the launch switch and is not gated by campaign publication. Dismissal lasts 7 days and ends early at a milestone. The bar never says what leftover funds do: the permit's primary use is the marketing campaign and the service's running costs, and leftovers go to shared demonstration equipment.

## Campaign page

- **Campaign page follows mockup v2.2 (2026-11-13).** The maintainer approved the mockup after several review rounds; copy, order and states are not redesigned. Dates and phase come from the fundraising configuration (see [campaign-page.md](campaign-page.md)); the Helsinki date picks the timeline dot, the donate slot and, from 16.11.2026, moves "Tietoa keräyksestä" above "Mistä on kyse". The page keeps the campaign publication gate. The mockup has no newsletter block, so the page no longer has one. The opening interest form and the volunteer form's new data model ship as separate pull requests.
- **Volunteer form data model (2026-11-16).** The maintainer approved the mockup's model: no phone number, role chips (`stickers`, `pride_banner`, `events`, `communications`), an optional area only when stickers are chosen, an optional note, no postal address. `POST /kampanja/api/volunteers` ignores unknown keys (so an old client's phone number is never stored) and rejects unknown roles and over-long values. Older documents keep their phone number and have no `roles` or `area`; readers treat the new fields as optional. Confirmation by e-mail is unchanged (token valid one day). Hardening after the security review: at most 5 signups per client and 2 per address per hour (hashed keys kept one hour; a repeat address silently gets no second mail), the link token is stored only as a hash, unconfirmed signups are purged a week after their link expires (on the next signup), CSV cells starting like a formula are escaped, format characters are stripped from free text. Retention decided by the maintainer (16.11.2026): volunteer signups are deleted after 31.5.2027. The daily job `purge_campaign_volunteers` (`python -m mielenosoitukset_fi.scripts.purge_campaign_volunteers --dry-run`) deletes all volunteer documents from 1.6.2027 00:00 Helsinki and does nothing earlier; it only deletes.
- **Opening interest form UI behind a flag (2026-11-16).** The form is built from the approved mockup and stays off (`opening_interest_form: false` in `fundraising.json`). Until the backend (double opt-in, reminder system list type) exists, submitting validates and then says nothing was sent. The ticket-willingness bands are the only euro amounts on the page.
- **Opening interest backend on the reminder system (2026-11-16).** The avajaiset interest form is a third list on the fundraising reminder system (after the reminder and the Pride banner carriers): Go routes `POST /api/campaign-opening/interest` (+ `/confirm`, `/unsubscribe`), collection `campaign_opening_interest`, double opt-in with hashed one-time tokens, one-click removal, Flask mails. Fields: e-mail, optional city, optional willingness to pay and price band, consent time and version; no name, IP or phone. Unconfirmed rows are deleted after two weeks, everything on 13.3.2027 (a week after the opening; the consent text promises deletion after the opening). Everything ships off: `OPENING_INTEREST_SIGNUP` (Go), `OPENING_INTEREST_MAILS_ENABLED` (Flask), `opening_interest_form` (UI), the Apache rule and the unique index each need the maintainer's go. Details: [campaign-opening-interest.md](campaign-opening-interest.md).
- **Opening interest form activated (2026-11-17).** On the maintainer's go the backend was switched on in production: unique index `email_unique` on `campaign_opening_interest`, `OPENING_INTEREST_MAILS_ENABLED` (systemd drop-in `opening-interest.conf`), `OPENING_INTEREST_SIGNUP: true` in `config.yaml` with one Go restart, the Apache rule (already deployed). One test signup with the maintainer's own address was confirmed by mail and then deleted. The form itself appears with `opening_interest_form: true` in `fundraising.json`. Revert: set the flag to false, remove `OPENING_INTEREST_SIGNUP` and restore `/etc/mielenosoitukset-api/environment` from the copy in `/root/opening-interest-activation-20261117T072858`, delete the drop-in; the database revert point is the nightly core archive `mielenosoitukset-core-20261117T024423Z.archive.gz`.

- **Suggestion IP addresses are removed after 90 days (2026-11-18).** The maintainer decided to keep storing the submitter's IP address and User-Agent on organisation and demonstration suggestions (Flask `save_suggestion` and `suggest_change`, and the Go handlers), but to remove them after 90 days. The daily job `purge_suggestion_ips` (`python -m mielenosoitukset_fi.scripts.purge_suggestion_ips --dry-run`) unsets `ip` and `user_agent` on `demo_suggestions`, `meta.ip` and `meta.user_agent` on `org_edit_suggestions`, and the same fields inside `cases.suggestion`, for suggestions created more than 90 days ago. It never deletes suggestions or cases. The scheduled job changes nothing until `SUGGESTION_IP_PURGE_ENABLED=1` is set in the Flask environment; `--dry-run` always works and only counts. The privacy notice does not list suggestion data, so it is unchanged. Moderator e-mails already sent keep the address; they are not touched.

- **Off-site backup copy and e-mail alerts (2026-11-17).** The maintainer decided
  (1) the off-site copy is an encrypted copy to their homelab, (2) the maintainer
  alone holds the encryption key, (3) backup failures alert by e-mail to the
  maintainer. Built as: `age` encryption on lc-main with a public recipient key
  only (the private key is never on the server or in the repository), rsync over
  SSH with a restricted write-only key to a landing directory, a receiving-side
  script that moves verified archives to a vault and applies the off-site
  retention (30 days core, 90 days full), a freshness check that alerts when the
  last off-site core copy is older than 36 hours (full: 9 days), and
  `backup-notify.py` replacing the log-only `OnFailure` line (site SMTP account,
  recipient from `/etc/mielenosoitukset/backup.env`). Hostnames, keys and the
  address live only on the servers. Losing the private key makes the off-site
  copies unreadable, so the maintainer keeps a second copy. Install and revert:
  [backups.md](backups.md). Not installed until the maintainer says go.

- **Dumps must never hurt the site; no ad-hoc daytime dumps (2026-11-17).** An
  ad-hoc core dump made lc-db's network stack saturate (ksoftirqd 100 %, 50 %
  packet loss) and the site answered 503. Maintainer: "ship a bit slower rather
  than the site going down". Rules: (1) dumps run only 03:00-05:00 Helsinki, from
  the timers (01:30 UTC), never by hand by day; (2) they are throttled (`pv`,
  default 1 MiB/s compressed), read one collection at a time, never overlap, and
  skip with an alert mail when lc-db ping is already high; (3) activations and
  migrations back up with the latest nightly archive plus a `mongoexport` of only
  the touched collections, not a new dump; (4) the weekly full dump becomes monthly
  if a watched run shows it cannot be made gentle. The rate is unmeasured until a
  watched run with the maintainer's go. Dumping on lc-db with `rsync --bwlimit` is
  the documented next step, not built. See [backups.md](backups.md).

## Go write APIs

- **Contact form `POST /contact` in Go, shipped dark (2026-11-17).** The maintainer approved exact Flask parity, shipped off, with activation per API only on their go. Go enqueues the support mail (Flask's worker sends it) and counts the same Flask-Limiter quotas (`limits.counters`); Flask stays the session owner and answers a read-only token check on `/api/session` (header `X-Contact-Form-Token`, behind `AUTH_BRIDGE_SCOPES`); Go consumes the token hash once. Flag `CONTACT_GO_ENABLED`, default off, no Apache change, no new schema. Known gap before activation: Go cannot write Flask flashes, so the React screen must show the `?contact=` outcome. Details, activation and revert: [go-contact.md](go-contact.md).

## Suggestion forms in Go

- **Suggestion writes move to Go, dark first (2026-11-17).** `POST /organization/<id>/save_suggestion` and `POST /suggest_change/<demo_id>` have Go handlers with exact Flask parity (stored fields, case record, queued moderator mail, validation, redirects), behind `SUGGESTIONS_GO_ENABLED` (default false, no Apache rule). Defaults chosen by the coordinator because the change is revertible: no new schema, index, session or auth change; the reporter's account id is read only through the existing read-only session call; Flask's worker keeps sending mail. Activation, the flash replacement and the case-number allocator are separate approvals. See [go-public-api.md](go-public-api.md).
- **Suggestion forms: answers to the open questions (2026-11-17, coordinator, revertible because the routes ship off).** (1) The missing flash message is replaced by a React notice driven by a query flag, in a separate small pull request before activation; it needs a visual reviewer SHIP. (2) An atomic case counter is a separate change, proposed at activation. (3) A failed mail insert is only logged, as in Flask. (4) Stricter per-client limits are decided at activation. (5) A malformed organisation id stays a 400. (6) Markdown edge cases outside the test corpus are accepted, because output is escaped and allowlisted.
- **Bio written by Go is limited to 5000 characters (2026-11-17).** Flask stores any bio; 5000 is the length the page already reads and the old textarea allowed. The Go route
  `POST /api/account/profile` (behind `ACCOUNT_SETTINGS_WRITES`) trims it, strips bidi override characters, refuses more than 5000 characters
  and control characters other than newline and tab, and stores an empty bio as `null`. The
  Flask path is unchanged. See [account-pages-react.md](account-pages-react.md).

## Account settings in Go

- **General settings written by Go, change mail queued by Go with Flask parity (2026-11-18, coordinator).** `POST /api/account/settings` (behind the account write flag, no new flag) changes display name, city, language and dark mode, copies a changed display name to `users.displayname`, and inserts the same "Käyttäjäasetusten muutokset" mail Flask would into `email_queue`; Flask's worker sends it, Go never does. Parity is pinned by a golden fixture from the real Flask render. The mail's field order is fixed (city, display_name, language, dark_mode) because Flask's is a set. Go is stricter on input (field list, 40/100 character limits, city list, `fi` only). Dark mode is a stored preference only. Activation needs the maintainer's go. See [account-pages-react.md](account-pages-react.md).
- **Backup alerts go to Bark push, e-mail is the fallback (2026-11-18).** The
  maintainer prefers a Bark push (iOS) over e-mail for ops alerts. `backup-notify.py`
  tries Bark first (`BARK_KEY`, `BARK_URL` default `https://api.day.app`, HTTPS only,
  10 s timeout), then e-mail if `ALERT_EMAIL` is set, and exits non-zero when no
  channel works so the failure is visible. The key lives only in the root-only
  `backup.env` on the server and is never logged. The push names only the label and
  the failed check. Setup: [backups.md](backups.md).
- **Suspended, banned and deactivated accounts have no public profile (2026-11-18, maintainer).** The public profile page and the Go profile read answer "not found" for an account with `security_suspension.active`, `banned: true` or `active: false`, the same as for a missing user, so a profile cannot show that an account is suspended. Deleted or anonymised accounts need no rule: the code has no such marker and a deleted account has no document. Unconfirmed accounts stay visible. Flask `profile.py` and `go-api/profile.go` apply the rule; revert by removing the check in both. See [account-pages-react.md](account-pages-react.md).
## Lockdown gate when the status read fails

- **A failed lockdown read no longer closes the site (2026-11-17, approved by the maintainer 18.11.2026, including staying open after a restart during a database outage).** The shared public gate (Go API, React host, Flask; collection `panic`) used to fail closed after 30 seconds without a successful read, so a slow database closed the whole site on 17.11.2026 at 09:31 and 09:34–09:43 Helsinki although no lockdown was set ([investigation](investigations/site-503-2026-11.md)). Rule in all three implementations: a successful read that says locked applies at once and is sticky (a failed read never reopens a locked site); after a successful "not locked" read a failed read keeps that answer for 15 minutes (`PUBLIC_GATE_STALE_SECONDS`) and then closes; a process with no successful answer yet (restart during an outage) stays open; the local emergency override closes the gate on every failed read, in any state (`PUBLIC_LOCKDOWN_OVERRIDE=closed` or the file `PUBLIC_LOCKDOWN_OVERRIDE_FILE`, default `/etc/mielenosoitukset/lockdown-override`). Read failures are logged (at most one line per 30 seconds, with a count since start). Trade-off: a database outage cannot be used to bypass a lockdown, because the lockdown lives in the same database, a locked answer is sticky, and a new lockdown cannot be written while the database is down; the cost is that during an outage a lockdown set from another process or just before a restart is applied only once a read succeeds, or through the override.

## Decisions log merges as a union

- **Two branches that both add entries keep both (2026-11-19, maintainer request).** `.gitattributes` marks `docs/decisions.md` with `merge=union`, so a local `git merge origin/main` keeps both sides instead of stopping on a conflict. GitHub web and merge-queue merges ignore this setting, so a branch that conflicts there still needs `main` merged in locally first. Revert: delete the line from `.gitattributes`.

- **Token pages stop writing on GET (2026-11-20, maintainer).** `/kampanja/confirm/<token>`, `/auth/confirm_email/<token>` and `/orgs/accept_invite` confirm or join in the GET itself, so a mail scanner or prefetch can trigger them. Each becomes a React page with a confirm button plus a Go POST that does the write, one draft PR per page, all dark (own env flag, default off; Apache rules commented until the maintainer's go; Flask stays live). The campaign volunteer confirmation is first: `POST /api/campaign-volunteers/confirm`, flag `VOLUNTEER_CONFIRM_GO_ENABLED`, atomic consume, same outcomes and status codes as Flask. See [volunteer-confirm-react.md](volunteer-confirm-react.md). Revert: re-comment the Apache rules; Flask is untouched.

- **E-mail confirmation follows the same split, key question open (2026-11-20).** `GET /users/auth/confirm_email/<token>` becomes a React button page plus `POST /api/account/confirm-email` in Go (flag `EMAIL_CONFIRM_GO_ENABLED`, off). Go must verify Flask's HS256 token, which needs the signing key; the route takes it from `EMAIL_CONFIRM_JWT_KEY`. Whether that is Flask's `SECRET_KEY` (also the session key) or a derived key minted by a changed Flask issuer is for the maintainer and the security review to decide before activation. See [email-confirm-react.md](email-confirm-react.md). Revert: re-comment the Apache rules, unset the variables.

- **Organization invitations: read and accept in Go, dark (2026-11-20).** `GET /users/orgs/accept_invite` joins in the GET itself. It becomes a React page that reads `GET /api/organizations/<id>/invitation` and joins with `POST …/invitation/accept` (flag `ORG_INVITE_GO_ENABLED`, off, inside the auth bridge, same guards as the other signed-in Go writes, atomic claim of the invitation, `$setOnInsert` membership, audit row, system teams refused). Exact e-mail matching is kept. See [org-invite-react.md](org-invite-react.md). Revert: re-comment the Apache rules, unset the flag.
## Demo suggestions in cases and case notices

- **Demo suggestions become cases; new cases notify in the inbox only (2026-11-20, maintainer request, issue #1217).** Olivia asked that demonstration suggestions also show up in the admin cases, because on the separate suggestions page they were easily missed, and that cases send notifications. Notices go to the site inbox, and she then asked for e-mail too: admins with case rights everywhere (full admins and global case grants) always get the mail, other admins (city-scoped case rights) opt in on the case list (`users.case_notice_emails`), and each case records and shows which admins were notified and mailed ("for admins always but we should know which admin gets pinged"). Mails are queued one recipient each, only when the inbox notice is first created, so reruns never mail twice. The support mailbox keeps its existing suggestion mail. The background job `deliver_case_notices` (every 2 minutes) opens a `demo_edit_suggestion` case for each open `demo_suggestions` document, reusing the suggestion's `_id`, and closes it once the suggestion is applied, rejected or closed. A job rather than the write handlers covers the Flask and dark Go suggestion writers alike without touching their parity. The case copies no IP address, User-Agent or suggested values; review and apply stay on the suggestion page. Recipients are the active accounts for which `case_scope_query(user, "VIEW_CASE")` includes the case, so city admins only hear about their own cities; the inbox rechecks that and hides the notice when the case closes. Only cases created in the last two days are notified, so switching on does not flood inboxes with history, and city admin applications keep their own national-team notice. All parts are off until `DEMO_SUGGESTION_CASES_ENABLED`, `CASE_NOTIFICATIONS_ENABLED` and (for mail) `CASE_NOTIFICATION_EMAILS_ENABLED` are set in the Flask environment; unsetting them is the rollback.
- **Case suggestions, notices and mails switched on by default (2026-11-20, maintainer go).** Olivia approved switching all three on and asked for it through a pull request rather than server access. The three switches now default to on in code; setting one to `0`, `false`, `no` or `off` in the Flask environment turns that part off, and reverting the pull request restores the old off-by-default behaviour. Previews and tests stay safe: previews run with `ENABLE_EMAIL_WORKER: false` and a local mail catcher, and the tests set every switch explicitly.
- **Structured reasons for rejecting a demonstration and closing a case (2026-11-20, maintainer request).** Olivia asked for a better reject flow with duplicate as a reason. Both dialogs now offer a fixed list (handled (close only), duplicate, spam, out of scope, not enough information, other) plus a note, required only for other. A duplicate names the original: a demonstration picked by title search when rejecting, a case number when closing; the case history stores the key, label, note and the original's id and shows a link to it. A city admin can only point at a case they may view, and an unknown or hidden case gets the same "not found" answer. Requests with the old free-text `reason` are read as other with that note, so older clients keep working. Closing also fills `meta.closed_reason` (shown on the case), and reopening clears it.
- **Campaign volunteer signup in Go, shipped dark (2026-11-20).** `POST /kampanja/api/volunteers` has a Go implementation with exact Flask parity (validation, role chips, hashed rate-limit documents in `volunteer_signup_attempts`, hashed link token, retention fields, welcome mail queued for Flask's worker). Flag `CAMPAIGN_VOLUNTEERS_GO_ENABLED`, default off, needs the auth bridge (private-campaign preview); no Apache rule is active and no schema changes. `GET /kampanja/confirm/<token>` stays with the token pages work; the Go side offers `confirmCampaignVolunteer` as an atomic, hash-or-legacy-token confirm. Activation and revert: [go-campaign-volunteers.md](go-campaign-volunteers.md); needs the maintainer's go and an independent security SHIP.

## Deleting an account

- **Delete what exists only for the account, keep abuse evidence pseudonymised, seal the identity offline (2026-11-20, maintainer, issue #596).** Olivia chose to keep what is needed to investigate abuse, to keep every record of an account with extra permissions (role other than `user`, `global_admin` or any global permission), and to make what is kept not directly linkable to the person. When an admin deletes an account, `admin/user_erasure.py` deletes its sign-in means and settings, deletes its other personal records unless it is privileged, and pseudonymises the rest: the account id becomes a stable fake id, the email, username and display name become a `poistettu-…` alias; IP addresses stay for abuse work. The real identity is stored only sealed with a public key (`ERASURE_SEAL_PUBLIC_KEY`); the private key is kept offline. Nobody can unseal in the app (Olivia, 20.11.2026, option "offline key only"); the unseal procedure will live in the separate administration system she is building. After `ERASURE_RETENTION_DAYS` (default 730, two years) the seal and the kept non-audit records are deleted; audit records stay pseudonymised. Procedure: [account-erasure.md](account-erasure.md). The privacy text needs a lawyer check.
## Audit trail for every write

- **Every database write is audited at the driver (2026-11-20, maintainer request).** Olivia: "every edit etc etc no matter how it's done should leave a trace so no untraceable edits can happen". Instead of a log call at each of ~480 write sites, every MongoDB client (Flask `DatabaseManager`, scripts, migrations, the Go API) carries an audit listener that writes one `audit_events` document per write: actor (ids only, no email, name or IP), path, filter, diff or inserted document with secrets redacted, outcome. Telemetry counters and the job leader lock are skipped, with reasons in [audit-trail.md](audit-trail.md). Retention 730 days by TTL (`AUDIT_RETENTION_DAYS`), matching account erasure. `tests/test_audit_trail_coverage.py` fails when code opens a client without the listener. Writes from outside the code base (mongosh, restores) are not covered yet. Revert: revert the pull request; `AUDIT_TRAIL_DISABLED=1` switches it off per process.
- **Old admin request log rows lose their stored credentials (2026-11-20, maintainer go).** Before the logger fix in #1247, admin request log rows stored the raw request (session cookie, Authorization header) and the admin's password hash. A one-time background job (`scrub_admin_log_secrets`) unsets `request.environ`, `request.headers` and `user.password_hash` on rows from before 20.11.2026 08:00 UTC. It keeps the rows and copies the User-Agent to `request.user_agent` first. The result (counts before and after) is written to `maintenance_markers` and shown as an `admin_log_secret_scrub` entry in the admin log. No separate backup copy is made, since it would only duplicate the credentials. The weekly full archive keeps the original rows until it expires (35 days locally, 90 days off site). Switch off: `ADMIN_LOG_SCRUB_DISABLED=1`.

## Hallinto city-admin appointment (C02)

- **`city_admin_v1` is exactly nine permissions for one city (2026-11-20, maintainer decision A3).** `LIST_DEMOS`, `VIEW_DEMO`, `EDIT_DEMO`, `ACCEPT_DEMO`, `CREATE_DEMO`, `GENERATE_EDIT_LINK`, `LIST_CASES`, `VIEW_CASE`, `EDIT_CASE`. The city is enabled only when the Hallinto decision allows it; otherwise an inactive city is rejected. Hallinto confirms an appointment only when the owner receipt reports exactly these, sorted. The writer lives in `mielenosoitukset_fi/admin/city_grants.py` and is shared with the existing user admin. It has no route yet: the internal route waits for Hallinto's action-bound fresh proof (I02) and stays disabled by default. Revert: revert the pull request; the legacy views keep working because they call the same writer.
- **Appointed city grants end on time (2026-11-21, Hallinto B3).** An appointment carries `valid_until`, the exclusive end instant; Flask stores it as `expires_at` and every permission read ignores the grant from then on, without a cron job. Grants without `expires_at` (all legacy grants) never expire.
- **Organization and demonstration change history from the audit trail (2026-11-20, maintainer request).** Olivia: "Myöskin organisaatioilla pitäis olla muokkaus ja luontihistoriat". Organizations get an admin Muutoshistoria page and the demonstration edit history page shows every recorded change, both read from `audit_events` ([audit-trail.md](audit-trail.md#reading-the-history)). Access follows the existing edit rights. History starts when the trail was deployed.

## Public page look

- **One plain page heading for public pages; /ohjeet stays as it is (2026-11-20, maintainer).** An audit of every public React page found four different hero styles (navy gradient card, centred gradient card with an icon tile, gradient with glow on /ohjeet, pale card with a coloured stripe), which made the site look template-made. Olivia chose "fix the bugs and unify the look": the shared `.public-page-header` and the organisation page heading become a plain left-aligned title with a muted lead and a divider, like the cities, listing and status pages, and event cards are to be left-aligned with one login prompt per page. She asked to keep /ohjeet as it is for now. Admin pages are not affected. Revert: restore the gradient rules in `user-workspace.css` and `organization.css`.
## Site-wide cap on signup forms that send mail

- **Signups that mail a typed address get a site-wide cap (2026-11-20, maintainer go via the coordinator).** Campaign volunteer signup, fundraising reminders, the Pride banner list and campaign opening interest had per-IP and per-address limits only. After INC-2026-0001 the contact form got a global limit because many sources together get past per-IP limits; these forms now get the same: per form, 30 signups per 10 minutes and 200 per day (`go-api/signup_mail_cap.go`). Over the cap the form answers like the per-IP limit (429, "try again later"). The first refusal in a window writes one `signup_mail_cap_reached` entry to the admin log, so the team sees it without a mail per request. The counters are Go rate rows in `demo_submission_tokens`, removed by the existing cleanup after 48 hours. Change the numbers in `signupMailCaps`; revert the pull request to remove the cap.

## Token confirm pages switched on

- **Volunteer and e-mail confirmation pages switched on (2026-11-21, maintainer go relayed by the coordinator).** After #1322 was live and green, `VOLUNTEER_CONFIRM_GO_ENABLED`, `EMAIL_CONFIRM_GO_ENABLED` and the derived `EMAIL_CONFIRM_JWT_KEY` are written by `deploy/go-api/configure_environment.py` on every deploy (on by default with public submissions, `VOLUNTEER_CONFIRM_GO: false` / `EMAIL_CONFIRM_GO: false` opt out), and their Apache rules are active. `release_routing.sh` installs the include only after both Go routes answer GET with 405. The deploy derives the key itself because the Go environment file is rewritten on every deploy, which would drop a hand-written key. Organization invitations follow in a separate change, since they also need `BOARD_ORG_ID`. Revert: re-comment the Apache rules and deploy.

- **Organization invitation page switched on (2026-11-21, maintainer go in the thread).** Olivia confirmed `BOARD_ORG_ID` is set in production `config.yaml`. `configure_environment.py` now writes `ORG_INVITE_GO_ENABLED` with the auth bridge on (default on, `ORG_INVITE_GO: false` opts out) plus both team ids, and only when both ids are valid; the Apache rules are active and `release_routing.sh` checks the Go accept route answers GET with 405 first. Revert: re-comment the Apache rules and deploy.

## National moderators on duty shifts

- **Moderation rights only during a duty shift, checked on every request (2026-11-21, maintainer).** Per the hallintomalli OT appoints national moderators; Olivia's handoff requires that their rights are active only during a shift, enforced on the server and not dependent on a scheduled job. Olivia chose the right set on 21.11.2026: `LIST_DEMOS`, `VIEW_DEMO`, `EDIT_DEMO`, `ACCEPT_DEMO`, `LIST_CASES`, `VIEW_CASE`, `EDIT_CASE` and `GENERATE_EDIT_LINK`, nationwide (no deletion, no user, organization or city management), and a 30-minute grace after a shift ends so started work can be finished. Appointments live in `national_moderators`, shifts in `moderator_shifts` (`utils/duty_shifts.py`). The login loader adds the shift's rights to the signed-in account for that request only; they are never saved, cannot be passed on through the user editor, and API, MCP and Hallinto lookups never see them. The moderator books their own shifts within the appointment and an OT member approves them; only an approved shift gives rights, and an appointment may allow booking without approval (`self_booking`) (Olivia, 21.11.2026). A moderator can never approve their own shift. Booking, approval and the list of all shifts with their states are on the admin page Vuorot (`/admin/vuorot/`), not in Hallinto (Olivia, 21.11.2026), so moderators need no Hallinto account; the appointment lists the accounts that may approve its shifts (`approvers`, OT members sent by Hallinto). A moderator with an unrevoked appointment can open the admin to book shifts, but gets no right off shift. Cancelling a shift, freezing or revoking the appointment ends the rights on the next request with no grace. Every write on shift carries `duty_shift_id` in the audit trail, and every state-changing request is logged in `moderator_duty_uses`. Writes come only from the Hallinto route, which follows the city-admin contract and is a separate change. Revert: revert the pull request; existing data then grants nothing.

- **One admin page for volunteer and sign-up lists (2026-11-21, maintainer: "control all volunteer stuff in the same way").** Campaign volunteers, Pride banner carriers and opening-event (avajaiset) sign-ups are shown on `/admin/kampanja/ilmoittautumiset`, one tab each, through `utils/volunteer_registry.py`. The collections and public forms stay separate (different data and retention). Admins can list, export CSV (formula-safe) and delete with a required reason; each export and delete writes a `volunteer_admin_actions` row (who, when, why, no personal data). Admins no longer confirm or edit sign-ups: only the person confirms, from the e-mail link. Applications and requests (city admin, organization) stay in cases. A new sign-up list is added by one entry in the registry.
