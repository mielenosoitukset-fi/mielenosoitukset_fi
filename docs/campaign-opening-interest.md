# Campaign opening interest signup

Status: implemented behind switches that are all off, 16 November 2026. Not active in production.

Visitors can say they are interested in the campaign's opening event (6.3.2027, Tampere) from the form on
`/kampanja/`. It is a **third list on the fundraising reminder system** ([fundraising-reminder.md](fundraising-reminder.md)),
built like the Pride banner carrier list ([pride-banner-volunteers.md](pride-banner-volunteers.md)). Read those for the
guards, token rules and limits; this file lists only what differs.

## Parts

| Part | Where |
| --- | --- |
| Routes | Go, `go-api/opening_interest.go` on `reminderListHandler`: `POST /api/campaign-opening/interest`, `…/confirm`, `…/unsubscribe` |
| Form | React, `OpeningInterestForm.tsx`, shown only while `opening_interest_form` is true in `fundraising.json` |
| Link page | `OpeningLink.tsx`: `/kampanja/?avajaiset=vahvista#t=…` or `…=peru#t=…`. Works whatever the form switch says; a button press is needed, the token stays in the fragment |
| Tokens and mail | Flask, `utils/campaign_opening_interest.py` and job `deliver_opening_interest_confirmations` |
| Retention | job `purge_opening_interest` (`python -m mielenosoitukset_fi.scripts.purge_opening_interest --dry-run`) |
| Export | `python -m mielenosoitukset_fi.scripts.export_opening_interest` (confirmed only, formula-safe, read-only) |
| Index | `python -m mielenosoitukset_fi.scripts.ensure_opening_interest_indexes` (unique `email`) |
| Data | collection `campaign_opening_interest` |

## Data and retention

`email` (lower case), `city`, `willing` (`yes`, `maybe`, `no` or empty), `band` (`under10`, `10to20`, `over20` or empty, kept only
after `yes` or `maybe`), `status`, `locale`, `consent_at`, `consent_version` (`2026-11-17`), timestamps and token hashes.
No name, IP address, user agent or phone number; the mail repeats no form text. Unconfirmed rows are deleted after 14 days;
everything is deleted from **13.3.2027** unless a row carries a later `retain_until` (set only after a new explicit consent).
The date is `PURGE_AFTER` in `utils/campaign_opening_interest.py`; the consent text and the privacy notice name it.

## Switches (all off by default)

| Switch | Where | Effect |
| --- | --- | --- |
| `OPENING_INTEREST_SIGNUP: true` in `config.yaml` (needs `GO_SUBMIT_ENABLED`) | deploy writes `OPENING_INTEREST_SIGNUP_ENABLED=true` for the Go API | routes exist; without the unique index Go keeps them off |
| `OPENING_INTEREST_MAILS_ENABLED` | Flask scheduler environment | confirmation mails are queued |
| `opening_interest_form` in `fundraising.json` | shared configuration | form appears on the page |
| Apache rule in `deploy/apache/react-ssr-routing.conf` | host | sends the three routes to the Go API |

## Activation (needs the maintainer's go; backend first, UI last)

Follow the Pride banner runbook in [pride-banner-volunteers.md](pride-banner-volunteers.md) with these names: backup, create the index,
set the mail flag, set `OPENING_INTEREST_SIGNUP`, deploy the Go API, activate the Apache rule, then set `opening_interest_form`.
Revert in reverse order; `dropIndex("email_unique")` removes the index.
