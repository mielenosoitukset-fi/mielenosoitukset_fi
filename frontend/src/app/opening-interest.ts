/**
 * Submit for the opening interest form. The backend ships separately, so until
 * an endpoint is configured nothing is sent and the form says so honestly.
 */
export type OpeningInterest = {
  email: string; city: string; consent: boolean;
  willingToPay: '' | 'yes' | 'maybe' | 'no'; priceBand: '' | 'under10' | '10to20' | 'over20'; note: string;
};

export type OpeningResult = { ok: true } | { ok: false; reason: 'not-open' | 'failed' };

export const openingCopy = {
  invalidEmail: 'Tarkista sähköpostiosoite. Siinä pitää olla @-merkki ja verkkotunnus.',
  consentRequired: 'Anna suostumus, niin voimme lähettää viestit.',
  notOpen: 'Ilmoittautuminen ei ole vielä käytössä. Tietojasi ei ole lähetetty.',
  failed: 'Ilmoittautuminen epäonnistui. Yritä uudelleen.',
} as const;

export const emailLooksValid = (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value.trim());

export async function submitOpeningInterest(endpoint: string | undefined, interest: OpeningInterest): Promise<OpeningResult> {
  if (!endpoint) return { ok: false, reason: 'not-open' };
  try {
    const response = await fetch(endpoint, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(interest),
    });
    const result = await response.json().catch(() => null);
    return response.ok && result?.success === true ? { ok: true } : { ok: false, reason: 'failed' };
  } catch {
    return { ok: false, reason: 'failed' };
  }
}
