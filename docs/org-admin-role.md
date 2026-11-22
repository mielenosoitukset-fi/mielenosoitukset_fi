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
