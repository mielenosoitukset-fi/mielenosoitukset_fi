// Organization invitation page, /users/orgs/accept_invite?organization_id=<id>. The page
// reads the signed-in account's invitation from Go and joins only when the button is
// pressed, so opening the link never joins anyone. The copy is Flask's flash messages
// for this route.
export const orgInviteLoginPath = '/users/orgs/accept_invite';
export const orgInviteIdPattern = /^[0-9a-f]{24}$/;

export const orgInviteCopy = {
  title: 'Liity organisaatioon',
  loading: 'Ladataan kutsua…',
  signedOut: 'Kirjaudu sisään liittyäksesi organisaatioon.',
  login: 'Kirjaudu sisään',
  missingId: 'Organisaation tunnus puuttuu.',
  noOrganization: 'Organisaatiota ei löytynyt.',
  notInvited: 'Kutsua ei löytynyt.',
  protectedOrganization: 'Tämän organisaation jäsenyyttä hallitaan hallinnon hyväksyntöjen kautta.',
  button: 'Liity organisaatioon',
  working: 'Liitytään…',
  joinedTitle: 'Olet nyt jäsen',
  openOrganization: 'Avaa organisaatio',
  home: 'Etusivulle',
  unavailable: 'Kutsua ei voitu hakea. Yritä uudelleen hetken kuluttua.',
  error: 'Liittyminen ei onnistunut. Yritä uudelleen hetken kuluttua.',
  rateLimited: 'Yrityksiä on liian monta. Odota hetki ja yritä uudelleen.',
  expiredSession: 'Istunto on vanhentunut. Lataa sivu uudelleen.',
  noscript: 'Kutsun hyväksyminen tarvitsee JavaScriptin. Ota käyttöön JavaScript tai kirjoita osoitteeseen tuki@mielenosoitukset.fi.',
} as const;

export const invitedIntro = (name: string) => `Sinut on kutsuttu organisaatioon ${name}. Liity painamalla painiketta.`;
export const joinedText = (name: string) => `Liityit organisaatioon ${name}.`;

export type InvitationState =
  | { kind: 'signed-out' }
  | { kind: 'invited'; name: string; csrf: string }
  | { kind: 'not-invited' | 'organization-not-found' | 'protected' | 'unavailable' };

export type AcceptOutcome =
  | { kind: 'joined'; name: string }
  | { kind: 'not-invited' | 'organization-not-found' | 'protected' | 'signed-out' | 'session' | 'rate-limited' | 'error' };

type Fetcher = (input: string, init: RequestInit) => Promise<Pick<Response, 'ok' | 'status' | 'json'>>;
const defaultFetch: Fetcher = (input, init) => fetch(input, init);
const readStates = new Set(['not-invited', 'organization-not-found', 'protected']);

export const invitationUrl = (organizationId: string) => `/api/organizations/${organizationId}/invitation`;

export function loginUrlFor(loginBase: string, organizationId: string): string {
  return `${loginBase}?next=${encodeURIComponent(`${orgInviteLoginPath}?organization_id=${organizationId}`)}`;
}

/** Reads the invitation. A failure is `unavailable`, never "not invited". */
export async function loadInvitation(organizationId: string, fetcher: Fetcher = defaultFetch): Promise<InvitationState> {
  if (!orgInviteIdPattern.test(organizationId)) return { kind: 'organization-not-found' };
  try {
    const response = await fetcher(invitationUrl(organizationId), {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'no-referrer', headers: { Accept: 'application/json' },
    });
    if (response.status === 401) return { kind: 'signed-out' };
    if (!response.ok) return { kind: 'unavailable' };
    const body = await response.json().catch(() => null) as { state?: unknown; csrf?: unknown; organization?: { name?: unknown } } | null;
    if (body?.state === 'invited' && typeof body.csrf === 'string' && body.csrf && typeof body.organization?.name === 'string') {
      return { kind: 'invited', name: body.organization.name, csrf: body.csrf };
    }
    if (typeof body?.state === 'string' && readStates.has(body.state)) return { kind: body.state as 'not-invited' | 'organization-not-found' | 'protected' };
    return { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Joins. Only Go's own answers end the page; anything else leaves the button usable. */
export async function acceptInvitation(organizationId: string, csrf: string, fetcher: Fetcher = defaultFetch): Promise<AcceptOutcome> {
  if (!orgInviteIdPattern.test(organizationId) || !csrf) return { kind: 'error' };
  try {
    const response = await fetcher(`${invitationUrl(organizationId)}/accept`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'no-referrer',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-Token': csrf },
      body: '{}',
    });
    if (response.status === 401) return { kind: 'signed-out' };
    if (response.status === 429) return { kind: 'rate-limited' };
    const body = await response.json().catch(() => null) as { state?: unknown; success?: unknown; organization?: { name?: unknown } } | null;
    if (response.ok && body?.state === 'joined' && body.success === true) {
      return { kind: 'joined', name: typeof body.organization?.name === 'string' ? body.organization.name : '' };
    }
    if ((response.ok || response.status === 403) && typeof body?.state === 'string' && readStates.has(body.state)) {
      return { kind: body.state as 'not-invited' | 'organization-not-found' | 'protected' };
    }
    // A 403 without a state is a stale or foreign token: reload to get a fresh one.
    if (response.status === 403) return { kind: 'session' };
    return { kind: 'error' };
  } catch {
    return { kind: 'error' };
  }
}
