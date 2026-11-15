# Finnish admin copy audit

Base: `576709a11ee18c2b67538285e89a627ccc1f5bee` (public PR #1164 merged).
Model: `gpt-6.1-sol`. Source review: 15 November 2026.
Branch: `codex/finnish-admin-copy-audit`; isolated worktree `/tmp/moso-admin-finnish`.

## Delivery status (15 November 2026)

The source audit is complete for the inventoried admin sources, with the
explicit exclusions/context limits in the reports below. Five bounded
correction batches have merged through the required-check merge queue:

| Batch | PR / durable notes | Delivered copy | Local proof / independent acceptance |
| --- | --- | --- | --- |
| Case detail | [#1165](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1165) | 38 exact proposals | 91 tests; 136 before/after images; SHIP both themes |
| Editors/dashboard | [#1166](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1166), `docs/finnish-admin-editor-copy.md` | 8 exact proposals | 121 tests; 68 images; SHIP |
| Operations/analytics | [#1167](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1167), `docs/finnish-admin-operations-copy.md` | 11 exact proposals | 105 tests; 56 images; SHIP |
| Manuals | [#1168](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1168), `docs/finnish-admin-manual-copy.md` | 14 exact proposals; two optional rewrites rejected | 18 tests; 100 images including mobile pans; SHIP |
| Permission display | [#1169](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1169), `docs/finnish-admin-permission-copy.md` | 8 headings +45 descriptions, Finnish editor only | 47 tests; 72 images; SHIP; catalog/checkbox/DB invariants preserved |
| Feedback/shared names | [#1170](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1170), `docs/finnish-admin-feedback-copy.md` | 26 backend pairs plus close/theme/title copy prepared | 235 tests; 136 final images; SHIP both themes; CodeRabbit zero findings |

These are source/capture-specific acceptances, not certification of the whole
admin. Captures and review digests remain outside the repository under
`/tmp/moso-admin-visual/`. Each batch has its own changelog note and exact
original/replacement/reason records. Human en/sv catalogs, auth/permission logic,
production data and email content were not changed. No real messages were sent.
No frontend module changed, so no hashed-asset rebuild was needed.

### Remaining / review adjudication

- Final feedback #1170: independent light/dark SHIP completed after replacing
  two toast states captured during Bootstrap's opacity0 animation. Final images
  wait for actual opacity1 without changing app code/styles/timers. All hashes
  verified; source is implementation `bae26844`. Required GitHub checks and
  merge queue still control merging this final batch.
- Native campaign alert/prompt strings and the native case action-type option
  `Oltu yhteydessa`: deferred without required menu/dialog evidence. No pass implied.
- Guest edit-link explanations and token confirmation prose: public recipient
  surfaces excluded from this admin-only correction scope.
- `(ei viestia)`, recurring token notice, unused tag form and dead validation
  branches: inactive/unreachable findings recorded, not falsely called delivered.
- Geocode/legacy console-only errors, email-only organization fallback, shared
  public account validator and persisted history/audit labels: excluded after
  consumer review. Never apply their initial proposals blindly.
- GodScreen security terms, Git/OAuth identifiers, optional wording and uncertain
  campaign queue semantics stay unchanged. Manual total-object and `aina kun`
  proposals were rejected as optional style; see manual notes.
- `admin/permission_copy.py` is a new reviewed admin display-copy owner. The
  original catalog remains byte-identical because it also feeds email summaries.
- User emails remain a later phase; no email proofreading was started.

The historical public audit stays unchanged. The broad reports below describe
the original source snapshot and proposals; the batch table and individual
notes above supersede their initial pending/apply/consumer assumptions.

### Batch 1 decisions and capture scope

Applied the case template proposals below, except `Oltu yhteydessa` (native
select popup lacks capture evidence) and `(ei viestia)` (current backend
always supplies a nonempty fallback). Both stay explicitly deferred.
Existing Finnish/en/sv compiled catalogs have no translations for the changed
case msgids; no human translations are removed or edited.

Capture the actual fixture-backed Flask app at 390px/1440px in light and dark:
new demonstration fields/organizers/actions, internal notes, status/log/history,
cancellation and confirmation/error dialog, error report, generic fallback,
urgent support from the contact form, search error, rejection dialog and
empty-response errors for demonstration/case updates. Synthetic intercepted
error responses exercise existing UI fallback text without performing writes.
The fixture DB is disposable and the cleanup plugin restricts cleanup to its
own DB. Capture harness and evidence stay outside the repository under
`/tmp/moso_admin_capture.py` and `/tmp/moso-admin-visual/`.

Focused checks: `test_admin_case_bp.py`, `test_admin_case_component_contract.py`,
`test_admin_ui_contract.py`, `test_admin_flash_i18n_contract.py`,
`test_city_scoped_admin.py`: 91 passed. Existing fixture JWT key warnings only.
The existing render assertion now expects the corrected Finnish heading.
Changelog rendered successfully. No frontend source/bundle change.
Independent visual verdict: SHIP, separate fresh-context light/dark reviewers
opened all 136 images at readable scale. Template SHA-256:
`9a1fe6e0ba596a8a8be2a912422915eda013bdeda879c4251d0779e5ced6f7f7`. Captured before base `576709a1`; reviewed uncommitted
source is content-identical to implementation commit `23569ab3`. Source and
image hashes rechecked after verdict. Evidence: `/tmp/moso-admin-visual/`,
`review-light.json`, `review-dark.json`, `verdict-light.md`, `verdict-dark.md`.
Scope is only the changed case-detail text and listed fixture states; no claim
about the whole admin. Existing dark-theme white dialogs/placeholder contrast
and sidebar truncation are unchanged. Intentional demo-search abort exercises
the actual error message; no resources missing or JavaScript errors observed.
CodeRabbit CLI reviewed the two changed tracked source/test files: completed,
zero findings. Changelog and audit documentation were inspected locally.
Batch 1 PR: [#1165](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1165).
No production access is needed.

# Admin Finnish template audit (read only)

Scope: all 67 admin_V2 templates, admin_base, four admin/ templates, admin-only oauth/mcp_consent. Public/email/manual source not changed. Exact lines refer to pre-edit worktree /tmp/moso-admin-finnish. Source files were not edited.

Method: read all extracted gettext, visible text, aria-label/title/placeholder and inline browser-message copy; read contextual source around findings. Dynamic backend copy is delegated to the backend audit. This is language audit, not visual acceptance.

## Exact proposals — actual errors

- `admin_V2/cases/case.html:56` — `URGENT` → `KIIREELLINEN`. Untranslated English urgency badge.
- `admin_V2/cases/case.html:59` — `Alkupera` → `Alkuperä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:71` — `(ei viestia)` → `(ei viestiä)`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:171,254` — `Paivamaara` → `Päivämäärä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:173` — `Paattymisaika` → `Päättymisaika`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:209` — `Jarjestajat` → `Järjestäjät`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:213` — `Nimeton jarjestaja` → `Nimetön järjestäjä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:228` — `Lahetetty` → `Lähetetty`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:235` — `Hyvaksy mielenosoitus` → `Hyväksy mielenosoitus`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:240,558` — `Hylkaa mielenosoitus` → `Hylkää mielenosoitus`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:246` — `Eskaloi esihenkilolle` → `Eskaloi esihenkilölle`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:256` — `Lahde` → `Lähde`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:257` — `Pyytaja` → `Pyytäjä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:258` — `Pyynto vastaanotettu` → `Pyyntö vastaanotettu`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:262` — `Ei syyta annettu` → `Ei syytä annettu`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:268,580` — `Hyvaksy peruutus` → `Hyväksy peruutus`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:275` — `Liittyva mielenosoitus` → `Liittyvä mielenosoitus`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:281` — `Virheilmoituksen sisalto` → `Virheilmoituksen sisältö`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:282` — `Ei sisaltoa` → `Ei sisältöä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:338` — `Talle tapaukselle ei ole viela omaa tarkempaa esitystapaa.` → `Tälle tapaukselle ei ole vielä omaa tarkempaa esitystapaa.`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:381` — `Sisaiset muistiinpanot` → `Sisäiset muistiinpanot`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:387` — `Merkinnan tyyppi` → `Merkinnän tyyppi`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:391` — `Oltu yhteydessa` → `Oltu yhteydessä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:395` — `Sisalto` → `Sisältö`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:396` — `Kirjoita mita tehtiin tai mita seuraavaksi tarvitaan...` → `Kirjoita, mitä tehtiin tai mitä seuraavaksi tarvitaan...`. Missing Finnish diacritics; add comma before indirect question.
- `admin_V2/cases/case.html:400` — `Lisaa merkinta` → `Lisää merkintä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:418` — `Paivitetty` → `Päivitetty`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:456,483` — `Jarjestelma` → `Järjestelmä`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:460` — `Ei lisatietoja` → `Ei lisätietoja`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:465` — `Ei viela merkintoja.` → `Ei vielä merkintöjä.`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:533` — `Tapausta ei voitu paivittaa.` → `Tapausta ei voitu päivittää.`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:551` — `Demoa ei voitu paivittaa.` → `Demoa ei voitu päivittää.`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:560` — `Syy hylkaykselle` → `Syy hylkäykselle`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:561` — `Kirjoita perustelu hylkaykselle...` → `Kirjoita perustelu hylkäykselle...`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:240,558,563` — `Hylkaa` → `Hylkää`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:582` — `Sisainen perustelu` → `Sisäinen perustelu`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:583` — `Valinnainen lisatieto...` → `Valinnainen lisätieto...`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:235,268,580,585` — `Hyvaksy` → `Hyväksy`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:600` — `Peruutusta ei voitu kasitella.` → `Peruutusta ei voitu käsitellä.`. Missing Finnish diacritics.
- `admin_V2/cases/case.html:686` — `Haku epaonnistui.` → `Haku epäonnistui.`. Missing Finnish diacritics.
- `admin_base.html:48,372` — `aria-label="Close"` → `aria-label="{{ _('Sulje') }}"`. Untranslated accessible close label, two instances.
- `admin_base.html:330` — `Vaihtele teema` → `Vaihda teemaa`. Vaihtele takes a partitive object here; idiomatic action label.
- `admin_V2/dashboard.html:125,128` — `Kirjautumiset onnistui` → `Onnistuneet kirjautumiset`. Plural subject with singular verb; noun phrase suits percentage label.
- `admin_V2/dashboard.html:165` — `Syvennä tapahtumia` → `Tarkastele tapahtumia`. Syventää tapahtumia is an incorrect idiom for inspecting logs.
- `admin_V2/demonstrations/form.html:366` — `Sijaintiedot` → `Sijaintitiedot`. Misspelled compound: sijainti + tiedot.
- `admin_V2/demonstrations/form.html:71` — `Muokkauslinkillä myöskin he joilla ei ole käyttäjätiliä sivustolle, voivat muokata
                mielenosoituksia. Muokkauslinkillä muokkaus on rajoitettua.` → `Muokkauslinkillä myös ne, joilla ei ole käyttäjätiliä sivustolla, voivat muokata mielenosoituksia. Muokkauslinkillä muokkaaminen on rajoitettua.`. Missing relative clause comma; käyttäjätili sivustolla, not sivustolle. Preserve restriction meaning.
- `admin_V2/recu_demonstrations/_form_v2.html:519` — `Sijaintiedot` → `Sijaintitiedot`. Misspelled compound: sijainti + tiedot.
- `admin_V2/recu_demonstrations/_form_v2.html:63` — `Muokkauslinkillä myöskin he joilla ei ole käyttäjätiliä sivustolle, voivat muokata
                mielenosoituksia. Muokkauslinkillä muokkaus on rajoitettua.` → `Muokkauslinkillä myös ne, joilla ei ole käyttäjätiliä sivustolla, voivat muokata mielenosoituksia. Muokkauslinkillä muokkaaminen on rajoitettua.`. Missing relative clause comma; käyttäjätili sivustolla, not sivustolle. Preserve restriction meaning.
- `admin_V2/demonstrations/translations_editor.html:197` — `Käännös ei julkaistu heti. Tarkistaja hyväksyy sen ensin.` → `Käännöstä ei julkaista heti. Tarkistaja hyväksyy sen ensin.`. Broken negative passive; explanation describes future workflow, not past publication.
- `admin_V2/recu_demonstrations/_form_v2.html:246` — `nth viikonpäivä` → `Kuukauden tietty viikonpäivä`. Untranslated nth in visible option; subsequent selector supplies occurrence ordinal.
- `admin_V2/recu_demonstrations/_form_v2.html:225` — `Kuinka monta kertaa toisto tapahtuu (esim. joka 2. viikko)` → `Kuinka monen päivän, viikon, kuukauden tai vuoden välein tapahtuma toistuu (esim. joka 2. viikko)`. Original asks total count despite describing interval; Finnish wording conflicts with own example.
- `admin_V2/recu_demonstrations/_form_v2.html:481` — `Voit täydentää toistuvalle mielenosoitukselle käännökset eri kielillä. Tyhjät kentät fallbackaavat oletuskielen sisältöön.` → `Voit täydentää toistuvalle mielenosoitukselle käännökset eri kielillä. Tyhjä kenttä tarkoittaa, että julkisessa näkymässä käytetään oletuskielen sisältöä.`. Untranslated English-derived fallbackaavat; reuse wording from single demonstration form.
- `admin_V2/governance/audit.html:11` — `Tarkastele kuka myönsi` → `Tarkastele, kuka myönsi`. Comma before indirect interrogative clause.
- `admin_V2/logs.html:11` — `Selvitä kuka teki mitä ja milloin.` → `Selvitä, kuka teki mitä ja milloin.`. Comma before indirect interrogative clause.
- `admin_V2/status.html:44` — `Gunicorn työntekijät` → `Gunicorn-työntekijät`. Hyphen joins proper name and Finnish compound.
- `admin_V2/status.html:153` — `Ei virheitä viime aikana` → `Ei virheitä viime aikoina`. Incorrect idiom; viime aikoina means recently.
- `admin_V2/overall_24h_analytics.html:4,9` — `Yhteensä katselukerrat viimeisen 24 tunnin aikana` → `Katselukerrat yhteensä viimeisen 24 tunnin aikana`. Yhteensä belongs after the noun; present word order is broken.
- `admin_V2/per_demo_analytics.html:34` — `Keskimäärin näytöksiä/minuutti` → `Keskimäärin näyttöjä/minuutti`. Näytös is a performance/show; metric counts page views, näyttö.
- `admin_V2/per_demo_analytics.html:42` — `Lataa per minuutti PNG` → `Lataa minuuttikohtainen kaavio PNG-kuvana`. Malformed download label; Finnish inflection and untranslated dashboard.
- `admin_V2/per_demo_analytics.html:46` — `Lataa per päivä PNG` → `Lataa päiväkohtainen kaavio PNG-kuvana`. Malformed download label; Finnish inflection and untranslated dashboard.
- `admin_V2/per_demo_analytics.html:50` — `Lataa per viikko PNG` → `Lataa viikkokohtainen kaavio PNG-kuvana`. Malformed download label; Finnish inflection and untranslated dashboard.
- `admin_V2/per_demo_analytics.html:54` — `Lataa kaikki PNG` → `Lataa kaikki kaaviot PNG-kuvina`. Malformed download label; Finnish inflection and untranslated dashboard.
- `admin_V2/per_demo_analytics.html:58` — `Lataa koko dashboard PNG` → `Lataa koko näkymä PNG-kuvana`. Malformed download label; Finnish inflection and untranslated dashboard.
- `admin_V2/kampanja/list.html:508` — `Tallennus epäonnistui palvelimeen: ` → `Tallennus palvelimelle epäonnistui: `. Wrong case and word order in server save error.
- `admin_V2/kampanja/list.html:662` — `Kirjoita sähköpostin viesti (plain text):` → `Kirjoita sähköpostin viesti (pelkkää tekstiä):`. Untranslated visible English explanation.
- `admin_V2/cc/confirm_action.html:39` — `Oletko varma että haluat` → `Oletko varma, että haluat`. Missing comma before että clause.
- `oauth/mcp_consent.html:6` — `MCP OAuth Consent` → `MCP OAuth -valtuutus`. Untranslated document title; retain protocol names.

## Optional / uncertain — preserve as-is

- `admin_V2/demonstrations/form.html:208` — `vain kun peruskenttien sisältö vastaa valittua kieltä` → `vain, kun peruskenttien sisältö vastaa valittua kieltä`. Potential punctuation adjustment only; leave unchanged unless Finnish reviewer confirms placement around vain kun.
- `admin_V2/kampanja/list.html:680` — `Sähköpostit lähetetty (tai tilattu palvelimelle).` → `Sähköpostit lähetetty tai lisätty palvelimen lähetysjonoon.`. Tilattu palvelimelle is incorrect idiom for queueing email, but no backend handler was found for /api/send-bulk-email; defer until endpoint behavior is verified.
- `admin_V2/background_jobs.html:76,239` — `Scheduler` → `Ajastin`. Generic actor label could be Finnish; technical scheduler terminology allowed.
- `admin_V2/background_job_detail.html:115` — `Näytä virheen stack trace` → `Näytä virheen pinolistaus`. Stack trace is established technical terminology; retain unless terminology policy changes.
- `admin_V2/ui_translations/editor.html:51` — `Branch` → `Haara`. Git terminology; preserve existing terms under task scope.
- `admin_V2/ui_translations/dashboard.html:107` — `Mergetty` → `Yhdistetty`. Git terminology; colloquial Finnish inflection, optional only.
- `admin_V2/cases/all.html:40` — `Vaatii seniorin` → `Vaatii kokeneen käsittelijän`. Seniority/role terminology may be intentional; do not change authority semantics.
- `admin_V2/demonstrations/form.html:452` — `Reitti (marssin)` → `Marssin reitti`. Understandable Finnish; optional word order only.
- `admin_V2/recu_demonstrations/_form_v2.html:606` — `Reitti (marssin)` → `Marssin reitti`. Understandable Finnish; optional word order only.
- `admin_V2/demonstrations/command_center.html:122` — `Toistuvat lapset` → `Lapsimielenosoitukset`. Existing technical parent/child model; optional consistency only.
- `oauth/mcp_consent.html:86` — `Hyväksyttävät scopet:` → `Hyväksyttävät käyttöoikeusalueet:`. OAuth scope terminology explicitly preserved; optional terminology policy change.

## Every-file inventory

| Template (relative to templates/) | Status | Finding / scope |
|---|---|---|
| `admin_base.html` | reviewed | 2 exact correction proposals above. |
| `admin_V2/_background_job_macros.html` | reviewed | No substantiated language correction. |
| `admin_V2/_modals_users.html` | reviewed | No substantiated language correction. |
| `admin_V2/_users_table.html` | reviewed | No substantiated language correction. |
| `admin_V2/analytics.html` | reviewed | No substantiated language correction. |
| `admin_V2/background_job_detail.html` | reviewed | No substantiated language correction. |
| `admin_V2/background_jobs.html` | reviewed | No substantiated language correction. |
| `admin_V2/cases/all.html` | reviewed | No substantiated language correction. |
| `admin_V2/cases/blocklist.html` | reviewed | No substantiated language correction. |
| `admin_V2/cases/case.html` | reviewed | 40 exact correction proposals above. |
| `admin_V2/cc/confirm_action.html` | reviewed | 1 exact correction proposals above. |
| `admin_V2/cc/edit_link_unavailable.html` | reviewed | No substantiated language correction. Mixed surface: public recipient of admin-generated edit link; no changes proposed. |
| `admin_V2/cc/link_already_used.html` | reviewed | No substantiated language correction. Mixed surface: admin-generated token recipient; no changes proposed. |
| `admin_V2/cities/index.html` | reviewed | No substantiated language correction. |
| `admin_V2/cities/review_dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/dashboard.html` | reviewed | 2 exact correction proposals above. |
| `admin_V2/demonstrations/_edit_link_modal.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/_media_fields.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/_organizer_editor.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/audit_log.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/audit_timeline.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/command_center.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/confirm_delete.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/form.html` | reviewed | 2 exact correction proposals above. |
| `admin_V2/demonstrations/magic_tokens.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/merge.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/review.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/submission_errors.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/translations_dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/demonstrations/translations_editor.html` | reviewed | 1 exact correction proposals above. |
| `admin_V2/developer/requests.html` | reviewed | No substantiated language correction. |
| `admin_V2/developer/user_apps.html` | reviewed | No substantiated language correction. |
| `admin_V2/governance/_tabs.html` | reviewed | No substantiated language correction. |
| `admin_V2/governance/audit.html` | reviewed | 1 exact correction proposals above. |
| `admin_V2/governance/clearances.html` | reviewed | No substantiated language correction. |
| `admin_V2/governance/dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/governance/security.html` | reviewed | No substantiated language correction. |
| `admin_V2/kampanja/list.html` | reviewed | 2 exact correction proposals above. |
| `admin_V2/logs.html` | reviewed | 1 exact correction proposals above. |
| `admin_V2/macros.html` | reviewed | No substantiated language correction. |
| `admin_V2/organizations/confirm_delete.html` | reviewed | No substantiated language correction. |
| `admin_V2/organizations/dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/organizations/form.html` | reviewed | No substantiated language correction. |
| `admin_V2/organizations/macros.html` | reviewed | No substantiated language correction. |
| `admin_V2/organizations/review_suggestion.html` | reviewed | No substantiated language correction. |
| `admin_V2/organizations/view.html` | reviewed | No substantiated language correction. |
| `admin_V2/overall_24h_analytics.html` | reviewed | 1 exact correction proposals above. |
| `admin_V2/per_demo_analytics.html` | reviewed | 6 exact correction proposals above. |
| `admin_V2/recu_demonstrations/_form_v2.html` | reviewed | 5 exact correction proposals above. |
| `admin_V2/recu_demonstrations/confirm_delete.html` | reviewed | No substantiated language correction. |
| `admin_V2/recu_demonstrations/dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/site_analytics.html` | reviewed | No substantiated language correction. |
| `admin_V2/site_demo_analytics.html` | reviewed | No substantiated language correction. |
| `admin_V2/stats.html` | reviewed | No substantiated language correction. |
| `admin_V2/status.html` | reviewed | 2 exact correction proposals above. |
| `admin_V2/super_audit/logs.html` | reviewed | No substantiated language correction. |
| `admin_V2/tag_form.html` | unused | No render/include reference found in Python/templates. English Cover Photo / Enter image URL or upload below / Cover Photo Preview retained as unused inventory; see unused proposals below. |
| `admin_V2/ui_translations/dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/ui_translations/editor.html` | reviewed | No substantiated language correction. |
| `admin_V2/ui_translations/sync_dashboard.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/_history.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/account_status.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/api_tokens.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/confirm.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/edit.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/history.html` | reviewed | No substantiated language correction. |
| `admin_V2/user/list.html` | reviewed | No substantiated language correction. |
| `admin/demonstrations/demo_diff.html` | reviewed | No substantiated language correction. Legacy directory, actively referenced by admin routes; not obsolete solely due directory name. |
| `admin/demonstrations/edit_history.html` | reviewed | No substantiated language correction. Legacy directory, actively referenced by admin routes; not obsolete solely due directory name. |
| `admin/suggestion_view.html` | reviewed | No substantiated language correction. Legacy directory, actively referenced by admin routes; not obsolete solely due directory name. |
| `admin/suggestions_list.html` | reviewed | No substantiated language correction. Legacy directory, actively referenced by admin routes; not obsolete solely due directory name. |
| `oauth/mcp_consent.html` | reviewed | 1 exact correction proposals above. Admin-only MCP OAuth authorization; technical scope keys and Client ID preserved. |

## Unused / excluded cross-checks

- `admin_V2/tag_form.html:2` — `Cover Photo` → `Kansikuva`; line 3 `Enter image URL or upload below` → `Syötä kuvan URL tai lataa tiedosto alla`; line 7 `Cover Photo Preview` → `Kansikuvan esikatselu`. Only apply if deliberately maintaining unused templates; no caller found via repository search.
- Root `macros.html` and `macros_new.html`: reviewed ownership, public demonstration cards / public email imports; excluded from admin patch. Root macros contain `Demonstration card` and `EiAiheita`, but these belong to public audit. Admin-owned macros reviewed in full: `admin_V2/macros.html`, `_background_job_macros.html`, `organizations/macros.html`.
- `_macros/_hero.html`, `_macros/_pagination.html`, `_macros/_public_search.html`, `_macros/frontend.html`: public/shared host helpers, outside admin copy ownership; admin imports `admin_V2/macros.html` instead.
- No manuals or email template content audited here.
- Technical names/role names intentionally retained: Admin, god, passkey, MFA, JSON, URI, GET/HEAD, RSS, OAuth, MCP, scope/scopet, Client ID, endpoint, blueprint, Git branch/commit/merge/push, Fuzzy, Core Web Vitals, Panic Mode, stack trace. These are not marked untranslated prose.

## Limitations / implementation notes

- Proposals for short ASCII msgids such as Hyvaksy and Hylkaa apply to that exact msgid. Longer forms have their own entries; avoid unbounded substring replacement. Line lists may include the same word inside those longer forms.
- Gettext .mo catalogs have known duplicate-msgid limitations. New Finnish msgids should remain untranslated; preserve existing i18n calls.
- Verify corrected msgids do not map to unexpected Finnish catalog values; source review alone does not establish runtime copy.
- Full template runtime reachability is not proven for every historical template; unused is only assigned where the focused reference search found no caller. Active directory membership alone is not proof.
- Copy-only fixes still need mandatory capture + independent reviewer SHIP; main agent owns that workflow.
- Report generated before edits; main agent must resolve original multiline whitespace when applying the edit-link sentence replacement.


# Admin Finnish audit: scripts, backend, GodScreen, manuals

Read-only source review in `/tmp/moso-admin-finnish`; no repository edits. Read AGENTS.md, docs/decisions.md and docs/development-workflow.md. Paths/lines refer to this snapshot. Public UI, mail bodies/subjects, translation catalog contents, generated bundles, vendor libraries and non-rendered logs are excluded. Templates other than manuals are owned by the other reviewer; selected consumers were read to establish visibility only.

## Actual spelling, case and grammar errors

| Source | Original → replacement | Reason |
|---|---|---|
| mielenosoitukset_fi/admin/admin_org_bp.py:770 | `Organisatiota ei löytynyt.` → `Organisaatiota ei löytynyt.` | Missing a. |
| mielenosoitukset_fi/admin/admin_user_bp.py:1205 | `Käyttäjä ID puuttuu.` → `Käyttäjän tunniste puuttuu.` | Broken compound/case; reuses existing Finnish wording at :977. |
| mielenosoitukset_fi/admin/admin_case_bp.py:197, :213, :230 | `Ei lisatietoja` → `Ei lisätietoja` | Missing ä. |
| mielenosoitukset_fi/admin/admin_case_bp.py:201 | `Liittyvaa mielenosoitusta ei loytynyt` → `Liittyvää mielenosoitusta ei löytynyt` | Missing diacritics. |
| mielenosoitukset_fi/admin/admin_case_bp.py:214 | `Demoa ei loytynyt` → `Demoa ei löytynyt` | Missing ö. |
| mielenosoitukset_fi/admin/admin_case_bp.py:216 | `Organisaation muutospyynto` → `Organisaation muutospyyntö` | Missing ö. |
| mielenosoitukset_fi/admin/admin_case_bp.py:975 | `Mielenosoituksen tila paivitettiin.` → `Mielenosoituksen tila päivitettiin.` | Missing ä. Existing gettext msgid: preserve wrapper/placeholders; leave en/sv to humans. |
| mielenosoitukset_fi/admin/admin_case_bp.py:1072 | `Eskaloitu esihenkilolle` → `Eskaloitu esihenkilölle` | Missing ö. Stored history label: changing source affects new entries only. No historical-data rewrite. |
| mielenosoitukset_fi/admin/admin_demo_bp.py:5568, :5591, :5669 | `Mielenosoitus ei löytynyt` → `Mielenosoitusta ei löytynyt` | Negative existential needs partitive. |
| mielenosoitukset_fi/templates/manuals/dashboard.html:35–36 | `jotta etäisintä toimintoa ei vahingossa käynnisitetä.` → `jotta toimintoa ei vahingossa käynnistetä.` | Misspelled verb and meaningless adjective in this context. Keep preceding factual five-second claim unchanged unless separately verified. |
| mielenosoitukset_fi/templates/manuals/demos.html:21 | `komennuskeskus-toimintoja` → `komentokeskustoimintoja` | Typo and unnecessary hyphen; matches section title. |
| mielenosoitukset_fi/templates/manuals/demos.html:23 | `Luo uusi mielenosoitus käyttäen` → `Luoda uuden mielenosoituksen käyttäen` | List continues `jossa voit:`; requires infinitive. |
| mielenosoitukset_fi/templates/manuals/demos.html:27 | `vain niille kaupunkeihin myönnettyjen` → `vain heille kyseisiin kaupunkeihin myönnettyjen` | Conflicting cases and unclear recipient. Permission identifiers/meaning unchanged. |
| mielenosoitukset_fi/templates/manuals/demos.html:76 | `näyttävät kuka` → `näyttävät, kuka` | Required subordinate-clause comma. |
| mielenosoitukset_fi/templates/manuals/index.html:11–12 | `Tämä käyttöohje auttaa sinua käyttämään hallintapaneelia: mielenosoitusten, käyttäjien, organisaatioiden, tilastojen, tietoturvan ja ylläpidon keskeisiin toimintoihin.` → `Tämä käyttöohje auttaa sinua käyttämään hallintapaneelin keskeisiä toimintoja: mielenosoitusten, käyttäjien ja organisaatioiden hallintaa sekä tilastoja, tietoturvaa ja ylläpitoa.` | Original list does not attach grammatically to main clause. |
| mielenosoitukset_fi/templates/manuals/organizations.html:28–29 | `hyväksyä kutsun, ja muuttaa` → `hyväksyä kutsun ja muuttaa` | No comma between coordinated infinitives. |
| mielenosoitukset_fi/templates/manuals/organizations.html:40 | `varmista että` → `varmista, että` | Subordinate-clause comma. |
| mielenosoitukset_fi/templates/manuals/recurring.html:35 | `aina kun` → `aina, kun` | Subordinate-clause comma. |
| mielenosoitukset_fi/templates/manuals/support.html:62 | `CSV- vienti` → `CSV-vienti` | Incorrect space. |
| mielenosoitukset_fi/templates/manuals/users/about_roles.html:46 | `käyttää sille määritellyt palvelut` → `käyttää sille määriteltyjä palveluja` | Object case with ongoing use. |
| mielenosoitukset_fi/templates/manuals/users/about_roles.html:96 | `superkäyttäjän laajuuksia` → `yhtä laajoja oikeuksia kuin superkäyttäjällä` | Unidiomatic noun/meaningless expression. Full sentence: `Laajemmat oikeudet kuin kaupunkiadminilla, mutta ei yhtä laajoja oikeuksia kuin superkäyttäjällä.` |
| mielenosoitukset_fi/templates/manuals/users/add.html:13 | `“Luo uusi käyttäjä”-ponnahdusikkunassa` → `“Luo uusi käyttäjä” -ponnahdusikkunassa` | Space before hyphen after multiword name; same rule applies to demos.html:23 `“Uusi mielenosoitus”-painiketta`, users/add.html:54 `“Kaupunkiadminin paikkakunnat”-kentästä`, faq.html:42 `“Luo muokkauslinkki”-toimintoa`. |

## Untranslated messages with verified rendered consumers

JSON keys, role names, action identifiers and permission constants stay unchanged. Preserve existing gettext boundaries; changing Finnish msgids does not authorize translating en/sv.

| Source | Original → replacement | Consumer/evidence |
|---|---|---|
| mielenosoitukset_fi/admin/admin_dev_bp.py:204, :220, :232, :265 | `Request not found` → `Hakemusta ei löytynyt.` | developer/requests.html:68–71 displays response.message in toast. |
| mielenosoitukset_fi/admin/admin_case_bp.py:938 | `Invalid request` → `Virheellinen pyyntö.` | cases/case.html:551 displays data.error. |
| mielenosoitukset_fi/admin/admin_case_bp.py:942, :1055 | `Demo not found` → `Mielenosoitusta ei löytynyt.` | cases/case.html:551/:617. |
| mielenosoitukset_fi/admin/admin_case_bp.py:953 | `Rejection reason required` → `Anna hylkäyksen perustelu.` | cases/case.html:551. |
| mielenosoitukset_fi/admin/admin_case_bp.py:974 | `Unknown action` → `Tuntematon toiminto.` | cases/case.html:551. |
| mielenosoitukset_fi/admin/admin_case_bp.py:1086 | `Demo status reset – requires superior review.` → `Mielenosoituksen tila palautettiin. Ylemmän tason käsittelijän on tarkistettava mielenosoitus.` | flash_message. Avoids inventing a specific role. |
| mielenosoitukset_fi/admin/admin_case_bp.py:1098 | `Case not found.` → `Tapausta ei löytynyt.` | cases/case.html:633. |
| mielenosoitukset_fi/admin/admin_case_bp.py:1133 | `Internal server error.` → `Palvelimella tapahtui virhe.` | cases/case.html:633. |
| mielenosoitukset_fi/admin/admin_case_bp.py:593 / :620 / :655 / :682 | `Linked to demo` / `Unlinked demo` / `Linked to organization` / `Unlinked organization` → `Liitetty mielenosoitukseen` / `Mielenosoituslinkki poistettu` / `Liitetty organisaatioon` / `Organisaatiolinkki poistettu` | History labels, cases/case.html history list. Source changes new entries only. |
| mielenosoitukset_fi/admin/admin_case_bp.py:1079 | `Demo status needs superior review` → `Mielenosoituksen tila vaatii ylemmän tason käsittelijän tarkistuksen` | action_logs.reason rendered verbatim at cases/case.html:460; future entries only. |
| mielenosoitukset_fi/admin/admin_case_bp.py:1118 | `Case de-escalated by superior` → `Ylemmän tason käsittelijä poisti eskaloinnin` | action_logs.reason, same consumer; future entries only. |
| mielenosoitukset_fi/admin/admin_org_bp.py:944 | `Membership ID puuttuu` → `Jäsenyyden tunniste puuttuu.` | organizations/view.html:144 displays data.error. |
| mielenosoitukset_fi/admin/admin_org_bp.py:1319, :1440, :1536 | `Missing required fields.` → `Pakollisia tietoja puuttuu.` | organizations/view.html shared action helper displays data.error. |
| mielenosoitukset_fi/admin/admin_org_bp.py:1328, :1446, :1546 | `Organization not found.` → `Organisaatiota ei löytynyt.` | Same helper. |
| mielenosoitukset_fi/admin/admin_org_bp.py:1454, :1549, :1671 | `Forbidden.` → `Sinulla ei ole oikeutta tähän toimintoon.` | Same helper. |
| mielenosoitukset_fi/admin/admin_org_bp.py:1471 | `Invitation not found.` → `Kutsua ei löytynyt.` | Same helper. |
| mielenosoitukset_fi/admin/admin_org_bp.py:1540 | `Invalid role.` → `Virheellinen rooli.` | Same helper. |
| mielenosoitukset_fi/admin/admin_org_bp.py:1647 | `Invalid role. Role must be one of these: 'member', 'admin', 'owner'` → `Virheellinen rooli. Roolin on oltava 'member', 'admin' tai 'owner'.` | Same helper. Role values must remain exact. |
| mielenosoitukset_fi/admin/admin_demo_bp.py:5546 | `Missing JSON payload` → `Pyynnön JSON-sisältö puuttuu.` | form.html:418 and recu_demonstrations/_form_v2.html:572 throw data.error, surfaced by catch. |
| mielenosoitukset_fi/admin/admin_demo_bp.py:5552 | `Missing 'address' or 'city'` → `Osoite tai kaupunki puuttuu.` | Same geocode consumers; field names unchanged in JSON. |
| mielenosoitukset_fi/admin/admin_demo_bp.py:5556 | `No coordinates found` → `Koordinaatteja ei löytynyt.` | Same geocode consumers. |

## Optional style/terminology: leave unchanged for narrow correction pass

- GodScreen.tsx:8–11/:119/:122/:127–135 and users/god_auth.py:217 mix `authenticator`, `authenticator-koodi`, `passkey`. These name distinct security factors. Optional cohesive translation to `todennussovellus` and `pääsyavain` needs terminology decision and separate security-flow verification. No actual grammar errors found in GodScreen.
- Manuals mix dashboard, Panic mode, bulk-update, magic linkit, tokenit, audit-timeline and technical identifiers. Most are explanatory labels or exact UI terms; do not blanket-replace them. `magic linkit` would orthographically be `magic-linkit`, but prefer a separate consistent terminology decision over isolated change.
- `demon`, `Demo päivitetty!`, `lapsimielenosoitus`, `Globaalit oikeudet`, `käyttötila`, `kaupunkiadmin-myönnöt`: optional clarity changes only. Do not alter permission meaning.
- manuals/stats.html:28 `demonstraatiot` → optional `mielenosoitukset` for consistency.
- manuals/index.html:23 `Yleiskatsaus dashboardista` → optional `Yleiskatsaus hallintapaneelista`.
- manuals/users/about_roles.html:383 `arkaluontoisia` is acceptable; `arkaluonteisia` is more usual, optional.
- Recurrence JS `Seuraavat __COUNT__ päivämäärää` and `__COUNT__ taukopäivää` use generic count wording; singular 1 would need plural logic, outside this text-only pass. Keep placeholder contract intact.

## Coverage / status

Every file below was inspected for displayed literals. `no-copy` means no owned Finnish display text (often copies dataset strings from templates, reviewed separately).

### Backend reviewed

- mielenosoitukset_fi/admin/admin_bp.py — reviewed; Finnish strings otherwise pass. English abort descriptions are not rendered by shared error handlers (confirmed below).
- mielenosoitukset_fi/admin/admin_case_bp.py — reviewed; findings above. Log identifiers and logger messages excluded.
- mielenosoitukset_fi/admin/admin_city_bp.py — reviewed; Finnish passes.
- mielenosoitukset_fi/admin/admin_demo_bp.py — reviewed for all literal display text; findings above; legacy endpoint visibility pending below. Raw external exception interpolation is existing behavior, not translated here.
- mielenosoitukset_fi/admin/admin_dev_bp.py — reviewed; findings above; email subject/body excluded.
- mielenosoitukset_fi/admin/admin_governance_bp.py — reviewed; Finnish passes (`sähköpostityöntekijällä` is awkward optional terminology, no automatic replacement).
- mielenosoitukset_fi/admin/admin_kampanja_bp.py — reviewed; Finnish grammar passes. Visibility claims may be stale behavior documentation; do not change during language audit.
- mielenosoitukset_fi/admin/admin_org_bp.py — reviewed; findings above; docstring copies of messages are not runtime copy.
- mielenosoitukset_fi/admin/admin_recu_demo_bp.py — reviewed; Finnish passes; `None` coordinate text is technical log output, not flash copy.
- mielenosoitukset_fi/admin/admin_site_analytics_bp.py — no-copy: only logger errors in English.
- mielenosoitukset_fi/admin/board_audit.py — reviewed; `Tuntematon käyttäjä` passes.
- mielenosoitukset_fi/admin/board_compliance.py — reviewed; Finnish passes. Exact governance policy unchanged.
- mielenosoitukset_fi/admin/user_history.py — reviewed; Finnish passes. Optional `Aika ei saatavilla` → `Aika ei ole saatavilla` is acceptable ellipsis, not required.
- mielenosoitukset_fi/admin/utils.py — reviewed; English fallback `Unknown Organization` at :40/:45 is used only by excluded email copy; user route imports it but does not call it. Operational exceptions/logs excluded.
- mielenosoitukset_fi/admin/__init__.py, pagination.py, v2.py — no-copy.
- mielenosoitukset_fi/users/god_auth.py — reviewed displayed errors; grammar passes. Emails and provisioning label excluded; authenticator terminology optional.
- mielenosoitukset_fi/utils/god_appointments.py — reviewed displayed errors and action labels; `god-hyväynnän peruminen` :226 → `god-hyväksynnän peruminen` is an actual spelling error in stored governance audit copy. Mail excluded.
- mielenosoitukset_fi/utils/god_emergency.py — reviewed; Finnish passes.

### JavaScript and React reviewed

- frontend/src/app/GodScreen.tsx — reviewed in full; no required copy changes; security terminology optional as above.
- mielenosoitukset_fi/static/js/admin_recurrence_preview.js — reviewed Finnish fallbacks, no required changes.
- mielenosoitukset_fi/static/js/admin_workspace.js — no-copy; dataset labels owned by templates.
- mielenosoitukset_fi/static/js/admin_performance_explorer.js — no-copy; dataset/report labels.
- mielenosoitukset_fi/static/js/admin_organizer_editor.js — no-copy; dataset/template copy.
- mielenosoitukset_fi/static/js/admin_demo_edit_links.js — no-copy; dataset/server copy.
- mielenosoitukset_fi/static/js/admin_deadlines.js — no-copy; dataset labels.
- mielenosoitukset_fi/static/js/admin_stats.js — no-copy; Matomo proper noun plus dataset label.
- mielenosoitukset_fi/static/js/admin_demo_info.js — no-copy (public detail admin shortcut; only console warning/window name, not rendered text).
- mielenosoitukset_fi/static/js/translation-editor.js — no-copy; dataset labels.
- mielenosoitukset_fi/static/js/webauthn.js — reviewed shared admin/public errors, Finnish passes. Passkey terminology left exact.
- mielenosoitukset_fi/static/js/date.js — reviewed calendar locale names; capitalized months optional style, no correction required.
- mielenosoitukset_fi/static/js/ckeditor-init.js — reviewed default content and headings; Finnish passes.
- mielenosoitukset_fi/static/js/modal.js, matomo.js — no-copy.

### Manuals reviewed (all visible copy, including role table and inline role suggestion script)

- mielenosoitukset_fi/templates/manual_base.html — reviewed; Finnish passes; FAQ abbreviation optional.
- mielenosoitukset_fi/templates/manuals/dashboard.html — reviewed; corrections above.
- mielenosoitukset_fi/templates/manuals/demos.html — reviewed; corrections above.
- mielenosoitukset_fi/templates/manuals/faq.html — reviewed; multiword UI-label hyphen spacing above.
- mielenosoitukset_fi/templates/manuals/index.html — reviewed; correction above.
- mielenosoitukset_fi/templates/manuals/organizations.html — reviewed; comma corrections above.
- mielenosoitukset_fi/templates/manuals/recurring.html — reviewed; comma correction above.
- mielenosoitukset_fi/templates/manuals/security.html — reviewed; Finnish passes; security factual claims unverified and unchanged.
- mielenosoitukset_fi/templates/manuals/stats.html — reviewed; optional consistent noun above.
- mielenosoitukset_fi/templates/manuals/support.html — reviewed; CSV correction above.
- mielenosoitukset_fi/templates/manuals/users.html — reviewed; Finnish passes; terminology optional.
- mielenosoitukset_fi/templates/manuals/users/about_roles.html — reviewed; corrections above. Permission chart factual correctness is outside language review.
- mielenosoitukset_fi/templates/manuals/users/add.html — reviewed; label spacing correction above. Do not change role values/policy.

## Pending / excluded follow-ups (not silently passed)

- admin_demo_bp.py:3630 `Internal server error.` → `Palvelimella tapahtui virhe.` and :3672 `An internal error has occurred.` → `Palvelimella tapahtui virhe.` are VERIFIED DISPLAYED: `_edit_link_modal.html:7–8` wires generate/email endpoints; static/js/admin_demo_edit_links.js:56/:86 displays response.message. These are required English-copy corrections, despite email send action itself remaining untouched.
- Legacy admin_demo_bp.py :5295/:5307/:5317/:5322 English accept-demo endpoint messages are NOT DISPLAYED in current dashboard.html:21–44: the client displays fixed Finnish messages and only sends data.message to console. Keep unchanged in narrow visible-copy pass. :5344 `Submitter not found.` requires detail-panel consumer review; current direct dashboard consumer logs it only.
- admin_case_bp.py:1129 `Case de-escalated successfully.` is a JSON message currently ignored by case.html success path (reload); optional localization to `Eskalointi poistettiin.` Not a required visible-copy fix.
- admin_bp.py:2311 `Invalid demo ID`, :2316 `No analytics data found` are not rendered: error_handlers.py:83–98 renders errors/404.html without passing description. :91 is a 503 description; no custom 503 handler found, so Flask default error page can display it: `Background jobs are not available in this deployment.` → `Taustatehtävät eivät ole käytettävissä tässä ympäristössä.` is a required English correction for unavailable-job state.
- admin/utils.py:40/:45 `Unknown Organization`: usage confirmed email-only in admin_org_bp.py:461/:466; imported unused in admin_user_bp.py. Excluded mail copy; no admin UI change.
- Additional shared helpers reviewed: utils/admin/organizers.py (all Finnish errors pass), utils/admin/demonstration.py and __init__.py (no-copy), utils/case.py, recurrence.py, case_access.py (no-copy), utils/city_review_dashboard.py (only format string), utils/security_cases.py and utils/team_governance.py (all displayed Finnish messages pass; emails excluded), utils/city_admin_applications.py (experience labels pass; `unknown status` is internal exception).
- Remaining broad helpers outside admin/ and explicitly reviewed utility modules, e.g. generic permissions and app-wide account validators, are not comprehensively inventoried here; parent must mark pending or rely on the public/shared audit where ownership overlaps.
- Other static/js scripts not referenced by admin templates: excluded public/shared scope; vendor/fa/leaflet, generated static/frontend bundles excluded generated/vendor.
- admin markdown TODOs and role engineering note: not rendered UI/manuals; excluded developer documentation.
- No browser captures/tests: read-only audit, no UI implementation or acceptance claim. Any eventual text implementation still needs applicable visual QA.

## Final implementation boundary

Parent selected cases-only first batch. All stored history/action/reason labels in this report (including god-hyväynnän and Eskaloitu esihenkilolle) are findings for tracking only: do not include them in this text-only patch. No existing data edits or future persisted audit-copy changes proposed for the current implementation. `/tmp/moso-admin-other-replacements.json` contains runtime-only candidate replacements with explicit per-location cautions; manual corrections remain reviewable follow-up work. Generic permission/account helper coverage remains explicitly pending as above.

## Scoped follow-up: generic permissions and account validators

Completed direct admin consumer review. Earlier generic-permissions/account-validator pending statement is superseded for the explicitly listed modules below; no claim of a repository-wide backend audit.

| File/status | Verified admin consumer / result |
|---|---|
| utils/validators.py — reviewed | admin_user_bp.py:592–594/:800–802/:1064–1066 flashes validate_username's returned strings. All Finnish passes except :100 `Käyttäjätunnuksessa ei voi olla peräkkäisiä väli- tai alaviivoja.` → `Käyttäjätunnuksessa ei voi olla peräkkäisiä yhdys- tai alaviivoja.` Reason: regex at :38 matches hyphen and underscore, not space; exact validation stays unchanged. `Käyttäjätunnus` vs `Käyttäjänimi` is optional terminology, leave unchanged. valid_email and normalization return booleans/values, no display strings. |
| utils/wrappers.py — reviewed | admin_required and permission_required imported throughout admin routes. :436/:469/:522 Finnish flashes pass. Remaining English text is logger-only, except deprecated-route flash at :574; `rg depracated_endpoint mielenosoitukset_fi/admin` finds no admin usage, so that flash is excluded from admin scope. |
| utils/step_up.py — reviewed | require_fresh_step_up imported by admin_user_bp.py:15 and used for protected admin operations; JSON message fields at :221/:242/:256 displayed by _modals_users.html error paths. Finnish passes. :148 RuntimeError is developer misuse for anonymous token generation, not a displayed admin validation response. |
| users/models.py — reviewed for direct account/permission methods | Admin user creation inserts a validated user_doc directly at admin_user_bp.py:1121–1144, bypassing create_user_v2 English missing-field/privileged-role exceptions. Admin save routes use User objects and validate before update. No direct admin consumer found for UserMFA.list_devices/add_device labels (`Unknown device`, `New device`); those belong to excluded account-security flows. User.from_OID `User not found` is an internal exception; no admin flash/JSON interpolation consumer found. Model's explicit Finnish god/super-admin errors pass and policy remains unchanged. |
| utils/variables.py — reviewed | :76 loads permission_groups.json; itself no display copy. User edit receives PERMISSIONS_GROUPS and iterates raw category/description strings. |
| utils/data/permission_groups.json — reviewed, untranslated display copy | user/edit.html:148–156 renders group_name and perm.description verbatim. Eight category headings and 45 permission descriptions are English; the KAMPANJA description stays an exact identifier. This is confirmed rendered copy, unlike permission `name` identifiers. Exact translation proposals below. API and KAMPANJA headings/identifiers left unchanged; no permission values, assignments or policies change. |
| users/BPs/auth.py — boundary evidence only, excluded authentication writes | Login reasons written in English (`MFA failed`, `User not found`, `Invalid password`, `Account disabled`, `Email not verified`) are returned from admin_user_bp.py:_serialize_login_log and displayed by _modals_users.html:519–532. These are persisted auth reasons; do not alter writers/data in current patch. Track a separate display-only mapping if approved. Public login flows and email/auth behavior excluded. |

Permission catalog proposals (all same reason: untranslated display label, direct consumer above; every identifier in `name` preserved):

| Source | Original → replacement |
|---|---|
| mielenosoitukset_fi/utils/data/permission_groups.json:2 | `Recurring Demonstration Management` → `Toistuvien mielenosoitusten hallinta` |
| mielenosoitukset_fi/utils/data/permission_groups.json:5 | `Create recurring demos.` → `Luo toistuvia mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:9 | `Edit recurring demos.` → `Muokkaa toistuvia mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:13 | `Delete recurring demos.` → `Poista toistuvia mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:17 | `View recurring demo details.` → `Katsele toistuvien mielenosoitusten tietoja.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:21 | `List all recurring demos.` → `Listaa kaikki toistuvat mielenosoitukset.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:24 | `Background Jobs` → `Taustatehtävät` |
| mielenosoitukset_fi/utils/data/permission_groups.json:27 | `View job schedules and history.` → `Katsele tehtävien ajastuksia ja suoritushistoriaa.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:31 | `Run jobs manually and change schedules.` → `Suorita tehtäviä käsin ja muuta ajastuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:34 | `Demonstration Management` → `Mielenosoitusten hallinta` |
| mielenosoitukset_fi/utils/data/permission_groups.json:37 | `Create demos.` → `Luo mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:41 | `Edit demos.` → `Muokkaa mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:45 | `Delete demos.` → `Poista mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:49 | `View demo details.` → `Katsele mielenosoitusten tietoja.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:53 | `List all demos.` → `Listaa kaikki mielenosoitukset.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:57 | `Can accept demos.` → `Hyväksy mielenosoituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:61 | `Generate edit link for demo.` → `Luo mielenosoituksen muokkauslinkki.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:65 | `Create translation proposals for demos.` → `Tee mielenosoitusten käännösehdotuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:69 | `Approve or reject demo translation proposals.` → `Hyväksy tai hylkää mielenosoitusten käännösehdotuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:73 | `Create UI translation proposals for gettext strings.` → `Tee käyttöliittymän gettext-tekstien käännösehdotuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:77 | `Approve or reject UI translation proposals.` → `Hyväksy tai hylkää käyttöliittymän käännösehdotuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:80 | `Case Management` → `Tapausten hallinta` |
| mielenosoitukset_fi/utils/data/permission_groups.json:83 | `Create cases.` → `Luo tapauksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:87 | `Edit cases.` → `Muokkaa tapauksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:91 | `Delete cases.` → `Poista tapauksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:95 | `View case details.` → `Katsele tapausten tietoja.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:99 | `List all cases.` → `Listaa kaikki tapaukset.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:102 | `User Management` → `Käyttäjien hallinta` |
| mielenosoitukset_fi/utils/data/permission_groups.json:105 | `View user details.` → `Katsele käyttäjien tietoja.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:109 | `Edit users.` → `Muokkaa käyttäjiä.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:113 | `Delete users.` → `Poista käyttäjiä.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:117 | `List all users.` → `Listaa kaikki käyttäjät.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:121 | `Create new users.` → `Luo uusia käyttäjiä.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:125 | `Force a user to change their password.` → `Pakota käyttäjä vaihtamaan salasanansa.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:128 | `Organization Management` → `Organisaatioiden hallinta` |
| mielenosoitukset_fi/utils/data/permission_groups.json:131 | `Create organizations.` → `Luo organisaatioita.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:135 | `Edit organizations.` → `Muokkaa organisaatioita.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:139 | `Delete organizations.` → `Poista organisaatioita.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:143 | `View organization details.` → `Katsele organisaatioiden tietoja.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:147 | `List all organizations.` → `Listaa kaikki organisaatiot.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:151 | `Can invite users to organization.` → `Kutsu käyttäjiä organisaatioon.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:154 | `Administration` → `Ylläpito` |
| mielenosoitukset_fi/utils/data/permission_groups.json:157 | `View administrative and submission error logs.` → `Katsele ylläpitolokeja ja ilmoitusten virhelokeja.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:161 | `Grant and revoke board clearances.` → `Myönnä ja peru hallituksen turvaluokituksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:165 | `View the board-clearance audit log.` → `Katsele hallituksen turvaluokitusten tarkastuslokia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:169 | `Publish cities and manage city settings.` → `Julkaise kaupunkeja ja hallitse niiden asetuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:172 | `Miscellaneous` → `Muut oikeudet` |
| mielenosoitukset_fi/utils/data/permission_groups.json:175 | `View analytics` → `Katsele analytiikkaa.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:179 | `can access beta` → `Käytä beta-ominaisuuksia.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:185 | `Read API data.` → `Lue tietoja API:n kautta.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:189 | `Write via API.` → `Kirjoita tietoja API:n kautta.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:193 | `Write demos via API.` → `Kirjoita mielenosoituksia API:n kautta.` |
| mielenosoitukset_fi/utils/data/permission_groups.json:197 | `Admin-level API access.` → `Ylläpitäjätason API-käyttöoikeus.` |

Catalog heading keys can be consumed structurally; use a display mapping if external contracts require preserving keys. This report proposes copy, not a permission schema migration. No source/catalog edits. JSON includes candidate mappings with the same caution.
