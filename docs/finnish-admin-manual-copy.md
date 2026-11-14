# Admin manual Finnish corrections — batch 4

Source review: `gpt-6.1-sol`, 15 November 2026. Parent audit PR #1165.
Text-only corrections in existing admin manuals; no layout, permissions,
policy or documented timing changes. Existing English/Swedish catalogs untouched;
these manual paragraphs were raw Finnish before and after.

## Exact corrections

| Manual | Original | Replacement | Reason |
| --- | --- | --- | --- |
| `dashboard.html` | jotta etäisintä toimintoa ei     vahingossa käynnisitetä. | jotta toimintoa ei     vahingossa käynnistetä. | Typo and meaningless adjective; five-second claim unchanged. |
| `demos.html` | komennuskeskus-toimintoja | komentokeskustoimintoja | Misspelling and compound spelling. |
| `demos.html` | Luo uusi mielenosoitus käyttäen | Luoda uuden mielenosoituksen käyttäen | List continues jossa voit; infinitive required. |
| `demos.html` | vain niille kaupunkeihin myönnettyjen | vain kyseisiin kaupunkeihin myönnettyjen | Conflicting case forms; city scope unchanged. |
| `demos.html` | näyttävät kuka | näyttävät, kuka | Comma before indirect question. |
| `demos.html` | “Uusi mielenosoitus”</strong>-painiketta | “Uusi mielenosoitus”</strong> -painiketta | Space before compound hyphen after multiword label. |
| `index.html` | Tämä käyttöohje auttaa sinua käyttämään hallintapaneelia: mielenosoitusten, käyttäjien,     organisaatioiden, tilastojen, tietoturvan ja ylläpidon keskeisiin toimintoihin. | Tämä käyttöohje auttaa sinua käyttämään hallintapaneelin keskeisiä toimintoja: mielenosoitusten,     käyttäjien ja organisaatioiden hallintaa sekä tilastoja, tietoturvaa ja ylläpitoa. | Original list does not attach grammatically to main clause. |
| `organizations.html` | hyväksyä kutsun,     ja muuttaa | hyväksyä kutsun     ja muuttaa | Remove comma between coordinated infinitives. |
| `organizations.html` | varmista että | varmista, että | Comma before subordinate clause. |
| `support.html` | CSV- vienti | CSV-vienti | Initialism compound spacing. |
| `users/about_roles.html` | superkäyttäjän laajuuksia. | yhtä laajoja oikeuksia kuin superkäyttäjällä. | Replace unidiomatic comparison, authority meaning unchanged. |
| `users/add.html` | “Luo uusi käyttäjä”</strong>-ponnahdusikkunassa | “Luo uusi käyttäjä”</strong> -ponnahdusikkunassa | Space before compound hyphen after multiword label. |
| `users/add.html` | “Kaupunkiadminin paikkakunnat”-kentästä | “Kaupunkiadminin paikkakunnat” -kentästä | Space before compound hyphen after multiword label. |
| `faq.html` | “Luo muokkauslinkki”</strong>-toimintoa | “Luo muokkauslinkki”</strong> -toimintoa | Space before compound hyphen after multiword label. |

## Review adjudication / limits

Do not treat every initial proposal as an actual error. `Voi ... käyttää sille
määritellyt palvelut` permits a total-object reading; its partitive rewrite is
optional style and remains unchanged. The `aina kun` comma proposal remains
unchanged as optional punctuation here. Established technical terms, factual
policy/timing claims and identifiers are unchanged. The city-scope sentence
only repairs `niille kaupunkeihin` to `kyseisiin kaupunkeihin`; no new scope or
permission meaning is introduced.

## Verification

Before capture: 48 real fixture-backed viewport images across index,
dashboard, demo list/audit, organization guidance, recurring reference,
support, FAQ, add-user top/city field and regular/admin role cards. Both
390/1440px and light/dark, readable scroll positions. Unchanged recurring/user
card captures are reference context only. Evidence under
`/tmp/moso-admin-visual/manuals-before/`. Real styles/fonts; no missing resources.
No account operations or email actions. Safe disposable DB cleanup only.
Existing manual page/content tests: **18 passed**. Changelog rendered; CodeRabbit
CLI completed on all eight changed templates with zero findings. Independent
fresh-context visual verdict: **SHIP**, all 100 images opened, both widths and
theme preferences. Four supplemental horizontal-pan mobile images show the
changed admin-role sentence remainder. Source/image hashes verified after review.

Existing roles-page mobile horizontal overflow remains outside this text-only
scope; the supplemental pan proves readable changed text without claiming the
layout is fixed. Existing manual_base content stays light under both theme
preferences. No whole-admin or manual redesign certification. Packet
`/tmp/moso-admin-visual/review-manuals.json`; verdict
`/tmp/moso-admin-visual/verdict-manuals.md`.

Reviewed source hashes:


- `mielenosoitukset_fi/templates/manuals/dashboard.html`: `66c9dba70c85d647dbd4bb8fce1f16f9ada72d9efd11ef940990c599ba89cc3d`

- `mielenosoitukset_fi/templates/manuals/demos.html`: `d15096872a76c4f1422f759c0b9a46736d44f313c51032cfc9ab01a9a8c609e5`

- `mielenosoitukset_fi/templates/manuals/faq.html`: `474b1b048adafca3a159a399254fd0c9eda497d6cdbec603b5285447996449ba`

- `mielenosoitukset_fi/templates/manuals/index.html`: `18ffe13987ca0f93c99571cc7e83cf4318436983af6020563f8682ee15ec90d2`

- `mielenosoitukset_fi/templates/manuals/organizations.html`: `2a14393db3903d810869ef2684fa0957bc0c2b338350124bd59407375c87baf9`

- `mielenosoitukset_fi/templates/manuals/support.html`: `8d712a2978826ba10d7da2d839f5218e90ee6ca460089fcdfca98e9e90667860`

- `mielenosoitukset_fi/templates/manuals/users/about_roles.html`: `4a43c53fe03fb7d93a252a044baf2a92fca6fad0b99a3f285602713e71e01ca2`

- `mielenosoitukset_fi/templates/manuals/users/add.html`: `7460902ea170656a7b88c807a4578496fb926cbd1d8371f22037976114fdf195`
