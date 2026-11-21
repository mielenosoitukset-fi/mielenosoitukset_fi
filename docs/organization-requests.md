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
| `/organisaatiot/hae` | POST | Go `POST /api/organization-requests` | Submit (JSON, or multipart with a logo) |
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
representing), `links` (1–5 http(s) links), `city` (optional, a city from the city catalogue, stored as its key),
`business_id` (optional, `1234567-8`),
`description` (≤1 500), `applicant_name`, `applicant_email` (anonymous only; a
signed-in account's own address is used), `applicant_role`, `access`
(`organization_account` | `own_account` (signed in only) | `invite`, only when
representing), `invite_email` (for `invite`), `onboarding`, `consent`, plus the
bot traps `homepage` (must be empty) and `started_at` (ms; under 2 s is a bot).
Unknown fields are refused.

With a logo the body is `multipart/form-data`: the JSON above in the `request`
part and the image in `logo` (PNG or JPEG, at most 1 MB and 2000×2000 px). Go
decodes and re-encodes it as PNG, so only pixel data is stored.

## Stored document

`organization_requests`: `organization{name,email,links,city?,business_id,description}`, `logo?{content_type,data,size}`
(copied to the organization when it is created in O4),
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

## Carrying out an approval (O4)

Hallinto calls `/hallinto-oidc/organization-requests/create-organization` once a case's
verified decision approves an organization's own request (see
`docs/hallinto-isolated-issuer.md`). The code is `utils/org_request_owner.py`. The
organization document only gets fields `Organization.from_dict` accepts, because any other
key breaks every loader: the organization has no city field, so the city stays on the request.

## Activation (21 November 2026)

The maintainer asked to turn every ready feature on. The Apache rules shipped in
d5858c2; the deploy now writes `ORG_REQUESTS_GO_ENABLED=true` whenever public
submissions are on (`ORG_REQUESTS_GO: false` in `config.yaml` opts out), and
`release_routing.sh` keeps the old routing unless Go answers 405 on
`GET /api/organization-requests`.

- Unconfirmed requests: the `organization_requests_unconfirmed_ttl` TTL index on
  `expires_at` (`scripts/provision_public_indexes.py`, every deploy) removes them
  once the link expires. Confirming unsets `expires_at`.
- The privacy notice (`frontend/src/app/information/PrivacyContent.tsx`) lists
  the register.
- Still open: deleting rejected requests after the reconsideration period
  (Hallinto owns rejections), and the full Hallinto intake (O3).

## Status updates from Hallinto

`organization_requests_update` is idempotent: repeating the same status, message,
case and access changes nothing, adds no `history` entry and sends no mail. A real
move to `approved`, `rejected` or `needs_info` queues one email to the applicant
(`applicant.email`, or the account email for a signed-in applicant) from
`templates/emails/org_request_status.html`; a rejection carries the reason. The
mail is best effort: a failure is logged and the saved status stands.

## Access recovery form (O5)

`/organisaatiot/palautus` asks for access to an organization's account: `lost_access`,
`owner_change` or `add_member`. Go stores it in `organization_requests` with
`kind: "access_recovery"`, the same status machine and status link as O2 (the status
page and `POST /organisaatiot/hakemus` are shared). Hallinto decides; Flask only
invites a person afterwards (`docs/hallinto-city-grant.md`, `org-recoveries`).

| Path | Method | Served by |
| --- | --- | --- |
| `/organisaatiot/palautus` | GET | React host, `noindex` |
| `/organisaatiot/palautus` | POST | Go `POST /api/organization-requests/recovery` |

Live since 21 November 2026 (maintainer: "approve everything"): the deploy writes
`ORG_RECOVERY_GO_ENABLED=true` with the requests above (`ORG_RECOVERY_GO: false` in
`config.yaml` opts out), the Apache POST and GET rules are on, and `release_routing.sh`
keeps the old routing unless Go answers 405 on `GET /api/organization-requests/recovery`.
Opting out also needs the Apache rules removed, or the release check refuses the routing.

The answer never says whether the organization exists, is protected or already has a
request. Quietly dropped, and answered like success: a bot, an address with two unconfirmed
requests, an organization name with three confirmed requests in 30 days, and a full site mail cap. A
request for an organization with a confirmed open one is stored with `duplicate_of` and
nothing shown to the sender, and is not reviewed twice. Only confirmed requests count
toward the open and monthly limits, so unconfirmed requests cannot lock an organization
out. The per-address count ignores `+tag`, and a form sent without `started_at` is treated
as a bot. Only the client's own limit (2 a minute, 5 an
hour) answers 429. An owner change and an added member name `person_email`; a lost-access
request uses the applicant's confirmed address. Hallinto reads them with
`POST /organization-requests/list {"kind": "access_recovery"}`; the ordinary list leaves
them out and `create-organization` refuses them, so no recovery request can create an
organization.

## Invitation link (O6)

An outbound invitation mail links to `/organisaatiot/hae?kutsu=<token>`. The form reads
the token from the address and sends it as `invitation`. Go claims it once: it finds the
invitation by the token's hash (`org_invitations`, status `sent`, not expired, never used),
marks it used and stores `invitation_id` and `invitation_case` on the request. Hallinto
reads `invitation_case` from the request list and links the two cases. A wrong, expired or
spent token changes nothing and says nothing, and the token proves nothing about the
organization: the channel proof still happens in Hallinto.
