/** Request helpers for the organization request pages, kept free of React and CSS so Node can test them. */
import { orgRequestStatusReadPath } from './org-request-route.ts';
import type { OrgRequestSummary } from './org-request-route.ts';

/** Splits the links field: one address per line, or separated by spaces or commas. */
export function splitLinks(value: string): string[] {
  return value.split(/[\s,]+/).map(item => item.trim()).filter(Boolean).slice(0, 5);
}

/** The JSON body Go reads (go-api/org_request.go, orgRequestInput). */
export function orgRequestBody(form: FormData, startedAt: number) {
  const represents = form.get('represents') === 'yes';
  const access = represents ? String(form.get('access') ?? '') : '';
  return {
    represents,
    organization_name: String(form.get('organization_name') ?? ''),
    organization_email: String(form.get('organization_email') ?? ''),
    links: splitLinks(String(form.get('links') ?? '')),
    business_id: String(form.get('business_id') ?? ''),
    description: String(form.get('description') ?? ''),
    applicant_name: String(form.get('applicant_name') ?? ''),
    applicant_email: String(form.get('applicant_email') ?? ''),
    applicant_role: represents ? String(form.get('applicant_role') ?? '') : '',
    access,
    invite_email: access === 'invite' ? String(form.get('invite_email') ?? '') : '',
    onboarding: form.get('onboarding') === 'on',
    consent: form.get('consent') === 'on',
    homepage: String(form.get('homepage') ?? ''),
    started_at: startedAt,
  };
}

export type OrgRequestState =
  | { kind: 'loading' }
  | { kind: 'missing' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; request: OrgRequestSummary };

/** The token from the link's fragment, or '' when it does not look like one. */
export function statusToken(hash: string): string {
  const token = hash.replace(/^#/, '');
  return /^[A-Za-z0-9_-]{16,64}$/.test(token) ? token : '';
}

export async function readStatus(token: string, fetcher: typeof fetch = fetch): Promise<OrgRequestState> {
  try {
    const response = await fetcher(orgRequestStatusReadPath, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ token }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.success && data.request) return { kind: 'ready', request: data.request as OrgRequestSummary };
    if (response.status === 404) return { kind: 'missing' };
    return { kind: 'error', message: typeof data.message === 'string' ? data.message : 'Tilaa ei voitu hakea. Yritä uudelleen.' };
  } catch {
    return { kind: 'error', message: 'Tilaa ei voitu hakea. Tarkista yhteys ja yritä uudelleen.' };
  }
}
