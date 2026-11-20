// The "all notifications" page, GET /api/notifications/all (the URL the header menu links to).
// The list and the mark-read action stay on the unchanged Flask JSON endpoints; Flask
// decides which notices the signed-in account may see (case scope, assignments).
export const notificationsPath = '/api/notifications/all';
/** How many notices the page asks for; the Flask list endpoint caps `limit` at the same value. */
export const notificationsPageLimit = 50;

export const notificationsCopy = {
  title: 'Kaikki ilmoitukset',
  lead: 'Ilmoitukset kutsuista, kaveripyynnöistä ja seuraamiesi mielenosoitusten muutoksista.',
  loading: 'Ladataan ilmoituksia…',
  empty: 'Ei ilmoituksia',
  unavailable: 'Ilmoitusten lataaminen epäonnistui.',
  retry: 'Yritä uudelleen',
  loginLead: 'Kirjaudu sisään nähdäksesi ilmoituksesi.',
  login: 'Kirjaudu sisään',
  markRead: 'Merkitse kaikki luetuiksi',
  marking: 'Merkitään…',
  markError: 'Ilmoituksia ei voitu merkitä luetuiksi.',
  unread: 'Lukematon',
  noscript: 'Ilmoitukset näkyvät, kun JavaScript on käytössä.',
};

export type Notice = { id: string; type: string; link: string; message: string; time: string; read: boolean };

/** Keeps only well-formed notices; anything else makes the whole answer unusable. */
export function readNotices(body: unknown): Notice[] | null {
  if (!Array.isArray(body)) return null;
  const notices: Notice[] = [];
  for (const item of body.slice(0, notificationsPageLimit)) {
    if (!item || typeof item !== 'object') return null;
    const { id, type, link, message, time, read } = item as Record<string, unknown>;
    if (typeof message !== 'string' || typeof link !== 'string' || typeof time !== 'string') return null;
    notices.push({ id: typeof id === 'string' ? id : '', type: typeof type === 'string' ? type : '', link, message, time, read: read === true });
  }
  return notices;
}

/** Same-origin path of a notice link, or null. Never an absolute URL to another site or a script URL. */
export function noticeHref(value: string, origin: string): string | null {
  if (!value || value === '#') return null;
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

/** Flask's login_required answers a redirect to the login page; `redirect: 'manual'` shows it as status 0. */
export function classifyNoticesResponse(response: { status: number; type?: string; ok: boolean }): 'ok' | 'signed-out' | 'unavailable' {
  if (response.type === 'opaqueredirect' || response.status === 401) return 'signed-out';
  return response.ok ? 'ok' : 'unavailable';
}

export function formatNoticeTime(value: string, locale: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Helsinki' }).format(date);
}
