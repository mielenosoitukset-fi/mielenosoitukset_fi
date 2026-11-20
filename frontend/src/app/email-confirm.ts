// E-mail address confirmation page, /users/auth/confirm_email/<token>. Opening the link
// only shows a button; the POST to Go does the write, so a mail scanner or link
// preview cannot confirm an account. The copy is Flask's flash messages for this route.
export const emailConfirmPath = '/api/account/confirm-email';
/** A signed JWT: three base64url parts, at most a little over 2 KB. */
export const emailConfirmTokenPattern = /^[A-Za-z0-9_-]{1,1000}\.[A-Za-z0-9_-]{1,1000}\.[A-Za-z0-9_-]{1,1000}$/;
/** Route-level shape: the dots are allowed, the parts are checked by Go. */
export const emailConfirmRoutePattern = /^[A-Za-z0-9_.-]{5,3100}$/;

export const emailConfirmCopy = {
  title: 'Vahvista sähköpostiosoite',
  intro: 'Vahvista sähköpostiosoitteesi painamalla painiketta. Sen jälkeen voit kirjautua sisään.',
  button: 'Vahvista sähköpostiosoite',
  working: 'Vahvistetaan…',
  confirmedTitle: 'Sähköpostiosoite on vahvistettu',
  confirmed: 'Sähköpostiosoitteesi on vahvistettu. Voit nyt kirjautua sisään.',
  login: 'Kirjaudu sisään',
  invalidTitle: 'Vahvistuslinkki ei kelpaa',
  invalid: 'Vahvistuslinkki on virheellinen tai vanhentunut.',
  invalidHelp: 'Voit pyytää uuden vahvistusviestin kirjautumissivulta.',
  notFoundTitle: 'Käyttäjää ei löytynyt',
  notFound: 'Käyttäjää ei löytynyt.',
  error: 'Vahvistus ei onnistunut. Yritä uudelleen hetken kuluttua.',
  rateLimited: 'Yrityksiä on liian monta. Odota hetki ja yritä uudelleen.',
  noscript: 'Vahvistus tarvitsee JavaScriptin. Ota käyttöön JavaScript tai kirjoita osoitteeseen tuki@mielenosoitukset.fi.',
} as const;

export type EmailConfirmOutcome = 'confirmed' | 'invalid' | 'user-not-found' | 'error' | 'rate-limited';

type Fetcher = (input: string, init: RequestInit) => Promise<Pick<Response, 'ok' | 'status' | 'json'>>;

/**
 * POSTs the token. Only Go's three verdicts end the page; a network failure, a 5xx,
 * a malformed body or a 429 is not a verdict on the link, so the button stays.
 */
export async function confirmEmail(token: string, fetcher: Fetcher = (input, init) => fetch(input, init)): Promise<EmailConfirmOutcome> {
  if (!emailConfirmTokenPattern.test(token)) return 'invalid';
  try {
    const response = await fetcher(emailConfirmPath, {
      method: 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (response.status === 429) return 'rate-limited';
    const body = await response.json().catch(() => null) as { status?: unknown } | null;
    if (response.ok && body?.status === 'confirmed') return 'confirmed';
    if (response.status === 400 && body?.status === 'invalid') return 'invalid';
    if (response.status === 404 && body?.status === 'user-not-found') return 'user-not-found';
    return 'error';
  } catch {
    return 'error';
  }
}
