// Steps an administrator can require before an account is used again:
// GET /users/auth/forced_pwd_reset/ and GET /users/auth/forced_identity_change/.
// Only the pages move to React. Flask keeps every check, the change itself, the log rows,
// the confirmation mail and the redirect to the next step; it also keeps a flagged account
// on these pages until the steps are done.
export const forcedPasswordPath = '/users/auth/forced_pwd_reset/';
export const forcedIdentityPath = '/users/auth/forced_identity_change/';

export const forcedPasswordCopy = {
  title: 'Vaihda salasana',
  lead: 'Ylläpito edellyttää uutta salasanaa ennen kuin voit jatkaa palvelun käyttöä.',
  password: 'Uusi salasana',
  passwordPlaceholder: 'Syötä vahva salasana',
  confirm: 'Vahvista salasana',
  confirmPlaceholder: 'Syötä salasana uudelleen',
  showPassword: 'Näytä salasana',
  generate: 'Luo vahva salasana',
  generated: 'Salasana luotu ja näytetään kentässä. Tallenna se salasanojen hallintaan.',
  requirements: 'Salasanan vaatimukset',
  reqLength: 'Vähintään 12 merkkiä pitkä',
  reqCategories: 'Vähintään 3 seuraavista: pieni kirjain, iso kirjain, numero, erikoismerkki',
  reqIdentity: 'Ei saa sisältää käyttäjätunnusta tai sähköpostiosoitetta (tarkistetaan lähetettäessä)',
  met: 'täytetty',
  notMet: 'ei täytetty',
  mismatch: 'Salasanat eivät täsmää',
  match: 'Salasanat täsmäävät',
  fixErrors: 'Korjaa merkityt kohdat ennen jatkamista.',
  submit: 'Vaihda salasana',
};

export const forcedIdentityCopy = {
  title: 'Valitse uudet nimet',
  lead: 'Ylläpito edellyttää uuden käyttäjätunnuksen ja näyttönimen valitsemista ennen kuin voit jatkaa palvelun käyttöä.',
  reserved: 'Admin ja @Admin ovat varattuja palvelun ylläpidolle.',
  username: 'Uusi käyttäjätunnus',
  usernameHint: '3–30 merkkiä: pieniä kirjaimia, numeroita, alaviivoja ja yhdysmerkkejä. Ei kahta alaviivaa tai yhdysmerkkiä peräkkäin, ei alussa eikä lopussa.',
  displayname: 'Uusi näyttönimi',
  displaynameHint: '2–80 merkkiä. Näkyy muille palvelussa.',
  checking: 'Tarkistetaan saatavuutta…',
  available: 'Käyttäjätunnus on vapaa',
  taken: 'Käyttäjätunnus on varattu',
  invalidUsername: 'Käyttäjätunnus ei kelpaa',
  checkFailed: 'Saatavuutta ei voitu tarkistaa. Voit silti jatkaa.',
  displaynameLength: 'Näyttönimen tulee olla 2–80 merkkiä pitkä.',
  fixErrors: 'Korjaa merkityt kohdat ennen jatkamista.',
  submit: 'Tallenna uudet nimet',
};

export const forcedCommonCopy = {
  help: 'Tarvitsetko apua? Ota yhteyttä:',
  helpEmail: 'tuki@mielenosoitukset.fi',
  logoutLead: 'Etkö halua jatkaa nyt?',
  logout: 'Kirjaudu ulos',
};

export type ForcedSteps = { password: boolean; identity: boolean } | undefined;

/**
 * Where a signed-in visitor goes instead of this page, or null to show it. Same order as
 * Flask: the password first, then the names; with nothing required, the settings page.
 * Unknown flags (an older session answer) show the page and leave the decision to Flask.
 */
export function forcedRedirect(page: 'password' | 'identity', forced: ForcedSteps, settingsUrl: string): string | null {
  if (!forced) return null;
  if (page === 'password') {
    if (forced.password) return null;
    return forced.identity ? forcedIdentityPath : settingsUrl;
  }
  if (forced.password) return forcedPasswordPath;
  return forced.identity ? null : settingsUrl;
}

/** Same bounds as Flask's display-name check, after trimming. */
export function displaynameOk(value: string): boolean {
  const name = value.trim();
  return name.length >= 2 && name.length <= 80;
}
