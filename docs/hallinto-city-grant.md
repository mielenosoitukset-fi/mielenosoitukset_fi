# Hallinto city appointment contract

C01 defines the proposed v1 owner contract before adding writes. Flask owns the wire contract and fixtures. Hallinto pins a reviewed commit before using them. This PR adds no endpoint or privilege change. [OpenAPI v1](hallinto-city-grant-v1.yaml) · [Synthetic examples](hallinto-city-grant-fixtures.json)

## Current owner path

- `admin_user_bp.py:_sync_city_scope_grant` normalizes city keys, adds listing/view permissions, writes `admin_scope_grants`, can enable a city and update `users.role`, and records history. Its callers in create/edit/save and any additional operational writer must share the same conditional operation before the API is enabled.
- `_can_manage_scope_grants`, `_check_user_target` and role/target checks govern current admin authority. `security_cases.py` handles protected targets and emergency suspension separately. A Hallinto attestation must never replace technical actor/target checks.
- `User.admin_scope_grants` accepts BSON IDs and legacy strings, and city permissions can have additional effective behavior through wrapper/role logic. Confirm a fresh user and permission check rather than reporting the saved grant payload as the outcome.
- `admin_case_bp.py:set_application_status` controls city application status separately. The pilot links a case and decision. Changing application status or sending applicant mail requires a separately approved effect. Helsinki's recruitment exception remains.

## Implementation status (C02)

`mielenosoitukset_fi/admin/city_grants.py` is the shared writer. `sync_city_scope_grant` takes the acting account explicitly; the create, edit and save views call it with the signed-in administrator. `apply_city_admin_v1` implements the appointment, `read_city_grant_state` the state read, and `read_operation_receipt` the receipt lookup. The operation journal is the `city_grant_operations` collection, unique on (integration, actor, `Idempotency-Key`). No route calls them yet. The internal route needs workload mTLS and the action-bound fresh proof from I02, and ships disabled by default.

Behaviour of the pilot operation:

- The actor is reloaded and must be an active account that may manage scope grants. Self-appointment is denied. A missing target is reported as `denied`.
- Eligible targets are active, confirmed, unprotected `user` accounts with no global permissions and no active scope grant of any kind.
- `expected_state` is a SHA-256 digest over the target's role, global admin and governance flags, global permissions, activity, protection, active grants and the set of enabled cities. Profile text does not affect it.
- Every city grant write, legacy or appointment, takes a per-account fence (`city_grant_fences`, keyed by account id, ten-minute lease). An appointment checks state, writes and rereads under one fence, and reports `reconciliation_required` if it no longer holds the fence before confirming. A competing writer gets 409 before writing anything.
- The writer refuses, before any write, to replace an existing city grant or to enable a city the request did not allow.
- After the write, a freshly loaded user must have the role `city_admin`, the city enabled, exactly one city scope and exactly the nine permissions through `User.has_scoped_permission`. Anything else is `reconciliation_required`.
- A `pending` operation whose last update is older than five minutes is reported as `reconciliation_required` on replay or lookup. It is never retried automatically.
- `valid_until` (required) is the exclusive end instant in UTC; Hallinto sends the Helsinki midnight after the last valid day. It must be ahead and at most about five years out. It is stored as the grant's `expires_at`. Every grant read (`utils/scope_grants.py`, and `liveGrantClauses` in the Go API) ignores a grant from that instant, so no job revokes it. A legacy admin save keeps an existing expiry. A legacy save after the end revokes the ended grant (`revoked_reason: expired`) and answers 409 if the form re-submits the same scope, so a stale form cannot recreate it without an end date. Receipts and the state read echo `valid_until`. Justification and meeting records stay in Hallinto.
- Any city grant still implies the read-only organization permissions in `CITY_ADMIN_ORGANIZATION_PERMISSIONS` (list and view). The receipt reports the city scope only.

## Pilot scope and security

The first write is one additive city-admin appointment for an eligible active account without an existing city grant. This limits replacement behavior and prevents a city-key union from broadening a permission across cities. A3 must approve the exact `city_admin_v1` preset, expected implicit permissions, role effects and eligible accounts. No arbitrary capability list, expiry, multi-city replacement, or protected-role changes through this contract.

Use authenticated internal ingress for the workload and a separate audience-bound owner-issued human proof. For writes require a fresh authorization for the exact account, city, template, city-enablement allowance, expected owner state, decision revision and operation key. Verify Hallinto's signed authority attestation for the same intent. Claim formats and factor issuance belong to I01/I02 and A2. Reject self-escalation, inactive accounts, unsupported targets/cities and unauthorized actors without exposing unrelated records.

`expected_state` is an opaque version of the owner inputs that affect the command. Cover target role/activity/security, active grant state and city enablement or recheck them under the same owner fence. Reject any unexpected prior grant or additional implication. Require explicit allowance if the operation would enable a city. Canonicalize validated stable fields before digesting. Proof tokens can be renewed without changing the intent. Log neither proof nor attestation bytes.

## Outcome and reconciliation

Scope the key by authenticated integration identity and acting subject. An exact replay after successful authorization returns the original committed receipt even if later revocation changes current access. Never reapply it. Same key and different intent returns conflict. Revalidate receipt visibility on lookup. A different operator needs an explicitly authorized owner reconciliation path, with separate audit.

First allocate a durable operation and serialize with the existing writer. Use a transaction where proven or a fenced, resumable journal where effects span writes. After mutation, check the exact effective city/permission result with newly loaded owner state. Return `confirmed` only when it matches the authorized plan. Report later drift separately. Unknown or partial operations stay pending/reconciliation-required. Locate them by the original key, preserve the receipt and never guess a successful outcome. Logs must correlate actor, case decision, account, operation ID and state transitions.

Until C02/C04 prove compatibility, keep API disabled. Required staging checks include native UI parity, no cross-city or protected effects, duplicate commands, competing saves, revocation racing with grants, interruptions between legacy effects, and authoritative positive and negative permission readback. Recovery disables new API operations and reconciles pending receipts. Reversal is a new authorized owner action.

## Internal route and fresh proof (C02 step 3, I02 remainder)

Off unless `HALLINTO_CITY_GRANT_CONFIG_FILE` names a restricted JSON file (mode 0600, owned by the service user or root). In production the file is `/etc/hallinto-city-grant/config.json`, in a directory owned by `www-data` with mode 0700, set by a systemd drop-in for the Flask unit. Never place it in `/etc/mielenosoitukset`, which deploys keep root-only for backup secrets. The deploy refuses to reload when the service user cannot read the file (see `docs/production-deployment.md`). The file contains:

- `proxy_secret_sha256`: lowercase SHA-256 of the secret the internal vhost adds as `X-Hallinto-Proxy-Secret`. A client certificate is public, so the forwarded certificate alone is not proof that the request came through the mTLS vhost.
- `workload_cert_sha256`: lowercase SHA-256 fingerprints of the DER client certificates Hallinto presents.
- `attestation_public_key`: Hallinto's Ed25519 public key, raw 32 bytes in base64url.
- `return_url`: the exact `https://` Hallinto URL that receives the proof.

Approval flow (Olivia, 21 November 2026: approve in Hallinto, step up on Flask by redirect, passkey or password+TOTP, 15 minutes):

1. In Hallinto the verifier presses "Hyväksy". Hallinto redirects the browser to `/admin/hallinto/approve?r=<base64url JSON terms>&state=<16–128 chars>`. The terms are `account_id`, `city`, `template`, `allow_city_enablement`, `decision_id`, `decision_revision`, optional `application_id`, and `valid_until`.
2. Flask shows the terms. The verifier must be allowed to manage city grants and must have stepped up with a passkey or password plus TOTP within 15 minutes. Password-only elevation does not count. Without either factor the page links to the account settings.
3. On "Vahvista nimitys", a same-origin POST, Flask fills `expected_state` from current owner state. It issues a proof (single use, five minutes) bound to the actor, the audience `hallinto-city-grants`, the action and the intent digest. It then redirects with 303 to `return_url#state=…&proof=…&expected_state=…`. The fragment keeps the proof out of server logs and Referer headers.
4. Hallinto checks `state` and signs `"hallinto-city-admin-v1-attestation\n" + intent_digest` with its Ed25519 key. It calls `POST /internal/hallinto/v1/city-appointments` with mTLS, `Authorization: Bearer <proof>`, `Idempotency-Key` and the full request, including `expected_state` and `authority_attestation` (base64url signature).

`intent_digest` is the SHA-256 hex digest of the canonical intent: the request without `authority_attestation`, plus `"permissions"` (the nine A3 permissions, sorted). It is serialized as JSON with sorted keys, no whitespace and unescaped UTF-8, which is Python's `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. In Go, marshal a map with `SetEscapeHTML(false)` and drop the trailing newline.

The first use of a proof binds it to its `Idempotency-Key`. Only a retry of the same command can present the proof again, and only until it expires. `GET /operations/by-key` takes an unexpired proof already bound to that `Idempotency-Key`. `GET /city-grants/{account_id}` takes an unexpired proof issued for that account. The actor is re-checked. The approval page and its redirect send `X-Frame-Options: DENY` and `frame-ancestors 'none'`.

Apache (internal vhost only, production activation needs Olivia's go-ahead): `SSLVerifyClient require` against the Hallinto CA. Overwrite the forwarded headers on every request, so a client value never reaches Gunicorn:

    RequestHeader unset X-SSL-Client-Cert
    RequestHeader unset X-SSL-Client-Verify
    RequestHeader set X-SSL-Client-Verify "%{SSL_CLIENT_VERIFY}s"
    RequestHeader set X-SSL-Client-Cert "expr=%{escape:%{SSL_CLIENT_CERT}}"
    RequestHeader set X-Hallinto-Proxy-Secret "<secret from a root-only include>"

Route only `/internal/hallinto/` through that vhost. Include `deploy/apache/hallinto-public-vhost-guard.conf` in every public vhost: it strips all three headers and denies `/internal/`.

## Ending an appointment (Hallinto B3c)

`POST /internal/hallinto/v1/city-appointment-ends` revokes or freezes a confirmed appointment at once. It needs the same three things as an appointment: mTLS workload identity, an actor proof and Hallinto's attestation.

Body: `appointment_id` (the Idempotency-Key of the original appointment), `kind` (`revoke` or `freeze`), `account_id`, `city`, `template` (`city_admin_v1`), `decision_id`, `decision_revision` and `authority_attestation`. The `Idempotency-Key` header is the end's own id.

- **Different signature.** The attestation signs `"hallinto-city-admin-end-v1-attestation\n" + end_digest`, so an appointment signature can never end a grant. The digest is built like the appointment digest from the body without `authority_attestation` (sorted keys, no whitespace, no `permissions`).
- **Different proof.** Flask issues the proof for the action `city_admin_end_v1`. Send the verifier to `/admin/hallinto/approve` with `r` holding the end terms (`appointment_id`, `kind`, `account_id`, `city`, `template`, `decision_id`, `decision_revision`). There is no `expected_state` in the end fragment, because ending is idempotent and only removes access.
- **What it does.** Flask checks that `appointment_id` is a confirmed appointment of the same account and city. It then ends that appointment's own grant (Flask stored its id when it confirmed the appointment) under the account fence, and returns the role `city_admin` to `user` only if no other live city grant remains. A later appointment or a legacy grant for the account is never touched. Both kinds remove access immediately.
- **Freeze.** A freeze marks the grant `frozen` and `revoked_reason: hallinto_frozen`; a revoke uses `hallinto_revoked`. While a frozen grant exists, the legacy admin views refuse to grant a city to the account (409). A new appointment lifts the freeze, and that needs a new decision.
- **Idempotent.** The same key and body replays the receipt. A new key for a grant that is already ended is a confirmed no-op.
- **Receipt.** `status: confirmed` means the appointment's grant is no longer live. `effective` is everything the account still has: `cities`, `permissions`, `role`, `global_admin`, `governance_managed` and `other_scope_types` (live grants that are not city grants). Flask only reports it. Hallinto confirms its end when `status` is `confirmed` and `effective` shows nothing of the ended appointment's city, and should show anything else that remains to the verifier. If the grant is still live after the write, the operation is `reconciliation_required`.
- **Errors.** Unknown appointment, a different city or an unconfirmed appointment give 422. A missing or demoted actor, a bad proof or a bad signature give 403. The target is never revealed.

## Orders: board team, role removal and site lockdown (Hallinto H12, H17)

`POST /internal/hallinto/v1/orders` carries out an order Hallinto decided. One route serves all kinds. The `Idempotency-Key` header is the order id (`order_id` in the body must equal it). Trust model as before: mTLS workload identity plus proxy secret, an actor proof and Hallinto's attestation.

- **Attestation.** Ed25519 over `"hallinto-flask-order-v1-attestation\n" + order_digest`. The digest is built like the others: the body without `authority_attestation`, canonical JSON.
- **Proof.** Action `flask_order_v1`. Send the verifier to `/admin/hallinto/approve?r=<base64url JSON of the order terms>&state=...`. The fragment returns `state` and `proof` only. A roster list must fit the request line (about 200 accounts at most; the real board is far smaller).

### `kind: board_roster` (H12)

Body: `order_id`, `kind`, `decision_id`, `decision_revision`, `roster` (sorted, unique account ids, 1 to 200), `authority_attestation`. Flask sets the board team (the memberships of `BOARD_ORG_ID`) to exactly that roster: accounts missing from the team are added with `grant_source: hallinto_decision`, accounts not listed are set `governance_approved: false`. Safeguards that stay in Flask, each answered 422 without a write:

- every listed account must exist and be active and confirmed;
- the board organization must be configured and exist;
- Olivia's account (`OVERRIDE_OLIVIA_USER_ID`) is never removed by an order;
- one order removes at most half of the current team, and adds at most as many accounts as the team has (at least five, so a new team can be set up);
- the account that signs the order in Flask cannot be on the list as an addition.

Re-approving an old membership row resets its role to `member` and its permissions to none. The approval page lists who is added and who is removed.

Receipt: `status: confirmed` with `effective: {roster: [...]}`, the team Flask has now. Hallinto confirms only when it equals the sent roster. The receipt has no `account_id`.

### `kind: role_removal` (H17)

Body: `order_id`, `kind`, `decision_id`, `decision_revision`, `account_id`, `role`, `authority_attestation`. Flask removes the one named role and nothing else. Removable roles: `city_admin`, `admin`, `global_admin`, `superuser`, `god`. Refused: any other role, the account named by `OVERRIDE_OLIVIA_USER_ID` and the acting verifier themselves (422, 403), and a role ranked above the actor (403): `god` needs a god actor and `superuser` a superuser or god actor, others need a global admin.

- `admin`, `superuser`, `god`: the role becomes `user`.
- `global_admin`: the stored flag, the role `global_admin` and the national team membership all end. A `superuser` or `god` role the account also has stays.
- `city_admin`: the account's city grants end and the role returns to `user`.
- Tokens and sessions are revoked and `auth_epoch` rises. Board team membership is not touched; the roster order owns that.
- Idempotent: a new order for a role the account no longer has is a confirmed no-op. "Permanent" means Hallinto will not grant it again by itself; the legacy admin views can still give a role, until they are retired.

Receipt: `status: confirmed`, `account_id`, `effective: {role, global_admin, full_permissions, system_team_access}`, read from the loaded account so access that comes from a team shows too. Hallinto confirms when `effective.role` differs from the removed role (for `global_admin`, when `effective.global_admin` is false unless a super role remains). If the named role is still held after the write the operation is `reconciliation_required`.

### `kind: site_lockdown` (hätäsulku)

Body: `order_id`, `kind`, `decision_id`, `decision_revision`, `action` (`close` or `reopen`), `authority_attestation`. Off unless `HALLINTO_LOCKDOWN_ORDERS_ENABLED: true` in `config.yaml` (default `false`): with the flag off the route answers 404 for this kind and the approval page answers 400.

Flask writes what a decision on its own page `/admin/governance/security` writes: a security case of kind `lockdown` in status `locked` (events carry method `hallinto_order` and the decision id) and the shared lock `security-lockdown` that case owns. The Flask page can therefore always reopen the site, with Hallinto up or down, and Flask's page stays the fallback. `reopen` ends the case that holds the lock and clears every lock name. An order that would not change anything (closing a closed site, opening an open one) is refused as `stale_state` (409), because the decision was made for a state the site no longer has. The shared fence is `site-lockdown`.

Receipt: `status: confirmed` with `effective: {lockdown: "closed" | "open"}`, read after the write. Hallinto confirms only when it matches the order's action.

Both kinds return the usual `pending`, `reconciliation_required` and `rejected` receipts, replayed by key.


## Role bundles (`translator_v1`, `api_access_v1`, `admin_v1`)

An account gets one named bundle by an appointment decision: a role and an exact permission list that Flask owns (`admin/role_bundles.py`, `BUNDLES`). Hallinto confirms only that exact set. The contract text is Hallinto's `docs/role-bundles-contract.md`.

`POST /internal/hallinto/v1/role-bundles`, Idempotency-Key = appointment id. Same workload identity, proof and attestation as city appointments, with proof action `role_bundle_v1` and attestation context `hallinto-role-bundle-v1-attestation\n`. Body: `account_id`, `template`, `decision_id`, `decision_revision`, `valid_until`, `expected_state`, `authority_attestation`. The signed digest is the canonical JSON of the body without the attestation plus the bundle's sorted `permissions`, which Flask adds itself. The approval page takes the same terms without `expected_state` and returns it in the fragment.

- One bundle per account: a new bundle replaces the earlier one (sessions and tokens are revoked on replacement). Accounts with a global-admin flag, a super or city role, a system team, a live scoped grant, a protected account or an inactive one are refused (422). Rights from the old admin tools are taken over only when the bundle gives them anyway. Nobody can give themselves a bundle.
- Flask writes `users.role`, exactly `users.global_permissions`, `governance_managed` and `users.role_bundle` (template, operation id, `valid_until`) through the guarded profile write, so the change is in the account history.
- Expiry is on read: `User.from_db` ignores a bundle once `valid_until` has passed (`utils/role_bundle_expiry.py`). Code that reads `users` documents directly still sees the stored fields until the next write.
- Receipt `effective`: `{role, city: "", permissions (sorted), valid_until}`, plus `resulted_in_city_enablement: false`.
- The account and the national admin team's active members get an email after a confirmed write or end (best effort; a mail failure never changes the result).

`POST /internal/hallinto/v1/role-bundles/{appointment_id}/end` ends the bundle that appointment wrote (revoke or freeze). Body: `appointment_id` (must equal the path), `kind`, `account_id`, `template`, `decision_id`, `decision_revision`, `authority_attestation`. Proof action `role_bundle_end_v1`, attestation context `hallinto-role-bundle-end-v1-attestation\n`. It removes exactly the bundle's role and permissions at once, is idempotent, and leaves a newer bundle alone. Receipt `effective`: `{role, permissions, global_admin, bundle}`; a plain user has `bundle: null`.

## National moderator appointments (`national_moderator_v1`)

A national moderator appointment records who may book duty shifts and who approves them. It grants no rights; rights exist only during an approved shift (`utils/duty_shifts.py`, `admin/moderator_appointments.py`).

`POST /internal/hallinto/v1/moderator-appointments`, Idempotency-Key = appointment id. Same workload identity and proof rules as city appointments, with proof action `national_moderator_v1` and attestation context `hallinto-national-moderator-v1-attestation\n`. Body: `account_id`, `template`, `decision_id`, `decision_revision`, `valid_until`, `self_booking` (bool), `shift_approvers` (sorted, unique Flask account ids, at most 50, never the appointee), `expected_state`, `authority_attestation`. The signed digest is the canonical JSON of the body without the attestation plus the sorted `permissions` (the eight national moderator rights), which Flask adds itself. The approval page takes the same terms without `expected_state` and returns it in the fragment.

- Eligible: an active, confirmed personal account (not an organization account) without a live moderator appointment. Every approver must be an existing account. Otherwise `422 unsupported_scope`.
- Flask writes one `national_moderators` record (approvers, `self_booking`, the decision and operation ids). It does not touch `users.role` or `users.global_permissions`.
- Receipt `effective`: `{city: "", permissions (sorted), valid_until}`, plus `resulted_in_city_enablement: false`.

`POST /internal/hallinto/v1/moderator-appointments/{appointment_id}/end` revokes or freezes the appointment that operation wrote. Body: `appointment_id` (must equal the path and be the Idempotency-Key of the appointment), `kind` (`revoke` or `freeze`), `account_id`, `template`, `decision_id`, `decision_revision`, `authority_attestation`. Proof action `national_moderator_end_v1`, attestation context `hallinto-national-moderator-end-v1-attestation\n`. Shifts stop giving rights at once, including a running shift and its grace period. It is idempotent. A freeze is lifted only by a new appointment decision; a frozen appointment does not block one. Receipt `effective`: `{account_id, role: "", city: "", cities: [], permissions: [], global_admin}`: nothing remains that this appointment gave.

`GET /internal/hallinto/v1/duty-shifts?from=YYYY-MM-DD&days=7` (1 to 14 days) is a read-only feed for Hallinto's Vuorot page: `{"shifts": [{starts_at, ends_at, moderator, state}]}` with the moderator's display name, UTC times and the shift state (`requested`, `approved`, `running`, `grace`, `ended`, `inactive`). Cancelled shifts are left out. The mTLS workload identity is the only check; it takes no proof because it changes nothing and shows nothing beyond the calendar.

Rows of that feed also carry the shift `id` and, while a shift waits for approval, `approvers` (the Flask account ids that may approve it), so Hallinto can show the Hyväksy and Hylkää buttons to the right people.

`POST /internal/hallinto/v1/shift-approvals` approves or rejects one booked shift for the approver Hallinto names. Body: `shift_id`, `action` (`approve` or `reject`), `approver_account_id`, `issued_at` (UTC, `%Y-%m-%dT%H:%M:%SZ`, at most five minutes old), `reason` (required for `reject`, at most 200 characters) and `authority_attestation`, Hallinto's Ed25519 signature over `hallinto-shift-approval-v1-attestation\n` plus the canonical intent digest. It needs the mTLS workload identity and takes no step-up proof; Hallinto has already signed the approver in. Flask re-checks that the account is an approver of that moderator's appointment, is not the moderator, and that the appointment is not frozen or revoked. Replaying an approval changes nothing; rejecting an approved shift or approving a cancelled one answers 409. Flask's own Vuorot page no longer approves; it points to Hallinto.

## Outbound organization invitations (O6)

Off unless `ORG_OUTREACH_ENABLED: true` is in `config.yaml` (default `false`) AND the grant config exists. With the flag off the routes answer 404 and the approval page answers 404. The opt-out page is always on.

`POST /internal/hallinto/v1/org-invitations`, Idempotency-Key = `invitation_id`. Proof action `org_invitation_v1`, attestation context `hallinto-org-invitation-v1-attestation\n`. Body (exact set, `note` optional): `invitation_id`, `case_id`, `decision_id`, `decision_revision`, `organization_name` (2 to 120, one line), `address` (lowercase, one `@`), `source_url` (https, where the address is published), `note` (<= 300, one line), `authority_attestation`. Flask sends one Finnish mail to the address with a link to `https://mielenosoitukset.fi/organisaatiot/hae?kutsu=<token>` and a one-click opt-out (`List-Unsubscribe` and `List-Unsubscribe-Post`). Tokens are random, expire after 30 days and are stored hashed.

The receipt is always `confirmed` once Flask has decided; `effective.status` says what happened: `sent`, `suppressed` (opt-out), `not_sent` (an account uses the address, or the address or organization had an invitation recently: the two are deliberately indistinguishable, so a send never tells whether somebody has an account), `limit` (the verifier's or the site's daily quota), `invalid_address` (role or automatic mailbox), `paused` (panic switch). It carries `invitation_id`, `expires_at` and the address's SHA-256, never the address. The journal and logs hold only the hash. A retry with the same key replays the receipt and never resends; if queueing the mail fails the operation stays pending (503), then `reconciliation_required`.

Aliases (`name+tag@domain`) count as the same mailbox for limits, opt-outs and the account check. Every attempt that reaches the quota check counts against the verifier's quota first, then the site's. Visible text may not contain Unicode format characters. Limits: one invitation per address per 90 days, one per organization name per 30 days (case and spacing ignored), 20 a day for the whole site, 3 a day per verifier. Opt-outs are permanent (`outreach_optouts`, address hash only); Hallinto never deletes entries.

`POST /internal/hallinto/v1/org-invitations/check` with `{"address": ...}` and workload identity only (no proof) answers `{sendable, reason}` with `reason` one of `suppressed`, `invalid_address`, `limit`, `rate_limited` (60 a minute) or null. It never says whether an account uses the address, and neither does a send.

Public page: `GET /organisaatiot/ei-kutsuja?t=<token>` shows a confirm button and changes nothing; `POST` records the opt-out (the one-click request from mail programs too). The page is `no-store`, `no-referrer` and `noindex`. The token only identifies an invitation.

The Go request form must accept `kutsu` and store the invitation's `case_id` on the new request (Go side, not in this change). Until it does, the link opens the plain form.

## Organization access recovery (O5)

Off unless `ORG_RECOVERY_ENABLED: true` is in `config.yaml` (default `false`) AND the grant config exists. With the flag off the route and the approval page answer 404.

`POST /internal/hallinto/v1/org-recoveries`, Idempotency-Key = `request_id`. Proof action `org_recovery_v1`, attestation context `hallinto-org-recovery-v1-attestation\n`. Body (exact set): `request_id`, `case_id`, `decision_id`, `decision_revision`, `need` (`owner_change`, `add_member`, `lost_access`), `organization_id`, `person_email` (lowercase), `person_role` (`owner` for `owner_change` and `lost_access`; `admin` or `member` for `add_member`), `expected_state`, `authority_attestation`. Approval terms are the same without `expected_state` and the attestation. Action discriminator for the approval page: `request_id` together with `need`. The approval page returns `expected_state` in the fragment; it is a digest of the organization's members and roles, its protected flag and its open invitations (addresses hashed).

Flask never grants access directly. It adds one invitation to the organization (the existing invitation list, with the `role_granted_by` marker, so the role check holds) for the stored address of an existing account and mails it with the normal invitation template. The invitation lasts 14 days (`expires_at`). For `owner_change` and `lost_access` it records `replaces_owners` (the current owner ids); only when the person accepts do those owners become `admin` (nobody is removed). An already-existing lower member is raised to the invited role on accept. If nobody accepts, nothing changes.

Refused with one and the same `422 unsupported_scope` (the reason is only in the server log, without any address): the national or board team organization, a missing organization, no account with the address, an inactive, banned, suspended or unconfirmed account, an account with site-wide rights, city grants, team access or protected status, a person who already holds the role or higher, an open invitation for the address, and (except for `lost_access`, where the person asked) an address on the outreach opt-out list. A changed organization gives `409 stale_state`; same key with another intent gives `409 idempotency_conflict`.

The receipt `effective` is `{organization_id, invitation_id, role, expires_at, replaces_owners_count, person_email_sha256}`; the hash is SHA-256 of the lowercased, trimmed address. When the invitation is accepted, `accepted_at` is added and Hallinto can read it with `GET /operations/by-key`. The journal and logs hold only the hash; the address sits in the mail queue until sent.
