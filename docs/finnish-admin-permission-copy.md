# Finnish permission editor display — batch 5

Source review: `gpt-6.1-sol`, 15 November 2026. Full audit PR #1165.
Eight English category headings and 45 descriptions are localized ONLY in the
Finnish admin user-editor GET display. Two admin-owned dictionaries are keyed
by existing category keys/stable permission identifiers. API/KAMPANJA headings
and KAMPANJA description intentionally fall back unchanged.

The shared `permission_groups.json` also builds account-change email summaries,
so changing it would touch excluded email content. It remains byte-identical.
Assignment validation, IDs/checkbox values/checked logic, group iteration/order,
POST paths and emails retain the original catalog. Non-Finnish editor displays
retain existing raw English text; no en/sv translations are generated or edited.
New Finnish copy uses the existing lazy `N_` gettext pattern (all 53 msgids
verified extractable). Template lookups default to empty maps for direct renders.
No public/UI asset source changes, generated rebuild or production access.

## Exact display proposals

| Original catalog display text | Finnish editor display |
| --- | --- |
| Recurring Demonstration Management | Toistuvien mielenosoitusten hallinta |
| Background Jobs | Taustatehtävät |
| Demonstration Management | Mielenosoitusten hallinta |
| Case Management | Tapausten hallinta |
| User Management | Käyttäjien hallinta |
| Organization Management | Organisaatioiden hallinta |
| Administration | Ylläpito |
| Miscellaneous | Muut oikeudet |
| Create recurring demos. | Luo toistuvia mielenosoituksia. |
| Edit recurring demos. | Muokkaa toistuvia mielenosoituksia. |
| Delete recurring demos. | Poista toistuvia mielenosoituksia. |
| View recurring demo details. | Katsele toistuvien mielenosoitusten tietoja. |
| List all recurring demos. | Listaa kaikki toistuvat mielenosoitukset. |
| View job schedules and history. | Katsele tehtävien ajastuksia ja suoritushistoriaa. |
| Run jobs manually and change schedules. | Suorita tehtäviä käsin ja muuta ajastuksia. |
| Create demos. | Luo mielenosoituksia. |
| Edit demos. | Muokkaa mielenosoituksia. |
| Delete demos. | Poista mielenosoituksia. |
| View demo details. | Katsele mielenosoitusten tietoja. |
| List all demos. | Listaa kaikki mielenosoitukset. |
| Can accept demos. | Hyväksy mielenosoituksia. |
| Generate edit link for demo. | Luo mielenosoituksen muokkauslinkki. |
| Create translation proposals for demos. | Tee mielenosoitusten käännösehdotuksia. |
| Approve or reject demo translation proposals. | Hyväksy tai hylkää mielenosoitusten käännösehdotuksia. |
| Create UI translation proposals for gettext strings. | Tee käyttöliittymän gettext-tekstien käännösehdotuksia. |
| Approve or reject UI translation proposals. | Hyväksy tai hylkää käyttöliittymän käännösehdotuksia. |
| Create cases. | Luo tapauksia. |
| Edit cases. | Muokkaa tapauksia. |
| Delete cases. | Poista tapauksia. |
| View case details. | Katsele tapausten tietoja. |
| List all cases. | Listaa kaikki tapaukset. |
| View user details. | Katsele käyttäjien tietoja. |
| Edit users. | Muokkaa käyttäjiä. |
| Delete users. | Poista käyttäjiä. |
| List all users. | Listaa kaikki käyttäjät. |
| Create new users. | Luo uusia käyttäjiä. |
| Force a user to change their password. | Pakota käyttäjä vaihtamaan salasanansa. |
| Create organizations. | Luo organisaatioita. |
| Edit organizations. | Muokkaa organisaatioita. |
| Delete organizations. | Poista organisaatioita. |
| View organization details. | Katsele organisaatioiden tietoja. |
| List all organizations. | Listaa kaikki organisaatiot. |
| Can invite users to organization. | Kutsu käyttäjiä organisaatioon. |
| View administrative and submission error logs. | Katsele ylläpitolokeja ja ilmoitusten virhelokeja. |
| Grant and revoke board clearances. | Myönnä ja peru hallituksen turvaluokituksia. |
| View the board-clearance audit log. | Katsele hallituksen turvaluokitusten tarkastuslokia. |
| Publish cities and manage city settings. | Julkaise kaupunkeja ja hallitse niiden asetuksia. |
| View analytics | Katsele analytiikkaa. |
| can access beta | Käytä beta-ominaisuuksia. |
| Read API data. | Lue tietoja API:n kautta. |
| Write via API. | Kirjoita tietoja API:n kautta. |
| Write demos via API. | Kirjoita mielenosoituksia API:n kautta. |
| Admin-level API access. | Ylläpitäjätason API-käyttöoikeus. |

## Verification

**47 focused existing/extended tests passed**: admin user management,
account-status/direct-render compatibility, assignable VIEW_LOGS and admin flash
i18n. One regression contract checks every permission checkbox `(name,value,checked)`
against original catalog and target rights, all three fi/en/sv displays, unchanged
shared in-memory catalog and an unchanged target DB document after each GET.
No permission/account writes are needed for this display proof. Existing fixture
JWT warnings only. Source catalog SHA-256:
`bbf9e8d22aa6ae0dc696e3fe4503e92383a12981bf59ff18bfeab4d1635ee76a`.

36 before and 36 after real fixture-backed images capture nine affected permission
categories at 390/1440px, both themes; nonchanging KAMPANJA excluded. No missing
resources/JavaScript errors. Evidence `/tmp/moso-admin-visual/permissions-before/`
and `permissions-after/`. Final source snapshot includes the new untracked copy
module explicitly, not just git HEAD. Independent fresh-context visual verdict: **SHIP**, all 72 images opened at
readable scale, both widths/themes and all changed groups. Longer descriptions
wrap cleanly; checkbox alignment/contrast/controls remain consistent. Source
and image digests verified after review. Packet
`/tmp/moso-admin-visual/review-permissions.json`; verdict
`/tmp/moso-admin-visual/verdict-permissions.md`. Acceptance covers displayed
copy only, not authorization or whole-admin behavior. Changelog rendered;
CodeRabbit CLI reviewed all six source/test/new note/report files: zero findings.

Reviewed source hashes:


- `mielenosoitukset_fi/admin/admin_user_bp.py`: `49da5225910a97234202b79027dde46441a711d21e59518dacc1665fe5a30f7f`

- `mielenosoitukset_fi/templates/admin_V2/user/edit.html`: `7be35674e762fb3f44799254c3bbbf3908f928251150590deeceeaba4ef6b687`

- `tests/test_admin_user_bp.py`: `58b2a62faf4a39a82c490eee4aa50b1d5cdbf11486a519581743886929385d98`

- `mielenosoitukset_fi/admin/permission_copy.py`: `1af86224f2e524ecc6fe6b07e175d29e6385a1fd66ca4b4f8fb318dd0690006e`
