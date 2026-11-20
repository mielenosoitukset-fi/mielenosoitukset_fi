// Password reset page, GET /users/auth/password_reset/<token>. Only the page moves to React.
// The link check (spent, signature, expiry, account, issued before the last change) and the
// visit log run in Flask through POST /users/auth/api/password_reset/status, and the form
// posts to the unchanged Flask handler, which checks everything again.
export const passwordResetTokenPattern = /^\/users\/auth\/password_reset\/([A-Za-z0-9_.-]{1,2048})$/;
export const passwordResetStatusApi = '/users/auth/api/password_reset/status';

export const passwordResetCopy = {
  title: 'Vaihda salasana',
  lead: 'Luo vahva ja turvallinen uusi salasana tilillesi.',
  checking: 'Tarkistetaan palautuslinkkiä…',
  checkFailed: 'Linkkiä ei voitu tarkistaa. Voit silti yrittää vaihtaa salasanan.',
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
  remembered: 'Muistitko salasanasi?',
  backToLogin: 'Takaisin kirjautumiseen',
  requestPath: '/users/auth/password_reset_request',
};

/** The token from the path, or null. Never decoded: the path segment is sent back as is. */
export function resetTokenFromPath(path: string): string | null {
  return passwordResetTokenPattern.exec(path)?.[1] ?? null;
}

export type LinkState = 'checking' | 'valid' | 'refused' | 'unknown';

/** Reads the status answer: {valid: boolean}. Anything else leaves the form usable. */
export function readResetStatus(status: number, body: unknown): LinkState {
  const valid = body && typeof body === 'object' ? (body as { valid?: unknown }).valid : undefined;
  if (status === 200 && valid === true) return 'valid';
  if (status === 200 && valid === false) return 'refused';
  return 'unknown';
}
