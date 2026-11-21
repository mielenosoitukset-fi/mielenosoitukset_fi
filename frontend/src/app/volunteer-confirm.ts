// Campaign volunteer confirmation page, /kampanja/confirm/<token>. Opening the link
// only shows a button; the POST to Go does the write, so a link scanner or prefetch
// cannot confirm a signup. The outcome copy is the existing confirmation page's
// (frontend/src/data/campaign.ts), reused by the screen so Flask and React say the same thing.

export const volunteerConfirmPath = '/api/campaign-volunteers/confirm';
export const volunteerConfirmTokenPattern = /^[A-Za-z0-9_-]{1,128}$/;

export const volunteerConfirmCopy = {
  title: 'Vahvista ilmoittautuminen',
  intro: 'Vahvista sähköpostiosoitteesi painamalla painiketta. Ilmoittautumisesi tallentuu vasta sen jälkeen.',
  button: 'Vahvista ilmoittautuminen',
  working: 'Vahvistetaan…',
  error: 'Vahvistus ei onnistunut. Yritä uudelleen hetken kuluttua.',
  rateLimited: 'Yrityksiä on liian monta. Odota hetki ja yritä uudelleen.',
  noscript: 'Vahvistus tarvitsee JavaScriptin. Ota käyttöön JavaScript tai kirjoita osoitteeseen tuki@mielenosoitukset.fi.',
} as const;

export type VolunteerConfirmOutcome = 'success' | 'invalid' | 'expired' | 'error' | 'rate-limited';

type Fetcher = (input: string, init: RequestInit) => Promise<Pick<Response, 'ok' | 'status' | 'json'>>;

/**
 * POSTs the token. `success`, `invalid` and `expired` are the answers Go gives;
 * anything else (network failure, 5xx, malformed body, 429) is not a verdict on
 * the link, so the page keeps its button and lets the visitor retry.
 */
export async function confirmVolunteer(token: string, fetcher: Fetcher = (input, init) => fetch(input, init)): Promise<VolunteerConfirmOutcome> {
  if (!volunteerConfirmTokenPattern.test(token)) return 'invalid';
  try {
    const response = await fetcher(volunteerConfirmPath, {
      method: 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (response.status === 429) return 'rate-limited';
    const body = await response.json().catch(() => null) as { status?: unknown } | null;
    if (response.ok && body?.status === 'success') return 'success';
    if (response.status === 400 && (body?.status === 'invalid' || body?.status === 'expired')) return body.status;
    return 'error';
  } catch {
    return 'error';
  }
}
