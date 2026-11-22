# org_admin role

Maintainer decision (Olivia, 22 Nov 2026): organisation admins are a real account role, `org_admin`, like `city_admin`, and it follows organisation membership.

## How it works

- Owner or admin of an organisation → `users.role = "org_admin"` (only when the account has no other site role; translators, admins and so on keep theirs) and one `admin_scope_grants` row per organisation: `scope_type: "organization"`, `template: "org_admin_v1"`, `scope_keys: [<organization id>]`, permissions exactly `utils/org_admin_role.PERMISSIONS`, no expiry.
- Stop being owner or admin → the grant ends (`revoked_reason: membership_ended`). The role goes back to `user` when no grant is left. The national and board system team organisations never get grants.
- `admin/org_admin_grants.py:sync_account` derives everything from `memberships`. It runs after every Flask membership write (`MemberShip.save`, member removal) and in the `sync_org_admins` background job (every 5 minutes), which also covers Go API invite accepts, recovery orders and writes outside the app. Idempotent. The Go API writes no grants, so there is no Go/Flask parity to keep.
- `User.memberships` raises a membership that has a live grant to at least `admin` with the grant's permissions (in memory, never stored), so every organisation check reads the grant.
- Switch off: `ORG_ADMIN_ROLE_ENABLED: false` in `config.yaml` (on by default), or `ORG_ADMIN_ROLE_ENABLED=0` for the job.

## Hallinto

Hallinto does not appoint. It may end a grant (`revoke` or `freeze`) and read state. Routes and shapes: the contract file given to Hallinto (`hallinto-org-approval-right-contract.md` in the project files), behind `ORG_ADMIN_HALLINTO_ENABLED` (off). An ended grant is marked `blocked` and is not recreated while the account stays owner or admin of that organisation; the block lifts when the membership role goes.

Until the membership-role rights are removed, an end removes only what the grant gives: the membership role still carries its default permissions.

## Cutover

Rights that come from the membership role itself are phased out by 1.1.2027, 1.3.2027 at the latest (Olivia). That PR must: stop `MemberShip.DEFAULT_PERMISSIONS` giving `admin`/`owner` the organisation, demo and case rights; make `case_access.managed_organization_ids` and the org admin checks read the grant; keep `member` as a plain team list; and run `sync_org_admins` once before switching so every owner and admin has a grant.

## Rights

Edit the organisation profile, invite and manage members (owners stay with owners, see `_can_manage_member`), create and edit its demos and recurring demos, read and add notes to its cases, and decide `new_demo` cases when the organisation holds the approval right. Never: verified flag, other organisations, site-wide pages, account roles, deleting the organisation (owners only, by membership).

## Organisation approval right (`ORG_APPROVAL_RIGHT_ENABLED`, off)

An organisation right, not a person's: "saa hyväksyä omat mielenosoituksensa". Hallinto grants and ends it; Flask adds no permission to any account.

- Stored at `organizations.rights.approve_own_demos` (`grant_id`, `case_id`, `decision_id`, `granted_by`, `valid_until`, `status`, `ended_at`, `ended_reason`); a replaced record moves to `organizations.rights_history.approve_own_demos`. Effective status (`utils/org_approval_right.py:status_of`): `live`, `expired` (on read, no job), `revoked`, `frozen`, `given_up`, `unverified`, `none`.
- Request: an owner or admin posts `/admin/organization/api/request_approval_right/`; Flask opens one `organization_right_request` case per organisation. Frozen organisations cannot ask; the right is for verified organisations only.
- Hallinto: `GET /org-right-requests`, `POST /org-rights` (grant), `POST /org-right-requests/{case}/refusal`, `POST /org-rights/{grant}/end`, `GET /org-rights/{org}`. Own proof actions (`org_right_v1`, `org_right_refusal_v1`, `org_right_end_v1`) and attestation contexts; the approval page shows the terms. Shapes: contract v3 in the project files. Journal key `org-right:<organization id>`.
- Effect: `has_demo_approval_permission` and the demo `ACCEPT_DEMO` check also accept an owner or admin (or org_admin grant holder) of an organisation with a live right, when **every** organiser of the demonstration is an organisation with a live right. Anything else goes to moderators. Escalated cases, the undo window and "Poista julkaisusta" are unchanged. A decision made only because of the right is marked in the case history ("Päätös tehty organisaation X hyväksymisoikeudella").
- Ends: Hallinto revoke or freeze; the owner gives it up (`/api/give_up_approval_right/`); the organisation loses `verified` (the check fails at once, the record is closed with `unverified`).
