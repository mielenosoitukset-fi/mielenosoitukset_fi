// Video guides. They are recorded on practice data only (no real people).
// To publish a new guide: upload the mp4 to cdn2 under guides/, copy its
// .fi.vtt to mielenosoitukset_fi/static/guides/ and add one line here.
export const guideVideoBase = 'https://cdn2.mielenosoitukset.fi/guides';
export const guideCaptionBase = '/static/guides';

export interface GuideVideo { id: string; title: string; minutes: number; summary: string }
export interface GuideVideoGroup { key: string; title: string; intro: string; videos: GuideVideo[] }

export const guideVideoGroups: GuideVideoGroup[] = [
  {
    key: 'account', title: 'Tili ja kirjautuminen', intro: 'Aloita tästä, jos et ole vielä kirjautunut palveluun.',
    videos: [
      { id: 'a3-tilin-luominen', title: 'Luo käyttäjätili', minutes: 3, summary: 'Rekisteröityminen ja sähköpostin vahvistus.' },
      { id: 'a4-salasanan-palautus', title: 'Palauta salasana', minutes: 3, summary: 'Uusi salasana, kun vanha on unohtunut.' },
    ],
  },
  {
    key: 'everyone', title: 'Kaikille', intro: 'Ei vaadi tiliä tai erityisiä oikeuksia.',
    videos: [
      { id: 'a1-ilmoita-mielenosoitus', title: 'Ilmoita mielenosoitus', minutes: 4, summary: 'Ilmoituslomake vaihe vaiheelta. Tiliä ei tarvita.' },
      { id: 'a2-muokkaa-tai-peruuta', title: 'Muokkaa tai peru ilmoitus', minutes: 3, summary: 'Järjestäjän linkki sähköpostissa: muokkaus ja peruutus.' },
      { id: 'a5-ehdota-muutosta', title: 'Ehdota muutosta mielenosoitukseen', minutes: 3, summary: 'Korjaus julkaistun mielenosoituksen tietoihin.' },
    ],
  },
  {
    key: 'organizations', title: 'Organisaatioille', intro: 'Järjestön tilin hakeminen ja kutsun vastaanotto.',
    videos: [
      { id: 'd1-organisaatiotili', title: 'Hae organisaatiotiliä', minutes: 4, summary: 'Organisaation tilin hakeminen.' },
      { id: 'd3-ota-kutsu-vastaan', title: 'Ota kutsu vastaan', minutes: 3, summary: 'Organisaation kutsun hyväksyminen.' },
      { id: 'd5-paasy-hukassa', title: 'Pääsy organisaation tiliin hukassa', minutes: 3, summary: 'Pyyntö uudesta hallinnoijasta, kun kukaan ei pääse tiliin.' },
    ],
  },
  {
    key: 'city-admins', title: 'Kaupunkiadmineille', intro: 'Hakemus kaupungin ilmoitusten käsittelijäksi.',
    videos: [
      { id: 'c1-kaupunkiadmin-hakemus', title: 'Hae kaupunkiadminiksi', minutes: 3, summary: 'Hakemuksen tekeminen ja seuraaminen.' },
    ],
  },
];
