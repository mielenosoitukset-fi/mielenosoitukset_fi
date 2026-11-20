// Registration page, GET /users/auth/register. Only the page moves to React: the POST, the
// validation that decides, the account insert, the confirmation mail and the redirect to the
// next-steps page stay in the Flask handler. The checks here only guide the visitor.
export const registerPath = '/users/auth/register';
export const usernameFreeApi = '/users/auth/api/username_free';

export const registerCopy = {
  title: 'Luo uusi tili',
  lead: 'Tilillä voit hallita ilmoituksiasi, seurata niiden käsittelyä ja osallistua organisaatioiden toimintaan.',
  detailsTitle: 'Tilin tiedot',
  detailsLead: 'Käyttäjätunnus näkyy palvelussa. Sähköpostia käytetään tilin vahvistamiseen ja tärkeisiin viesteihin.',
  username: 'Käyttäjätunnus',
  usernamePlaceholder: 'Valitse käyttäjätunnus',
  usernameHint: '3–30 merkkiä: pieniä kirjaimia, numeroita, alaviivoja ja yhdysmerkkejä. Ei kahta alaviivaa tai yhdysmerkkiä peräkkäin, ei alussa eikä lopussa.',
  email: 'Sähköposti',
  emailPlaceholder: 'nimi@esimerkki.fi',
  emailHint: 'Lähetämme vahvistuslinkin tähän osoitteeseen.',
  passwordTitle: 'Suojaa tilisi',
  passwordLead: 'Käytä vähintään 12 merkkiä ja kolmea eri merkkityyppiä.',
  password: 'Salasana',
  passwordPlaceholder: 'Luo vahva salasana',
  confirm: 'Vahvista salasana',
  confirmPlaceholder: 'Syötä salasana uudelleen',
  showPassword: 'Näytä salasana',
  generate: 'Luo vahva salasana',
  generated: 'Salasana luotu ja näytetään kentässä. Tallenna se salasanojen hallintaan.',
  requirements: 'Salasanan vaatimukset',
  reqLength: 'Vähintään 12 merkkiä pitkä',
  reqCategories: 'Vähintään 3 seuraavista: pieni kirjain, iso kirjain, numero, erikoismerkki',
  reqIdentity: 'Ei sisällä käyttäjätunnusta tai sähköpostiosoitetta',
  met: 'täytetty',
  notMet: 'ei täytetty',
  mismatch: 'Salasanat eivät täsmää',
  match: 'Salasanat täsmäävät',
  checking: 'Tarkistetaan saatavuutta…',
  available: 'Käyttäjätunnus on vapaa',
  taken: 'Käyttäjätunnus on varattu',
  invalidUsername: 'Käyttäjätunnus ei kelpaa',
  checkFailed: 'Saatavuutta ei voitu tarkistaa. Voit silti jatkaa.',
  fixErrors: 'Korjaa merkityt kohdat ennen jatkamista.',
  submit: 'Luo tili',
  haveAccount: 'Onko sinulla jo tili?',
  login: 'Kirjaudu sisään',
  terms: 'Luomalla tilin hyväksyt palvelun',
  termsLink: 'käyttöehdot',
  privacyLead: 'Lue myös',
  privacyLink: 'tietosuojaseloste',
};

/** Same normalisation as Flask's normalize_username. */
export function normalizeUsername(value: string): string {
  return value.trim().toLowerCase();
}

/** Same shape rules as Flask's validate_username, except the reserved-name list (Flask decides). */
export function usernameShapeOk(value: string): boolean {
  const name = normalizeUsername(value);
  return name.length >= 3 && name.length <= 30 && /^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/.test(name)
    && !/[_-]{2}/.test(name);
}

// The special characters Flask's is_strong_password counts.
const SPECIAL = /[!@#$%^&*()\-_+=[\]{};:,.<>?]/;

export type PasswordChecks = { length: boolean; categories: boolean; identity: boolean; ok: boolean };

/** Mirrors Flask's is_strong_password: 12+ characters, 3 of 4 categories, no username or email name. */
export function passwordChecks(password: string, username: string, email: string): PasswordChecks {
  const length = password.length >= 12;
  const categories = [/[a-z]/, /[A-Z]/, /\p{Nd}/u, SPECIAL].filter(pattern => pattern.test(password)).length >= 3;
  const lower = password.toLowerCase();
  const name = username.trim().toLowerCase();
  const local = email.trim().toLowerCase().split('@', 1)[0];
  const identity = !(name && lower.includes(name)) && !(local && lower.includes(local));
  return { length, categories, identity, ok: length && categories && identity };
}

export type UsernameState = 'idle' | 'checking' | 'available' | 'taken' | 'invalid' | 'failed';

/** Reads `/users/auth/api/username_free`: 200 {available} or 400 {available:false, error}. */
export function readUsernameFree(status: number, body: unknown): UsernameState {
  const available = body && typeof body === 'object' ? (body as { available?: unknown }).available : undefined;
  if (status === 400) return 'invalid';
  if (status !== 200 || typeof available !== 'boolean') return 'failed';
  return available ? 'available' : 'taken';
}

const ALPHABET = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%&*-_=+?';

/** 20 characters from the browser's CSPRNG, retried until it passes every check. */
export function generatePassword(fill: (bytes: Uint8Array<ArrayBuffer>) => unknown, username = '', email = ''): string {
  for (;;) {
    const bytes = new Uint8Array(40);
    fill(bytes);
    let out = '';
    // Rejection sampling keeps every character equally likely.
    for (const byte of bytes) {
      if (byte < 256 - (256 % ALPHABET.length)) out += ALPHABET[byte % ALPHABET.length];
      if (out.length === 20) break;
    }
    if (out.length === 20 && passwordChecks(out, username, email).ok && /[a-z]/.test(out) && /[A-Z]/.test(out)
      && /\d/.test(out) && SPECIAL.test(out)) return out;
  }
}
