# Hallinto city appointment contract

C01 defines the proposed v1 owner contract before adding writes. Flask owns the wire contract and fixtures. Hallinto pins a reviewed commit before using them. This PR adds no endpoint or privilege change. [OpenAPI v1](hallinto-city-grant-v1.yaml) · [Synthetic examples](hallinto-city-grant-fixtures.json)

## Current owner path

- `admin_user_bp.py:_sync_city_scope_grant` normalizes city keys, adds listing/view permissions, writes `admin_scope_grants`, can enable a city and update `users.role`, and records history. Its callers in create/edit/save and any additional operational writer must share the same conditional operation before the API is enabled.
- `_can_manage_scope_grants`, `_check_user_target` and role/target checks govern current admin authority. `security_cases.py` handles protected targets and emergency suspension separately. A Hallinto attestation must never replace technical actor/target checks.
- `User.admin_scope_grants` accepts BSON IDs and legacy strings, and city permissions can have additional effective behavior through wrapper/role logic. Confirm a fresh user and permission check rather than reporting the saved grant payload as the outcome.
- `admin_case_bp.py:set_application_status` controls city application status separately. The pilot links a case and decision. Changing application status or sending applicant mail requires a separately approved effect. Helsinki's recruitment exception remains.

## Pilot scope and security

The first write is one additive city-admin appointment for an eligible active account without an existing city grant. This limits replacement behavior and prevents a city-key union from broadening a permission across cities. A3 must approve the exact `city_admin_v1` preset, expected implicit permissions, role effects and eligible accounts. No arbitrary capability list, expiry, multi-city replacement, or protected-role changes through this contract.

Use authenticated internal ingress for the workload and a separate audience-bound owner-issued human proof. For writes require a fresh authorization for the exact account, city, template, city-enablement allowance, expected owner state, decision revision and operation key. Verify Hallinto's signed authority attestation for the same intent. Claim formats and factor issuance belong to I01/I02 and A2. Reject self-escalation, inactive accounts, unsupported targets/cities and unauthorized actors without exposing unrelated records.

`expected_state` is an opaque version of the owner inputs that affect the command. Cover target role/activity/security, active grant state and city enablement or recheck them under the same owner fence. Reject any unexpected prior grant or additional implication. Require explicit allowance if the operation would enable a city. Canonicalize validated stable fields before digesting. Proof tokens can be renewed without changing the intent. Log neither proof nor attestation bytes.

## Outcome and reconciliation

Scope the key by authenticated integration identity and acting subject. An exact replay after successful authorization returns the original committed receipt even if later revocation changes current access. Never reapply it. Same key and different intent returns conflict. Revalidate receipt visibility on lookup. A different operator needs an explicitly authorized owner reconciliation path, with separate audit.

First allocate a durable operation and serialize with the existing writer. Use a transaction where proven or a fenced, resumable journal where effects span writes. After mutation, check the exact effective city/permission result with newly loaded owner state. Return `confirmed` only when it matches the authorized plan. Report later drift separately. Unknown or partial operations stay pending/reconciliation-required. Locate them by the original key, preserve the receipt and never guess a successful outcome. Logs must correlate actor, case decision, account, operation ID and state transitions.

Until C02/C04 prove compatibility, keep API disabled. Required staging checks include native UI parity, no cross-city or protected effects, duplicate commands, competing saves, revocation racing with grants, interruptions between legacy effects, and authoritative positive and negative permission readback. Recovery disables new API operations and reconciles pending receipts. Reversal is a new authorized owner action.
