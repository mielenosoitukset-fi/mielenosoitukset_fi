/**
 * Client for the opening interest list (docs/campaign-opening-interest.md). The Go API stores
 * the signup and consumes the one-time links; the answer is the same for a new, a pending and an
 * already registered address, so the page never says which one it was.
 */
export type OpeningInterest = {
  email: string; city: string; consent: boolean;
  willingToPay: '' | 'yes' | 'maybe' | 'no'; priceBand: '' | 'under10' | '10to20' | 'over20'; website: string;
};

export type OpeningResult = { ok: true } | { ok: false; message: string };

export const openingPath = '/api/campaign-opening/interest';

export const openingCopy = {
  invalidEmail: 'Tarkista sähköpostiosoite. Siinä pitää olla @-merkki ja verkkotunnus.',
  consentRequired: 'Anna suostumus, niin voimme lähettää viestit.',
  notOpen: 'Ilmoittautuminen ei ole vielä käytössä. Tietojasi ei ole lähetetty.',
  rateLimited: 'Yrityksiä on liian monta. Odota hetki ja yritä uudelleen.',
  failed: 'Ilmoittautuminen epäonnistui. Yritä uudelleen.',
} as const;

export const emailLooksValid = (value: string) => value.trim().length <= 254 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value.trim());

async function post(path: string, body: Record<string, unknown>): Promise<{ ok: boolean; status: number; message: string }> {
  try {
    const response = await fetch(path, {
      method: 'POST', credentials: 'omit', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({})) as { message?: unknown };
    return { ok: response.ok, status: response.status, message: typeof data.message === 'string' ? data.message : '' };
  } catch {
    return { ok: false, status: 0, message: '' };
  }
}

export async function submitOpeningInterest(interest: OpeningInterest): Promise<OpeningResult> {
  const result = await post(openingPath, {
    email: interest.email, city: interest.city, willing: interest.willingToPay, band: interest.priceBand,
    consent: true, locale: 'fi', website: interest.website,
  });
  if (result.ok) return { ok: true };
  if (result.status === 404) return { ok: false, message: openingCopy.notOpen };
  if (result.status === 429) return { ok: false, message: openingCopy.rateLimited };
  if (result.status === 400 && result.message) return { ok: false, message: result.message };
  return { ok: false, message: openingCopy.failed };
}

export type OpeningLinkMode = 'vahvista' | 'peru';

/** The mode and one-time token from `?avajaiset=...#t=...`. The token stays in the fragment, so it is never sent to a server or logged. */
export function readOpeningLink(search: string, hash: string): { mode: OpeningLinkMode; token: string } | null {
  const mode = new URLSearchParams(search).get('avajaiset');
  if (mode !== 'vahvista' && mode !== 'peru') return null;
  return { mode, token: new URLSearchParams(hash.replace(/^#/, '')).get('t') ?? '' };
}

export const confirmOpening = (token: string) => post(`${openingPath}/confirm`, { token });
export const removeOpening = (token: string) => post(`${openingPath}/unsubscribe`, { token });

export const openingLinkCopy = {
  confirmTitle: 'Vahvista ilmoittautumisesi',
  confirmIntro: 'Vahvista ilmoittautuminen kampanjan avajaisiin painamalla painiketta. Kerromme paikan, kun se varmistuu.',
  confirmButton: 'Vahvista ilmoittautuminen',
  confirmed: 'Ilmoittautuminen on vahvistettu. Kiitos. Kerromme paikan, kun se varmistuu. Voit perua ilmoittautumisen milloin tahansa viestin linkistä.',
  removeTitle: 'Poista tietosi',
  removeIntro: 'Kun painat painiketta, poistamme ilmoittautumisesi ja kaikki siihen liittyvät tiedot. Et saa enää viestejä avajaisista.',
  removeButton: 'Poista tietoni',
  removed: 'Tietosi on poistettu. Et saa enää viestejä avajaisista.',
  badLink: 'Linkki ei kelpaa. Avaa se uudelleen sähköpostiviestistä.',
  expired: 'Linkki on vanhentunut tai jo käytetty. Jos olet jo vahvistanut ilmoittautumisen, sinun ei tarvitse tehdä mitään. Muuten voit ilmoittautua uudelleen lomakkeella.',
  error: 'Toiminto ei onnistunut. Yritä uudelleen hetken kuluttua.',
} as const;
