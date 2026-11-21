# Organization account requests

Slice O2 of the organization onboarding plan in the Hallinto roadmap
(<https://github.com/mielenosoitukset-fi/hallinto/blob/main/docs/ROADMAP.md#organization-accounts-proposal-of-21-november-2026>),
approved by the maintainer on 21 November 2026. The maintainer reviews the form
before it is enabled in production.

## What it does

Anyone can ask for an organization account, suggest an organization they do not
represent, or ask for onboarding. Signing in is optional. Go stores the request in
`organization_requests` and mails an anonymous applicant a private status link.
Review, the channel proof (email, social media or a meeting; silence never counts)
and the OT decision happen in Hallinto (O3). Creating the organization and sending
the owner invitation stay with Flask (O4). Nothing in this slice grants access.

## Pages and routes

| Browser path | Method | Served by | Purpose |
| --- | --- | --- | --- |
| `/organisaatiot/hae` | GET | React host | Form; signed-in visitors also see their requests |
| `/organisaatiot/hae` | POST | Go `POST /api/organization-requests` | Submit (JSON) |
| `/organisaatiot/hakemukset` | GET | Go `GET /api/organization-requests/mine` | Signed-in account's requests |
| `/organisaatiot/hakemus` | GET | React host | Status page; token in the URL fragment |
| `/organisaatiot/hakemus` | POST | Go `POST /api/organization-requests/status` | Read one request by token (`{"token"}`) |

Go registers the routes only with `ORG_REQUESTS_GO_ENABLED=true`. Apache rules,
to add next to the city admin rules in `deploy/apache/react-ssr-routing.conf`
when the maintainer approves activation:

```apache
RewriteCond %{REQUEST_METHOD} "^POST$"
RewriteRule "^/organisaatiot/hae$" "http://127.0.0.1:8091/api/organization-requests" [P,L]
RewriteCond %{REQUEST_METHOD} "^GET$"
RewriteRule "^/organisaatiot/hakemukset$" "http://127.0.0.1:8091/api/organization-requests/mine" [P,L]
RewriteCond %{REQUEST_METHOD} "^POST$"
RewriteRule "^/organisaatiot/hakemus$" "http://127.0.0.1:8091/api/organization-requests/status" [P,L]
RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"
RewriteRule "^/organisaatiot/(hae|hakemus)$" "http://127.0.0.1:8090$0" [P,L]
```

## Request body

`represents`, `organization_name` (2–120), `organization_email` (required when
representing), `links` (1–5 http(s) links), `business_id` (optional, `1234567-8`),
`description` (≤1 500), `applicant_name`, `applicant_email` (anonymous only; a
signed-in account's own address is used), `applicant_role`, `access`
(`organization_account` | `own_account` (signed in only) | `invite`, only when
representing), `invite_email` (for `invite`), `onboarding`, `consent`, plus the
bot traps `homepage` (must be empty) and `started_at` (ms; under 2 s is a bot).
Unknown fields are refused.

## Stored document

`organization_requests`: `organization{name,email,links,business_id,description}`,
`applicant{name,email,role,user_id?}`, `represents`, `access`, `invite_email`,
`onboarding`, `status`, `status_message`, `status_token_hash` (SHA-256; the token
itself is only in the mail), `source`, `consent_at`, `created_at`, `updated_at`,
`email_confirmed_at`, `expires_at` (unconfirmed only), `case_pending` (set once the
address is confirmed; Hallinto's intake clears it).

Statuses: `unconfirmed`, `received`, `verifying`, `in_review`, `needs_info`,
`approved`, `rejected`, `withdrawn`. Go only writes the first two; Hallinto owns
the rest.

## Abuse limits

Per client: 3 submissions a minute and 10 an hour; 20 status reads a minute and
120 an hour. Per address: at most 3 unconfirmed requests. The confirmation mail
counts against the site-wide signup mail caps (`signup_mail_cap.go`, route
`org-request`). The panic switch closes the form.

## Open before activation

- A TTL index or job removing unconfirmed requests after `expires_at`, and rejected
  ones after the reconsideration period.
- A privacy statement entry for the new register.
- Hallinto intake (O3).
