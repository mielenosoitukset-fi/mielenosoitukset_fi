"""Pages, task walkthroughs and search index of the admin manual.

The manual lives at ``/admin/manual/<page>``. Only the pages listed here are
served, so a crafted path never reaches the template loader. Each page lists
its searchable topics (anchor, title, keywords); ``tests/test_manual_pages.py``
checks that every anchor exists in the rendered page, so the search never
points at a missing section.

``HELP_LINKS`` maps admin endpoints to the manual section that explains them.
The admin sidebar uses it for its "Ohje tähän näkymään" link.

``access`` names a check from ``ACCESS_CHECKS``. The manual uses it only to
decide whether to show an "Avaa näkymä" shortcut and to sort "your tools"
first; every page of the manual stays readable to every signed-in user, and the
target routes keep their own permission checks.
"""

from flask import url_for


def _perm(*permissions):
    return lambda user: any(user.has_permission(p) for p in permissions)


def _city_or(*permissions):
    return lambda user: user.has_city_admin_scope_grants() or any(
        user.has_permission(p) for p in permissions
    )


def _role(*roles):
    return lambda user: getattr(user, "role", None) in roles


def _any(*checks):
    return lambda user: any(check(user) for check in checks)


# Mirrors the conditions of the admin sidebar in ``admin_base.html``.
ACCESS_CHECKS = {
    "dashboard": lambda user: True,
    "demos": _city_or("LIST_DEMOS"),
    "cases": _perm("LIST_CASES"),
    "recurring": _perm("LIST_RECURRING_DEMOS"),
    "organizations": _perm("LIST_ORGANIZATIONS"),
    "users": _perm("LIST_USERS"),
    "suggestions": _perm("EDIT_DEMO"),
    "submission_errors": _perm("VIEW_LOGS"),
    "translations": _any(
        _role("admin", "global_admin", "translator"),
        _perm("TRANSLATE_DEMO", "REVIEW_DEMO_TRANSLATIONS"),
    ),
    "analytics": _perm("VIEW_ANALYTICS"),
    "jobs": _perm("VIEW_BACKGROUND_JOBS"),
    "logs": _perm("VIEW_LOGS"),
    "developer": _perm("EDIT_USER"),
    "governance": lambda user: bool(getattr(user, "global_admin", False))
    or any(user.has_permission(p) for p in ("MANAGE_CLEARANCE", "VIEW_CLEARANCE_AUDIT", "MANAGE_CITIES")),
    "tokens": lambda user: bool(getattr(user, "global_admin", False)),
    "account": lambda user: True,
    "status": lambda user: bool(user.has_admin_access()),
}


def can_open(user, access):
    """Whether ``user`` would see the sidebar entry behind ``access``."""
    if not access:
        return True
    check = ACCESS_CHECKS.get(access)
    if check is None:
        return False
    try:
        return bool(check(user))
    except Exception:  # a broken user object must not break the manual
        return False


# Groups in the order of the admin sidebar.
GROUPS = (
    ("start", "Aloitus"),
    ("content", "Sisältö"),
    ("people", "Käyttäjät ja hallinto"),
    ("system", "Järjestelmä"),
)

# slug -> page. Order is the reading order (previous/next links).
PAGES = {
    "index": {
        "title": "Aloita tästä",
        "icon": "fa-compass",
        "group": "start",
        "summary": "Haku, yleisimmät tehtävät ja sinun työkalusi.",
        "topics": [
            ("tehtavat", "Yleisimmät tehtävät", "pikaohje tehtävä miten"),
            ("uutta", "Uutta hallintapaneelissa", "muutokset päivitys uutta"),
            ("tyokalut", "Sinun työkalusi", "oikeudet näkymät"),
        ],
    },
    "dashboard": {
        "title": "Hallintapaneeli",
        "icon": "fa-house",
        "group": "start",
        "access": "dashboard",
        "endpoint": "admin.admin_dashboard",
        "summary": "Etusivu, tilannekuva, kaupunkiadminin käsittelyjono ja hätäkatkaisin.",
        "topics": [
            ("etusivu", "Etusivun näkymä", "tilastokortit kirjautumispäiväkirja pikalinkit"),
            ("kaupunkiadmin", "Kaupunkiadminin käsittelyjono", "kaupunki määräaika jono kansallinen tiimi"),
            ("hatakatkaisin", "Hätäkatkaisin (panic mode)", "panic lukitus kriisi sulje sivusto"),
            ("navigointi", "Valikko, teema ja uloskirjautuminen", "sivupalkki tumma vaalea teema kirjaudu ulos"),
        ],
    },
    "cases": {
        "title": "Tapaukset",
        "icon": "fa-briefcase",
        "group": "people",
        "access": "cases",
        "endpoint": "admin_case.cases",
        "summary": "Kaikki käsiteltävät asiat yhdessä jonossa: uudet mielenosoitukset, ehdotukset, peruutukset ja tukipyynnöt.",
        "topics": [
            ("tyypit", "Tapaustyypit", "uusi mielenosoitus muutosehdotus virheilmoitus peruutuspyyntö tukipyyntö kaupunkiadminhakemus organisaatio"),
            ("lista", "Tapauslista ja suodattimet", "avoimet suljetut kriittiset eskaloinnit suodata"),
            ("ilmoitukset", "Ilmoitukset uusista tapauksista", "kello ilmoitus sähköposti tilaa lopeta notification bell"),
            ("kasittele-uusi", "Käsittele uusi mielenosoitus", "hyväksy hylkää uusi ilmoitus moderointi"),
            ("kaksoiskappale", "Hylkää kaksoiskappale", "duplikaatti kaksoiskappale tupla sama alkuperäinen duplicate"),
            ("syyt", "Hylkäämisen ja sulkemisen syyt", "syy roskaposti ei kuulu palveluun puutteelliset tiedot muu syy käsitelty"),
            ("muutosehdotus", "Käsittele muutosehdotus", "ehdotus muutosehdotus korjaus suggestion"),
            ("peruutus", "Hyväksy peruutuspyyntö", "peruutus peru peruttu cancel"),
            ("vastaa", "Vastaa ilmoittajalle", "vastaa sähköposti viesti markdown"),
            ("sulje", "Sulje tai avaa tapaus uudelleen", "sulje avaa uudelleen automaattisesti suljettu"),
            ("eskaloi", "Eskaloi tapaus", "eskaloi esihenkilö senior"),
            ("merkinnat", "Merkinnät ja liitokset", "muistiinpano seurantatoimi oltu yhteydessä liitä mielenosoitus organisaatio"),
            ("estolista", "Tukipyyntöjen estolista", "estä roskaposti estolista blocklist"),
        ],
    },
    "demos": {
        "title": "Mielenosoitukset",
        "icon": "fa-bullhorn",
        "group": "content",
        "access": "demos",
        "endpoint": "admin_demo.demo_control",
        "summary": "Lista, hyväksyminen, muokkaus, muokkauslinkit, historia ja yhdistäminen.",
        "topics": [
            ("lista", "Lista ja suodattimet", "hae suodata menneet toistuvat tunniste"),
            ("hyvaksy", "Hyväksy tai hylkää", "hyväksy hylkää odottaa käsittele päätös tapaus julkaise heti julkaisun tila"),
            ("peru-hyvaksynta", "Peru hyväksyntä", "peru hyväksyntä vahinko undo kumoa 30 minuuttia"),
            ("poista-julkaisusta", "Poista julkaisusta", "poista julkaisusta take down piilota julkaise uudelleen vahvista poisto kiireellinen toinen ylläpitäjä"),
            ("komentokeskus", "Komentokeskus", "command center hallintanäkymä"),
            ("muokkaa", "Muokkaa ja palauta aiempi versio", "muokkaa historia palauta rollback muutoshistoria"),
            ("muokkauslinkki", "Luo muokkauslinkki järjestäjälle", "muokkauslinkki linkki järjestäjä ilman kirjautumista edit link"),
            ("moderointilinkit", "Moderointilinkit ja tokenit", "taikalinkki magic link token kertakäyttöinen"),
            ("yhdista", "Yhdistä päällekkäiset", "yhdistä merge kaksoiskappale"),
            ("kaannokset", "Käännökset", "käännös kääntäjä käännöstyöjono"),
            ("virheet", "Ilmoitusvirheet ja ehdotussivu", "ilmoitusvirheet ehdotukset virhe"),
        ],
    },
    "recurring": {
        "title": "Toistuvat mielenosoitukset",
        "icon": "fa-redo-alt",
        "group": "content",
        "access": "recurring",
        "endpoint": "admin_recu_demo.recu_demo_control",
        "summary": "Sarjat, tauot ja tulevien päivien massapäivitys.",
        "topics": [
            ("perusteet", "Toistuvat mielenosoitukset", "toistuva sarja viikoittain"),
            ("tauot", "Tauot", "tauko taukopäivät"),
            ("massapaivitys", "Päivitä tulevat päivät", "massapäivitys massaperuutus lapsi"),
        ],
    },
    "organizations": {
        "title": "Organisaatiot",
        "icon": "fa-building",
        "group": "content",
        "access": "organizations",
        "endpoint": "admin_org.organization_control",
        "summary": "Organisaatiot, jäsenet, kutsut ja muutospyynnöt.",
        "topics": [
            ("toiminnot", "Keskeiset toiminnot", "luo muokkaa poista organisaatio"),
            ("jasenet", "Jäsenet ja kutsut", "kutsu jäsen rooli"),
            ("historia", "Organisaation muutoshistoria", "historia muutoshistoria loki audit kuka muutti"),
            ("muutospyynnot", "Organisaation muutospyynnöt", "muutospyyntö ehdotus organisaatio"),
        ],
    },
    "users": {
        "title": "Käyttäjät",
        "icon": "fa-users",
        "group": "people",
        "access": "users",
        "endpoint": "admin_user.user_control",
        "summary": "Käyttäjien luonti, muokkaus, salasanan vaihto ja poisto.",
        "topics": [
            ("toiminnot", "Keskeiset toiminnot", "luo muokkaa poista käyttäjä kirjautumishistoria"),
            ("suojaukset", "Suojaukset", "roolihierarkia turvaluokitus"),
        ],
    },
    "users/add": {
        "title": "Lisää käyttäjä",
        "icon": "fa-user-plus",
        "group": "people",
        "parent": "users",
        "access": "users",
        "endpoint": "admin_user.user_control",
        "summary": "Uuden käyttäjän luonti vaihe vaiheelta.",
        "topics": [
            ("vaiheet", "Lisää käyttäjä vaihe vaiheelta", "luo uusi käyttäjä kaupunkiadmin rooli"),
        ],
    },
    "users/about_roles": {
        "title": "Roolit ja oikeudet",
        "icon": "fa-user-shield",
        "group": "people",
        "parent": "users",
        "summary": "Roolien vertailu ja roolin valitsin.",
        "topics": [
            ("roolit", "Käyttäjäroolit", "rooli user translator city_admin admin global_admin"),
            ("vertailu", "Roolien vertailutaulukko", "oikeudet vertailu taulukko"),
            ("valitsin", "Mikä rooli sopii?", "valitse rooli ehdotus"),
        ],
    },
    "account": {
        "title": "Oma tili ja kirjautuminen",
        "icon": "fa-key",
        "group": "people",
        "access": "account",
        "summary": "Kaksivaiheinen tunnistus, passkeyt, henkilöllisyyden vahvistus ja kirjautuminen.",
        "topics": [
            ("mfa", "Ota kaksivaiheinen tunnistus käyttöön", "mfa kaksivaiheinen tunnistus authenticator koodi qr"),
            ("passkey", "Lisää passkey", "passkey webauthn touch id face id avainnippu"),
            ("vahvistus", "Henkilöllisyyden vahvistus", "vahvista henkilöllisyys step-up salasana"),
            ("kirjautuminen", "Kirjautuminen ja paluu sivulle", "kirjaudu next paluu istunto vanhentui"),
        ],
    },
    "stats": {
        "title": "Tilastot ja analytiikka",
        "icon": "fa-chart-line",
        "group": "system",
        "access": "analytics",
        "endpoint": "admin.stats",
        "summary": "Tilastot ja palvelun analytiikka.",
        "topics": [
            ("tilastot", "Tilastot", "tilastot määrät"),
            ("analytiikka", "Palvelun analytiikka", "analytiikka kävijät katselut"),
        ],
    },
    "security": {
        "title": "Tietoturva",
        "icon": "fa-lock",
        "group": "system",
        "summary": "Roolimalli, turvaluokitus, linkkien suojaus ja lokit.",
        "topics": [
            ("roolit", "Roolit ja oikeudet", "rooli oikeus kaupunkiadmin"),
            ("turvaluokitus", "Hallituksen turvaluokitus", "turvaluokitus board clearance hallitus"),
            ("linkit", "Linkkien suojaus", "linkki allekirjoitus token"),
            ("lokit", "Loki ja kirjaus", "loki audit kirjaus"),
            ("hyvat-tavat", "Hyvät tavat", "turvallisuus tili jako"),
        ],
    },
    "support": {
        "title": "Ylläpito ja tuki",
        "icon": "fa-tools",
        "group": "system",
        "summary": "Lokit, taustatyöt, järjestelmän tila ja kehittäjähallinta.",
        "topics": [
            ("lokit", "Lokit ja tapahtumat", "loki tapahtumat suodata"),
            ("taustatyot", "Taustatyöt", "taustatyö ajastus job"),
            ("tila", "Järjestelmän tila", "status tila tietokanta"),
            ("kehittajat", "Kehittäjähallinta", "api avain kehittäjä"),
            ("muut", "Muut hallintaosiot", "kaupungit hallinto kampanja"),
        ],
    },
    "faq": {
        "title": "Usein kysytyt",
        "icon": "fa-circle-question",
        "group": "system",
        "summary": "Lyhyet vastaukset yleisiin kysymyksiin.",
        "topics": [
            ("toiminto-puuttuu", "Miksi en näe jotain toimintoa?", "puuttuu oikeus ei näy"),
            ("ei-ilmoitusta", "Miksi en saanut ilmoitusta tapauksesta?", "ilmoitus kello sähköposti"),
            ("ehdotukset-vs-tapaukset", "Missä muutosehdotukset ovat?", "ehdotukset tapaukset"),
            ("linkki-ei-toimi", "Linkki ei toimi", "linkki vanhentunut mitätöity"),
            ("superkayttaja", "Miten tulen superkäyttäjäksi?", "global_admin superkäyttäjä"),
        ],
    },
}

# Step-by-step walkthroughs. ``page``/``anchor`` locate the walkthrough; the
# index lists them as the most common tasks.
TASKS = [
    ("cases", "kasittele-uusi", "Käsittele uusi mielenosoitus", "cases"),
    ("cases", "kaksoiskappale", "Hylkää kaksoiskappale", "cases"),
    ("cases", "muutosehdotus", "Käsittele muutosehdotus", "cases"),
    ("cases", "peruutus", "Hyväksy peruutuspyyntö", "cases"),
    ("cases", "vastaa", "Vastaa ilmoittajalle", "cases"),
    ("demos", "muokkauslinkki", "Anna järjestäjälle muokkauslinkki", "demos"),
    ("demos", "muokkaa", "Palauta aiempi versio", "demos"),
    ("users/add", "vaiheet", "Lisää käyttäjä", "users"),
    ("account", "mfa", "Ota kaksivaiheinen tunnistus käyttöön", "account"),
    ("account", "passkey", "Lisää passkey", "account"),
]

# Endpoint (exact) or blueprint prefix ("admin_case.") -> (page, anchor).
HELP_LINKS = {
    "admin.admin_dashboard": ("dashboard", None),
    "admin_case.cases": ("cases", "lista"),
    "admin_case.blocklist": ("cases", "estolista"),
    "admin_case.": ("cases", None),
    "admin_demo.demo_control": ("demos", "lista"),
    "admin_demo.demo_command_center": ("demos", "komentokeskus"),
    "admin_demo.demo_edit_history": ("demos", "muokkaa"),
    "admin_demo.view_demo_diff": ("demos", "muokkaa"),
    "admin_demo.manage_magic_tokens": ("demos", "moderointilinkit"),
    "admin_demo.merge_demos": ("demos", "yhdista"),
    "admin_demo.demo_translation_dashboard": ("demos", "kaannokset"),
    "admin_demo.edit_demo_translations": ("demos", "kaannokset"),
    "admin_demo.suggestions_list": ("cases", "muutosehdotus"),
    "admin_demo.suggestion_view": ("cases", "muutosehdotus"),
    "admin_demo.submission_errors_dashboard": ("demos", "virheet"),
    "admin_demo.": ("demos", None),
    "admin_recu_demo.": ("recurring", None),
    "admin_org.": ("organizations", None),
    "admin_user.user_control": ("users", "toiminnot"),
    "admin_user.": ("users", None),
    "admin_governance.": ("security", "turvaluokitus"),
    "admin_city.": ("support", "muut"),
    "admin.stats": ("stats", "tilastot"),
    "admin_site_analytics.": ("stats", "analytiikka"),
    "admin.background_jobs": ("support", "taustatyot"),
    "admin.background_job_detail": ("support", "taustatyot"),
    "admin.logs": ("support", "lokit"),
    "admin.admin_status": ("support", "tila"),
    "admin_dev.": ("support", "kehittajat"),
    "admin_kampanja.": ("support", "muut"),
}


def help_target(endpoint):
    """Return ``(page, anchor)`` of the manual section for an admin endpoint."""
    if not endpoint:
        return None
    if endpoint in HELP_LINKS:
        return HELP_LINKS[endpoint]
    blueprint = endpoint.split(".", 1)[0] + "."
    return HELP_LINKS.get(blueprint)


def page_url(slug, anchor=None):
    url = url_for("admin.manual") if slug == "index" else url_for("admin.manual_page", page=slug)
    return f"{url}#{anchor}" if anchor else url


def search_index():
    """Flat list of pages, topics and tasks for the manual's search box."""
    entries = []
    for slug, page in PAGES.items():
        entries.append({
            "title": page["title"],
            "where": "Sivu",
            "text": page["summary"],
            "url": page_url(slug),
        })
        for anchor, title, keywords in page.get("topics", ()):
            entries.append({
                "title": title,
                "where": page["title"],
                "text": keywords,
                "url": page_url(slug, anchor),
            })
    return entries


def tasks_for(user):
    """The common tasks, the ones the user can do first."""
    rows = [
        {"title": title, "url": page_url(page, anchor),
         "page": PAGES[PAGES[page].get("parent") or page]["title"],
         "icon": PAGES[page]["icon"], "allowed": can_open(user, access)}
        for page, anchor, title, access in TASKS
    ]
    return sorted(rows, key=lambda row: not row["allowed"])


def nav_groups():
    groups = []
    for key, label in GROUPS:
        items = [
            {"slug": slug, **page}
            for slug, page in PAGES.items()
            if page["group"] == key
        ]
        groups.append({"key": key, "label": label, "items": items})
    return groups


def neighbours(slug):
    slugs = list(PAGES)
    i = slugs.index(slug)
    prev_slug = slugs[i - 1] if i > 0 else None
    next_slug = slugs[i + 1] if i + 1 < len(slugs) else None
    return (
        {"slug": prev_slug, "title": PAGES[prev_slug]["title"]} if prev_slug else None,
        {"slug": next_slug, "title": PAGES[next_slug]["title"]} if next_slug else None,
    )
