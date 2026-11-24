# Account deletions by admins are reviewed in Hallinto

Maintainer decision, 24 November 2026 (Olivia): deleting someone's account is the heaviest thing an admin can do to a user, so it leaves a case, a decision and a trace in Hallinto, like a role removal.

## Flow

1. An admin deletes a user in the admin (fresh step-up as before). With `ACCOUNT_DELETION_IN_HALLINTO=true` Flask **soft deletes**: `active=false`, `soft_deleted` marks (who, when, the previous `active`), `auth_epoch` raised, API tokens, magic links and MFA challenges ended. Data stays. A row is stored in `account_deletion_reviews` (names and role only, never an email address). One open review per account (partial unique index).
2. Hallinto reads `/hallinto-oidc/account-deletions/list`, opens a case, and links it with `/account-deletions/update` (`id`, `case`). That update never decides anything.
3. OT decides with its own form. A verified decision becomes an order `account_deletion` with `action` `delete` (erase the closed account the way the instant delete did) or `restore` (reopen it as it was). Flask carries it out only for a closed account with an open review; anything else is `stale_state`. The protected owner account, the acting admin and higher roles than the actor's are refused.
4. A separate decision in the same Hallinto case reviews whether it was necessary for that admin to delete. It records only; it changes no account.

## Stays instant

A user deleting their own account and GDPR erasure requests do not use this path.

## Rollout

Flag off (default): the old instant delete. Turn it on only after Hallinto's account-deletion forms are deployed, or closed accounts wait for a decision nobody can make. Rollback: unset the flag. Closed accounts stay closed until an order restores them; restore one by hand with the same fields the order resets (`active`, `auth_epoch`, `$unset soft_deleted`).
