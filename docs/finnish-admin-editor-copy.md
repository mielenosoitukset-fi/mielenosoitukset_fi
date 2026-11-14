# Admin editor Finnish corrections — batch 2

Source review: `gpt-6.1-sol`, 15 November 2026. Full admin source inventory and
remaining proposals: [PR #1165](https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/pull/1165)
and `docs/finnish-admin-copy-audit.md` once merged.

This batch changes only four existing templates: dashboard, demonstration
form, recurring form and demonstration translation editor. No markup/style,
contracts, auth, role identifiers or stored data changes. The guest edit-link
explanation and optional comma changes remain outside this batch. No en/sv
catalog translations exist for any old string; catalogs are unchanged. No
frontend source changes, so no generated asset rebuild is needed.

## Exact corrections

| Template (under templates/) | Original | Replacement | Reason |
| --- | --- | --- | --- |
| `admin_V2/dashboard.html` | Kirjautumiset onnistui | Onnistuneet kirjautumiset | Plural subject with singular verb; noun phrase suits percentage label. |
| `admin_V2/dashboard.html` | Syvennä tapahtumia | Tarkastele tapahtumia | Syventää tapahtumia is an incorrect idiom for inspecting logs. |
| `admin_V2/demonstrations/form.html` | Sijaintiedot | Sijaintitiedot | Misspelled compound: sijainti + tiedot. |
| `admin_V2/recu_demonstrations/_form_v2.html` | Sijaintiedot | Sijaintitiedot | Misspelled compound: sijainti + tiedot. |
| `admin_V2/demonstrations/translations_editor.html` | Käännös ei julkaistu heti. Tarkistaja hyväksyy sen ensin. | Käännöstä ei julkaista heti. Tarkistaja hyväksyy sen ensin. | Broken negative passive; explanation describes future workflow, not past publication. |
| `admin_V2/recu_demonstrations/_form_v2.html` | nth viikonpäivä | Kuukauden tietty viikonpäivä | Untranslated nth in visible option; subsequent selector supplies occurrence ordinal. |
| `admin_V2/recu_demonstrations/_form_v2.html` | Kuinka monta kertaa toisto tapahtuu (esim. joka 2. viikko) | Kuinka monen päivän, viikon, kuukauden tai vuoden välein tapahtuma toistuu (esim. joka 2. viikko) | Original asks total count despite describing interval; Finnish wording conflicts with own example. |
| `admin_V2/recu_demonstrations/_form_v2.html` | Voit täydentää toistuvalle mielenosoitukselle käännökset eri kielillä. Tyhjät kentät fallbackaavat oletuskielen sisältöön. | Voit täydentää toistuvalle mielenosoitukselle käännökset eri kielillä. Tyhjä kenttä tarkoittaa, että julkisessa näkymässä käytetään oletuskielen sisältöä. | Untranslated English-derived fallbackaavat; reuse wording from single demonstration form. |

## Verification

Before captures: real fixture-backed Flask app, 390/1440px and light/dark,
eight affected states (32 images): dashboard metric/action links, both
location headings, recurring interval and translation guidance, selected
monthly weekday mode, translation submission guidance. The monthly option
is captured selected in the actual native select; no popup-menu fidelity is
claimed. Evidence and source hashes: `/tmp/moso-admin-visual/editors-before/`.
Uses existing disposable DB fixtures and restricted cleanup plugin; no real
email/external writes.

Existing focused tests: admin demo/recu views, demo translation workflow, admin UI
contracts, admin flash i18n and public locale restrictions: **121 passed**.
Existing deprecation/fixture JWT warnings only. Changelog rendered; diff whitespace
check passed. CodeRabbit CLI reviewed all four changed templates: zero findings.

Independent visual review: **SHIP**, fresh-context reviewer opened all 68 images.
The initial desktop translation capture missed the changed step-3 sentence;
four focused before/after desktop images resolved the gap without source edits.
Packet `/tmp/moso-admin-visual/review-editors.json`, detailed verdict
`/tmp/moso-admin-visual/verdict-editors.md`. Source/image hashes verified again
after review. The native monthly label fits in the closed selected control;
opened OS popup is outside the visual verdict. No missing assets/JS errors.

Reviewed source hashes:


- `mielenosoitukset_fi/templates/admin_V2/dashboard.html`: `a50338725b176b7594cb08280f9f27d5dc2d1cba5c8ce7f34535523112336c7a`

- `mielenosoitukset_fi/templates/admin_V2/demonstrations/form.html`: `2e43bbf4c2ff61c2bcd137b726b81950100ada60452dd047a0feefc6565888f2`

- `mielenosoitukset_fi/templates/admin_V2/demonstrations/translations_editor.html`: `4438baee319e416c3f0ede4c51378c6c8f651c95786836bfa06d84d530326635`

- `mielenosoitukset_fi/templates/admin_V2/recu_demonstrations/_form_v2.html`: `dc88c7c0af89013630201a2a0a512c54b94dd50ac26b33522ea18ea4f76fa1d3`
