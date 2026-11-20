# Deleting an account: what is deleted, kept and sealed

Decision: [decisions.md, "Deleting an account"](decisions.md#deleting-an-account) (20 November 2026, issue #596).
Code: `mielenosoitukset_fi/admin/user_erasure.py`, called by `delete_user` in
`admin/admin_user_bp.py` after the fresh step-up, the history flush and the guarded `users`
delete succeed. A refused or conflicting delete changes nothing.

## What happens

| Group | Collections | Ordinary account | Privileged account |
| --- | --- | --- | --- |
| Sign-in and settings | `user_settings`, `mfas`, `mfa_login_challenges`, `magic_links`, `api_tokens` | deleted | deleted |
| Personal records | `password_changes`, `demo_reminders`, `demo_attending`, `notifications`, `memberships`, `api_token_requests`, `developer_apps`, `developer_scope_requests`, `admin_scope_grants`, `board_clearances` | deleted | kept, pseudonymised |
| Abuse evidence and audit | `login_logs`, `messages`, `reports`, `malicious_reports`, `demo_submission_errors`, `submitters`, `demo_suggestions`, `cases`, `city_admin_applications`, `security_cases`, `admin_logs`, `board_audit_logs`, `super_audit_logs`, `demo_audit_logs` | kept, pseudonymised | kept, pseudonymised |

Privileged means `role` other than `user`, `global_admin`, or any `global_permissions`.
The account is also removed from other users' friend, request and follower lists and
from demonstration editor lists. Demonstrations it submitted stay as they are.

Pseudonymised: in every matching record, the account id becomes a stable fake id of
the same type (HMAC of the id under `SECRET_KEY`), and the email, username and display
name become the alias `poistettu-<12 hex>` wherever they appear as a value, and the
email also inside longer text. IP addresses and user agents stay, because abuse
investigation needs them. Each record gets `erased_subject` (the alias) and
`erased_at`. The same account always gets the same alias, so its records still
belong together.

## The identity seal

If `ERASURE_SEAL_PUBLIC_KEY` (PEM, in `config.yaml`) is set, the real id, username,
email, display name and role are encrypted to that public key and stored in
`erasure_identity_seals` under the alias (RSA-OAEP-SHA256 wraps a fresh Fernet key).
The server cannot decrypt it. If the key is not set, no seal is stored, a warning is
logged, and the records can never be re-linked.

Create the key pair offline, never on the server:

```sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out erasure-seal-private.pem
openssl pkey -in erasure-seal-private.pem -pubout -out erasure-seal-public.pem
```

Put the public key in `config.yaml` as `ERASURE_SEAL_PUBLIC_KEY: |` (block scalar) and
restart Flask. Keep the private key offline (password manager or hardware), with a
backup; losing it makes every seal unreadable, which is safe but final.

## Re-linking (unsealing)

Not possible in this app, by decision. It will be built in the separate
administration system. Requirements for that system:

- Allowed only for a concrete abuse investigation or a legal obligation, with a written reason and a case number.
- Done offline with the private key; `open_identity_seal()` in `user_erasure.py` is the reference for the format.
- Every unseal is logged (who, when, alias, reason, case number) somewhere the person doing it cannot edit.
- The result is used only for that case and not written back to this database.

## Retention

The daily job `purge_erased_accounts` deletes, after `ERASURE_RETENTION_DAYS`
(environment, default 730, minimum 30): the seal, and the kept non-audit records
(`erased_subject` set and `erased_at` older than the limit). `cases`,
`city_admin_applications`, `security_cases` and the audit logs stay, pseudonymised;
without the seal they cannot be linked to anyone. Dry run:
`python -m mielenosoitukset_fi.scripts.purge_erased_accounts --dry-run`.

## Limits

- Database backups still hold the old records until they rotate out (see `backups.md`).
- Application logs keep the real id of the deleted account in the deletion log line.
- Free text that names the person in another way (for example a nickname in a message) is not found.
