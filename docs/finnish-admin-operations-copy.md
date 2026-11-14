# Admin operations Finnish corrections — batch 3

Source review: `gpt-6.1-sol`, 15 November 2026. Full audit: PR #1165;
editor/dashboard batch: PR #1166. Five existing templates, text only.
No styles, markup structure, data, authorization or chart/export behavior changes.
No existing translations for old msgids in committed fi/en/sv catalogs; catalogs
unchanged. No frontend source changes or asset rebuild needed.

## Exact corrections

| Template (under templates/) | Original | Replacement | Reason |
| --- | --- | --- | --- |
| `admin_V2/governance/audit.html` | Tarkastele kuka myönsi | Tarkastele, kuka myönsi | Comma before indirect interrogative clause. |
| `admin_V2/logs.html` | Selvitä kuka teki mitä ja milloin. | Selvitä, kuka teki mitä ja milloin. | Comma before indirect interrogative clause. |
| `admin_V2/status.html` | Gunicorn työntekijät | Gunicorn-työntekijät | Hyphen joins proper name and Finnish compound. |
| `admin_V2/status.html` | Ei virheitä viime aikana | Ei virheitä viime aikoina | Incorrect idiom; viime aikoina means recently. |
| `admin_V2/overall_24h_analytics.html` | Yhteensä katselukerrat viimeisen 24 tunnin aikana | Katselukerrat yhteensä viimeisen 24 tunnin aikana | Yhteensä belongs after the noun; present word order is broken. |
| `admin_V2/per_demo_analytics.html` | Keskimäärin näytöksiä/minuutti | Keskimäärin näyttöjä/minuutti | Näytös is a performance/show; metric counts page views, näyttö. |
| `admin_V2/per_demo_analytics.html` | Lataa per minuutti PNG | Lataa minuuttikohtainen kaavio PNG-kuvana | Malformed download label; Finnish inflection and untranslated dashboard. |
| `admin_V2/per_demo_analytics.html` | Lataa per päivä PNG | Lataa päiväkohtainen kaavio PNG-kuvana | Malformed download label; Finnish inflection and untranslated dashboard. |
| `admin_V2/per_demo_analytics.html` | Lataa per viikko PNG | Lataa viikkokohtainen kaavio PNG-kuvana | Malformed download label; Finnish inflection and untranslated dashboard. |
| `admin_V2/per_demo_analytics.html` | Lataa kaikki PNG | Lataa kaikki kaaviot PNG-kuvina | Malformed download label; Finnish inflection and untranslated dashboard. |
| `admin_V2/per_demo_analytics.html` | Lataa koko dashboard PNG | Lataa koko näkymä PNG-kuvana | Malformed download label; Finnish inflection and untranslated dashboard. |

## Verification

Before captures: 28 actual fixture-backed images at 390/1440px, both themes:
log and governance intros; server and no-recent-errors panels; overall analytics
intro; demonstration analytics summary and all five download buttons. Seeded
nonzero analytics for both current UTC and Helsinki day. Real Chart.js,
html2canvas, CSS/fonts loaded; no external assets missing. No download clicks,
real emails or production access. Disposable test DB with restricted cleanup.
Evidence `/tmp/moso-admin-visual/operations-before/`.

Focused existing logs/governance/stats/status/admin UI/flash i18n tests:
**105 passed**. Existing deprecation/fixture JWT warnings only. Changelog
rendered and diff whitespace check passed. CodeRabbit CLI completed on all
five changed templates: zero findings.

Independent fresh-context visual verdict: **SHIP**, all 56 before/after images
opened at readable scale, both widths and themes. Longer PNG button labels
wrap cleanly without lost controls. Source/image digests verified after review.
Packet `/tmp/moso-admin-visual/review-operations.json`; verdict
`/tmp/moso-admin-visual/verdict-operations.md`. Limited to changed text and
captured states; export execution and the whole admin are not certified.

Reviewed source hashes:


- `mielenosoitukset_fi/templates/admin_V2/governance/audit.html`: `3758145a35108ecaa1a65774f85ee5d97e3d799c7ffb81b678a819d410c238a6`

- `mielenosoitukset_fi/templates/admin_V2/logs.html`: `5ce3eedbe3964d1258b46408558970935cffb5f2c6b229b66bd67e130162037e`

- `mielenosoitukset_fi/templates/admin_V2/overall_24h_analytics.html`: `767a807ce7cf2cb3b23e231181cfbfa70ae9925f8790c1f6c06ac21e1bdb28be`

- `mielenosoitukset_fi/templates/admin_V2/per_demo_analytics.html`: `62693bfdf10d15c71b9522e109c7627034d65fda0876547a16139a5ad183567e`

- `mielenosoitukset_fi/templates/admin_V2/status.html`: `8854f296c8c9cccc97fe5d57bf76b029f969c53136317d912b74b7bfe4ca504d`
