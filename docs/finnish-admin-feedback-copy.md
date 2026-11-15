# Finnish admin feedback and shared labels — batch 6

Source review: `gpt-6.1-sol`, 15 November 2026. Full audit PR #1165; editor #1166,
operations #1167, manual #1168 and permission display #1169 are separate batches.
This final batch corrects 26 distinct backend copy pairs at 38 literal sites,
shared close/theme names and the admin-only MCP consent document title.

Only declared source literal locations were changed, preserving all JSON keys,
HTTP status codes, action/role identifiers, placeholders, routes, permission
checks, writes and email content. Previously raw human messages now use existing
gettext plumbing; all new messages remain extractable. No human fi/en/sv catalogs
are edited and none of the old msgids had translations to preserve. No frontend
module/generated bundle changes, so an asset rebuild is unnecessary.

AST comparison of all six backend files after normalizing only the declared
message constants/gettext markers is identical to HEAD: no other behavior changed.
The shared admin shell changes only two close-button accessible names and the
theme button title. MCP consent changes only `<title>`; existing consent form,
protocol/scopes and fixed light design remain unchanged.

## Exact backend copy

| Source | Original | Replacement | Reason / consumer |
| --- | --- | --- | --- |
| `mielenosoitukset_fi/admin/admin_case_bp.py:197,213,230` | Ei lisatietoja | Ei lisätietoja | Missing Finnish diacritics in runtime case summary. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:201` | Liittyvaa mielenosoitusta ei loytynyt | Liittyvää mielenosoitusta ei löytynyt | Missing Finnish diacritics in runtime case summary. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:214` | Demoa ei loytynyt | Demoa ei löytynyt | Missing Finnish diacritics in runtime case summary. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:216` | Organisaation muutospyynto | Organisaation muutospyyntö | Missing Finnish diacritics in runtime case summary. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:938` | Invalid request | Virheellinen pyyntö. | Untranslated JSON error reaches admin feedback. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:942,1055` | Demo not found | Mielenosoitusta ei löytynyt. | Untranslated JSON error reaches admin feedback. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:953` | Rejection reason required | Anna hylkäyksen perustelu. | Untranslated JSON error reaches admin feedback. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:974` | Unknown action | Tuntematon toiminto. | Untranslated JSON error reaches admin feedback. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:1098` | Case not found. | Tapausta ei löytynyt. | Untranslated JSON error reaches admin feedback. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:1133` | Internal server error. | Palvelimella tapahtui virhe. | Untranslated JSON error reaches admin feedback. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:975` | Mielenosoituksen tila paivitettiin. | Mielenosoituksen tila päivitettiin. | Missing ä in success flash. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:1086` | Demo status reset – requires superior review. | Mielenosoituksen tila palautettiin. Ylemmän tason käsittelijän on tarkistettava mielenosoitus. | Untranslated warning flash; keeps unspecified superior permission level. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:770` | Organisatiota ei löytynyt. | Organisaatiota ei löytynyt. | Misspelling in missing-organization flash. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:1328,1446,1546` | Organization not found. | Organisaatiota ei löytynyt. | Untranslated current admin error. Role literal values remain unchanged. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:1454,1549,1671` | Forbidden. | Sinulla ei ole oikeutta tähän toimintoon. | Untranslated current admin error. Role literal values remain unchanged. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:1471` | Invitation not found. | Kutsua ei löytynyt. | Untranslated current admin error. Role literal values remain unchanged. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:1540` | Invalid role. | Virheellinen rooli. | Untranslated current admin error. Role literal values remain unchanged. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:1647` | Invalid role. Role must be one of these: 'member', 'admin', 'owner' | Virheellinen rooli. Roolin on oltava 'member', 'admin' tai 'owner'. | Untranslated current admin error. Role literal values remain unchanged. |
| `mielenosoitukset_fi/admin/admin_org_bp.py:1536` | Missing required fields. | Pakollisia tietoja puuttuu. | Untranslated current admin error. Role literal values remain unchanged. |
| `mielenosoitukset_fi/admin/admin_dev_bp.py:204,220,232,265` | Request not found | Hakemusta ei löytynyt. | Untranslated missing-application JSON message. |
| `mielenosoitukset_fi/admin/admin_user_bp.py:1205` | Käyttäjä ID puuttuu. | Käyttäjän tunniste puuttuu. | Broken compound/case; reuses existing wording at :977. |
| `mielenosoitukset_fi/admin/admin_demo_bp.py:5568,5591,5669` | Mielenosoitus ei löytynyt | Mielenosoitusta ei löytynyt | Negative existential requires partitive. Third occurrence belongs to cancel, not delete. |
| `mielenosoitukset_fi/admin/admin_demo_bp.py:3630` | Internal server error. | Palvelimella tapahtui virhe. | Untranslated email-send failure feedback in admin modal; email content unchanged. |
| `mielenosoitukset_fi/admin/admin_demo_bp.py:3672` | An internal error has occurred. | Palvelimella tapahtui virhe. | Untranslated generate-link failure feedback. |
| `mielenosoitukset_fi/admin/admin_bp.py:91` | Background jobs are not available in this deployment. | Taustatehtävät eivät ole käytettävissä tässä ympäristössä. | Untranslated description on default 503 error page. |
| `mielenosoitukset_fi/admin/admin_case_bp.py:200` | Peruutuspyynto | Peruutuspyyntö | Missing diacritic in runtime case-list title. |

Shared labels: `admin_base.html` raw `Close` → gettext `Sulje` (two buttons),
`Vaihtele teema` → `Vaihda teemaa`; `oauth/mcp_consent.html` document title
`MCP OAuth Consent` → `MCP OAuth -valtuutus`. Existing human translations of
`Sulje` remain available; no old theme-label translations existed.

## Rejected/deferred source-review proposals

The initial broad audit proposals are historical findings, not automatic edits.
Geocode JSON errors are console-only: getCoordinates logs/catches and the UI
already shows a generic Finnish alert. These stay unchanged. Dead organization
missing-field branches behind input validation also stay unchanged. API error
constant edits are restricted to verified rendered consumers; duplicate legacy
console-only errors and persisted audit/history labels remain unchanged.
Unused tag form, unreachable recurring token notice and `(ei viestia)` fallback
are not active UI corrections. Guest edit-link explanation and public recipient
confirmation copy are outside this admin-only patch. Native campaign alert/prompt
copy and native case action-type option `Oltu yhteydessa` remain deferred without
required native-menu/dialog evidence; no visual pass is claimed for them. The
recurrence nth label was separately accepted selected in its closed native
control in #1166. Optional terminology/grammar proposals are adjudicated in each
batch (manual total-object/`aina kun` proposals rejected as optional style).
Shared account validator wording affects public flows too and is not changed.
Existing framework-generated English 503 heading remains; this batch localizes
only the project-owned admin description. GodScreen security terms remain exact.

## Verification and capture boundaries

**235 focused existing tests passed**: case behavior/components, organization
action validation/orphan members, developer collections, user management, admin
UI/i18n, MCP OAuth/contracts, edit-link lifecycle and background jobs/detail.
Existing deprecation/fixture JWT warnings only. New message extraction and
backend AST-preservation checks passed; changelog/diff checks recorded below.

68 before + 68 after actual fixture-backed viewport images at 390/1440px,
both theme preferences: four case-list fallbacks, two SweetAlert errors,
case/organization flashes, long invalid-role organization toast, developer
request toast, missing-user-ID modal, command-center missing-demo toast,
generate/send-link errors, theme focus, real 503 and admin-only consent GET.
Fixtures omit cover/preview images rather than referencing non-existent example
CDN objects. Required real CSS/fonts/widgets loaded; no missing requests or JS
errors. Existing 503/consent light-only surfaces retain their original rendering.

Browser POSTs are blocked by default. Expected POSTs are intercepted with error
JSON whose copy is read from the exact current backend literal at the reviewed
source location. Existing real UI handlers display those values. This proves
message rendering, not every production error cause. Flashes are seeded in the
fixture session; no escalation/role/password/invitation/email mutation is invoked.
The no-manager extension produces the actual Flask 503 response. MCP uses a
registered disposable public client and authorization GET only; no grant POST,
code or token is issued. All data is in this session's disposable DB, and cleanup
is restricted to its own DB. No production access or real messages.

ARIA/title assertions verify flash/global-toast close names, focused theme-button
title and actual document.title. Screenshots alone do not prove accessible names
or a headless browser tab title. Evidence `/tmp/moso-admin-visual/feedback-before/`
and `feedback-after/`; external fixture harness `/tmp/moso_admin_capture.py`.
Initial developer/command-center toast captures caught Bootstrap's `showing`
state at opacity 0. Replacements wait for the real settled state (`!showing`,
opacity 1); no application code, styles or toast timers are modified. Before
replacement captures use exact detached `b458355a` baseline; all source hashes
match original evidence. Final packets replace those two states at each width/
theme and exclude the superseded images. No missing resources or JavaScript
errors in replacements. CodeRabbit completed on all eight changed production
files: zero findings. Changelog rendered and diff whitespace checks passed.
Independent final visual acceptance: **SHIP** for light and dark, two separate
reviewers opened all 136 final images at readable scale, including the settled
toast replacements at both widths. No introduced visual defects. Packet lookup
uses `(state, viewport width)` so each image is unique and correctly identified;
all final paths, source and image digests verified after review.

Implementation snapshot: `bae268447d5cf88b355ba0884d6cf9b47e571415`.
Packets `/tmp/moso-admin-visual/review-feedback-light.json` and
`review-feedback-dark.json`; detailed verdicts `verdict-feedback-light.md` and
`verdict-feedback-dark.md`. Each verdict is limited to its theme and captured
feedback states; POST error data is intercepted, ARIA/document titles have
separate DOM assertions, existing fixed-light framework/consent pages remain.
This is not authorization, production error-cause or whole-admin certification.

PR: [#1170](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1170).
Review source hashes:

- `mielenosoitukset_fi/admin/admin_bp.py`: `0953adc78f1c5db48a56831acce52b2793f5520a5945f383eb39056b44a797f2`

- `mielenosoitukset_fi/admin/admin_case_bp.py`: `7eb96a9944aa0afd27a0f9b66db12f309e424dc027bc6fd7ac44919965a2340a`

- `mielenosoitukset_fi/admin/admin_demo_bp.py`: `4fadbb294b00e6b3ec9298aa2b55e6fa56a2d4072df2b7da9daae557026fae7f`

- `mielenosoitukset_fi/admin/admin_dev_bp.py`: `9f06d12e40d104f4eff6e568d689711b2219816fff998b4a894b68ff8a69fee9`

- `mielenosoitukset_fi/admin/admin_org_bp.py`: `6b45a1495da07d0cd7c8469ddb4f913e049c0605e3a5faf682568b15eeb601fb`

- `mielenosoitukset_fi/admin/admin_user_bp.py`: `f1248c4f975da51124b1a8614c2fe356a340e6b63299008dcd6f6322c40d5c5d`

- `mielenosoitukset_fi/templates/admin_base.html`: `b2e9e1432ee5a3938ba7d1c8c3db43007cd1e668d3266055f8581981ec973a82`

- `mielenosoitukset_fi/templates/oauth/mcp_consent.html`: `fb1e3a5a82bbf7ccea18eb901d03f8e9a07219de962c2267e92eb84434cf6a66`
