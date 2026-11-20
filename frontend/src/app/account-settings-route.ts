// Account settings page, GET /users/auth/settings. The data comes from the Go read route
// behind the auth bridge (go-api/account_settings.go); the copy is the Flask template's
// Finnish msgids. Saving the general settings and the profile keeps posting to the
// unchanged Flask JSON endpoints. The security tab is read-only in this step.
export const accountSettingsPath = '/users/auth/settings';
export const accountSettingsApi = '/api/account/settings';
export const accountGeneralWriteApi = '/users/auth/api/v2/settings';
export const accountProfileWriteApi = '/users/auth/api/v2/user_profile';
/** Go bio write (docs/account-pages-react.md, step 3a); used only when the read answer carries a CSRF token. */
export const accountBioWriteApi = '/api/account/profile';
export const accountBioMax = 5000;
/** Announce the remaining length to screen readers once the text is this close to the limit. */
export const accountBioWarnAt = 200;
export const accountDeveloperPath = '/developer';
export const accountTranslationsUrl = 'https://mielenosoitukset.fi/upcoming/translations/';

export const accountTabs = [
  { id: 'general', panel: 'tab-general', label: 'Yleinen' },
  { id: 'security', panel: 'tab-security', label: 'Turvallisuus' },
  { id: 'tokens', panel: 'tab-tokens', label: 'API-avaimet' },
  { id: 'profile', panel: 'tab-profile', label: 'Profiili' },
] as const;
export type AccountTabId = typeof accountTabs[number]['id'];

/** The tab a `#tab-…` hash names; anything else is the first tab. */
export function tabFromHash(hash: string): AccountTabId {
  const wanted = hash.replace(/^#/, '');
  return accountTabs.find(tab => tab.panel === wanted)?.id ?? 'general';
}

/** Arrow, Home and End keys move between tabs; `null` means the key is not ours. */
export function nextTabIndex(key: string, index: number, count: number): number | null {
  if (key === 'ArrowRight') return (index + 1) % count;
  if (key === 'ArrowLeft') return (index - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
}

export const accountCopy = {
  title: 'Asetukset',
  lead: 'Hallinnoi tilisi tietoja, turvallisuutta, profiilia ja API-avaimia yhdessä paikassa.',
  tabsLabel: 'Asetusosiot',
  loading: 'Ladataan asetuksia…',
  loginLead: 'Kirjaudu sisään nähdäksesi asetukset.',
  login: 'Kirjaudu sisään',
  unavailable: 'Asetuksia ei saatu ladattua. Yritä hetken kuluttua uudelleen.',
  retry: 'Yritä uudelleen',
  noscript: 'Tämä sivu toimii parhaiten JavaScriptin kanssa.',
  generalTitle: 'Yleiset asetukset',
  generalLead: 'Valitse profiilissa näkyvä nimi, kotikaupunki ja käyttöliittymän asetukset.',
  displayName: 'Näyttönimi',
  displayNamePlaceholder: 'Kirjoita haluamasi nimi, joka näkyy profiilissasi',
  displayNameHelp: 'Näyttönimi näkyy muille käyttäjille profiilissasi ja kommenteissa. Voit käyttää omaa nimeäsi tai lempinimeä.',
  city: 'Kaupunki',
  cityOpen: 'Avaa paikkakuntavalikko',
  cityChoose: 'Valitse paikkakunta',
  citySearchLabel: 'Hae kaupungeista',
  citySearch: 'Hae paikkakunta…',
  language: 'Kieli',
  finnish: 'Suomi',
  translationsTitle: 'Käännökset ovat vielä kesken',
  translationsBody: 'Sivusto on tällä hetkellä käytettävissä vain suomeksi, mutta englannin ja ruotsin käännökset ovat parhaillaan työn alla.',
  translationsFollow: 'Voit seurata edistymistä ja halutessasi osallistua käännöstyöhön sivulla:',
  translationsLink: 'Katso käännöshanke',
  darkMode: 'Tumma tila',
  save: 'Tallenna muutokset',
  saving: 'Tallennetaan…',
  saveFailed: 'Virhe asetusten tallennuksessa.',
  securityTitle: 'Turvallisuusasetukset',
  securityLead: 'Hallinnoi tilisi turvallisuusasetuksia, kuten kaksivaiheista tunnistautumista (MFA).',
  securityLater: 'Salasanaa, kaksivaiheista tunnistusta ja passkeyjä ei voi vielä muuttaa tällä sivulla. Ne lisätään tänne myöhemmin.',
  mfaTitle: 'Kaksivaiheinen tunnistus (MFA)',
  mfaLead: 'Hallinnoi kaksivaiheista tunnistautumista ja MFA-laitteita.',
  mfaOn: 'MFA on käytössä',
  mfaOff: 'MFA ei ole käytössä',
  mfaDevice: 'MFA-laite',
  passkeysTitle: 'Passkeyt',
  passkeysLead: 'Kirjaudu ilman salasanaa puhelimen, tietokoneen tai turva-avaimen tunnistuksella.',
  noPasskeys: 'Ei passkeyjä vielä.',
  passkeyDevice: 'Todennuslaite',
  lastUsed: 'Viimeksi käytetty',
  historyTitle: 'Kirjautumishistoria',
  time: 'Aika',
  ip: 'IP-osoite',
  browser: 'Selain',
  loginOk: 'Onnistui',
  loginFailed: 'Epäonnistui',
  result: 'Tulos',
  noLogins: 'Ei kirjautumisia',
  tokensTitle: 'API-avaimet',
  tokensLead: 'Luo ja hallitse API-avaimia integraatioita varten. Avaimet näytetään vain kerran. Tallenna ne turvallisesti.',
  tokensPanel: 'Kehittäjäpaneeli (sovellukset)',
  profileTitle: 'Profiili',
  profileLead: 'Päivitä julkisessa profiilissasi näkyvä esittelyteksti ja kuva.',
  bio: 'Esittelyteksti',
  bioHelp: 'Kerro lyhyesti itsestäsi. Teksti näkyy julkisella profiilisivullasi.',
  picture: 'Profiilikuva',
  optional: 'Valinnainen',
  pictureHelp: 'Jätä kenttä tyhjäksi, jos haluat säilyttää nykyisen profiilikuvasi.',
  preview: 'Profiilikuvan esikatselu',
  saveProfile: 'Tallenna profiili',
  profileSaved: 'Profiili päivitetty.',
  profileFailed: 'Profiilin tallennus epäonnistui',
  imageFailed: 'Kuvan lukeminen epäonnistui',
  rateLimited: 'Liian monta pyyntöä. Yritä hetken kuluttua uudelleen.',
  sessionExpired: 'Istunto vanhentui.',
  reloadPage: 'Lataa sivu uudelleen',
  charsLeft: 'merkkiä jäljellä',
} as const;

export type AccountGeneral = { displayName: string; city: string | null; language: string | null; darkMode: boolean | null };
export type AccountMfaDevice = { id: string; name: string; createdAt: string };
export type AccountPasskey = { id: string; name: string; createdAt: string; lastUsedAt: string; transports: string[] };
export type AccountLogin = { time: string; ip: string; ua: string; ok: boolean; reason: string };

export type AccountSettingsRead = {
  general: AccountGeneral;
  bio: string | null;
  picture: string | null;
  mfa: { enabled: boolean; devices: AccountMfaDevice[] };
  passkeys: AccountPasskey[];
  logins: AccountLogin[];
  cities: string[];
  /** Set only when Go has the write routes on; without it the page keeps posting to Flask. */
  csrf?: string;
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, max: number) => typeof value === 'string' ? value.slice(0, max) : '';
const nullableText = (value: unknown, max: number): string | null | undefined =>
  value === null ? null : typeof value === 'string' ? value.slice(0, max) : undefined;

/** Pictures come from the database; only web addresses and site paths become an image source. */
export function safePicture(value: unknown): string | null {
  return typeof value === 'string' && value.length <= 2000 && /^(https?:\/\/|\/(?!\/))/i.test(value) ? value : null;
}

function readList<T>(value: unknown, read: (item: unknown) => T | null, max: number): T[] | null {
  if (!Array.isArray(value)) return null;
  return value.slice(0, max).flatMap(item => read(item) ?? []);
}

function readDevice(value: unknown): AccountMfaDevice | null {
  if (!isObject(value) || typeof value.id !== 'string' || !/^[a-f\d]{24}$/i.test(value.id)) return null;
  return { id: value.id.toLowerCase(), name: text(value.name, 100), createdAt: text(value.created_at, 40) };
}

function readPasskey(value: unknown): AccountPasskey | null {
  if (!isObject(value) || typeof value.id !== 'string' || !/^[a-f\d]{24}$/i.test(value.id)) return null;
  const transports = Array.isArray(value.transports)
    ? value.transports.filter((item): item is string => typeof item === 'string').slice(0, 10).map(item => item.slice(0, 20)) : [];
  return { id: value.id.toLowerCase(), name: text(value.name, 100), createdAt: text(value.created_at, 40),
    lastUsedAt: text(value.last_used_at, 40), transports };
}

function readLogin(value: unknown): AccountLogin | null {
  if (!isObject(value) || typeof value.ok !== 'boolean') return null;
  return { time: text(value.time, 40), ip: text(value.ip, 64), ua: text(value.ua, 120), ok: value.ok, reason: text(value.reason, 200) };
}

/**
 * Turn a Go answer into page data. Anything that does not match the contract is `null`
 * (unavailable), never a half-empty settings page.
 */
export function readAccountSettings(body: unknown): AccountSettingsRead | null {
  if (!isObject(body) || !isObject(body.general) || !isObject(body.profile) || !isObject(body.mfa)) return null;
  const { general, profile, mfa } = body;
  if (typeof general.display_name !== 'string') return null;
  const city = nullableText(general.city, 100);
  const language = nullableText(general.language, 10);
  if (city === undefined || language === undefined) return null;
  if (general.dark_mode !== null && typeof general.dark_mode !== 'boolean') return null;
  const bio = nullableText(profile.bio, 5000);
  if (bio === undefined) return null;
  if (profile.profile_picture !== null && typeof profile.profile_picture !== 'string') return null;
  if (typeof mfa.enabled !== 'boolean') return null;
  const devices = readList(mfa.devices, readDevice, 50);
  const passkeys = readList(body.passkeys, readPasskey, 50);
  const logins = readList(body.login_history, readLogin, 30);
  if (!devices || !passkeys || !logins || !Array.isArray(body.cities)) return null;
  const cities = body.cities.filter((item): item is string => typeof item === 'string' && item.length > 0 && item.length <= 100).slice(0, 1000);
  return {
    general: { displayName: general.display_name.slice(0, 100), city, language, darkMode: general.dark_mode as boolean | null },
    bio, picture: safePicture(profile.profile_picture), mfa: { enabled: mfa.enabled, devices }, passkeys, logins, cities,
    csrf: body.writes === true && typeof body.csrf === 'string' && /^[\w.-]{20,300}$/.test(body.csrf) ? body.csrf : undefined,
  };
}

export type AccountResponseKind = 'ok' | 'signed-out' | 'unavailable';

/** Only a real 401 means signed out; a Flask 404 page (route not proxied to Go) or any 5xx is unavailable. */
export function classifyAccountResponse(status: number): AccountResponseKind {
  if (status === 401) return 'signed-out';
  if (status >= 200 && status < 300) return 'ok';
  return 'unavailable';
}

export type AccountStanding = 'signed-in' | 'signed-out' | 'unknown';

/** Same rule as the developer panel: a failed read never turns anyone into a guest. */
export function accountStanding(initial: { authenticated: boolean; unverified?: boolean },
  bridge: 'initial' | 'loading' | 'ready' | 'unavailable', fresh: { authenticated: boolean }): AccountStanding {
  if (bridge === 'ready') return fresh.authenticated ? 'signed-in' : 'signed-out';
  if (initial.unverified) return 'unknown';
  return initial.authenticated ? 'signed-in' : 'signed-out';
}

export type FlaskResult = { ok: true; message: string; body: Record<string, unknown> } | { ok: false; message: string };

/**
 * One JSON post to an unchanged Flask endpoint, as the Flask page does it: same-origin cookie,
 * JSON body. Success is `res.ok` and `status: "success"`. On failure Flask's own message is
 * shown when it sent one, otherwise `fallback`.
 */
export async function postFlaskJson(url: string, payload: unknown, fallback: string, fetcher: typeof fetch = fetch): Promise<FlaskResult> {
  try {
    const response = await fetcher(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload) });
    let body: unknown = null;
    try { body = await response.json(); } catch { /* HTML or empty answer: a failure below */ }
    const raw = isObject(body) ? body : {};
    const message = typeof raw.message === 'string' ? raw.message.slice(0, 300) : '';
    if (response.ok && raw.status === 'success') return { ok: true, message, body: raw };
    return { ok: false, message: message || fallback };
  } catch {
    return { ok: false, message: fallback };
  }
}

/** The JSON the Flask settings page posts. A city is sent only when one is chosen. */
export function generalPayload(values: { displayName: string; city: string; language: string; darkMode: boolean }) {
  return { display_name: values.displayName, ...(values.city ? { city: values.city } : {}), language: values.language, dark_mode: values.darkMode };
}

/** Finnish message for a failed Go write; Go's own text is shown only for 4xx input errors. */
export function bioWriteFailure(status: number, body: unknown, fallback: string): string {
  const raw = isObject(body) ? body : {};
  const serverText = typeof raw.error === 'string' ? raw.error.slice(0, 200) : '';
  if (status === 429) return accountCopy.rateLimited;
  if (status === 401 || (status === 403 && serverText === 'invalid csrf token')) return accountCopy.sessionExpired;
  if (status >= 400 && status < 500 && serverText && serverText !== 'forbidden') return serverText;
  return fallback;
}

/** POST JSON to a Go account write: same-origin cookie, CSRF header, JSON body. */
async function postToGo(url: string, csrf: string, payload: unknown, fallback: string, fetcher: typeof fetch): Promise<FlaskResult> {
  try {
    const response = await fetcher(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(payload) });
    let body: unknown = null;
    try { body = await response.json(); } catch { /* an empty or non-JSON body is a failure below */ }
    if (response.ok && isObject(body) && body.status === 'success') {
      return { ok: true, message: typeof body.message === 'string' ? body.message.slice(0, 300) : '', body };
    }
    return { ok: false, message: bioWriteFailure(response.status, body, fallback) };
  } catch {
    return { ok: false, message: fallback };
  }
}

/** Save only the bio through Go. */
export function postBioToGo(csrf: string, bio: string, fallback: string, fetcher: typeof fetch = fetch): Promise<FlaskResult> {
  return postToGo(accountBioWriteApi, csrf, { bio }, fallback, fetcher);
}

/** Save the general settings through Go (docs/account-pages-react.md, step 3b). */
export function postGeneralToGo(csrf: string, payload: ReturnType<typeof generalPayload>, fallback: string, fetcher: typeof fetch = fetch): Promise<FlaskResult> {
  return postToGo(accountSettingsApi, csrf, payload, fallback, fetcher);
}

export const profilePayload = (bio: string, base64Image: string | null) => ({ bio, profile_picture: base64Image });

/** Lower-cased, accent-aware match for the city search. */
export function filterCities(cities: readonly string[], query: string): string[] {
  const wanted = query.trim().toLocaleLowerCase('fi');
  return wanted ? cities.filter(city => city.toLocaleLowerCase('fi').includes(wanted)) : [...cities];
}
