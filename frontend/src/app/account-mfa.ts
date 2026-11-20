/**
 * MFA device management for the React settings page. The endpoints are Flask's, unchanged
 * (`/users/auth/api/v2/mfa`, `mfa_device_rename`, `mfa_device_revoke`); setup needs no step-up,
 * rename and revoke do, and the shared step-up dialog handles that. Pure logic, no React:
 * MfaCard.tsx is the interface.
 */

export const mfaApi = '/users/auth/api/v2/mfa';
export const mfaRenameApi = '/users/auth/api/v2/mfa_device_rename';
export const mfaRevokeApi = '/users/auth/api/v2/mfa_device_revoke';
export const mfaDeviceNameMax = 60;

// Finnish texts are the Flask settings page's own (settings.html) where one exists.
export const mfaCopy = {
  enable: 'Ota käyttöön MFA',
  addDevice: 'Lisää laite',
  scanTitle: 'MFA:n QR-koodi',
  scan: 'Skannaa tämä QR-koodi MFA-sovelluksellasi ja syötä koodi vahvistaaksesi.',
  qrAlt: 'MFA:n QR-koodi',
  secretHelp: 'Jos et voi skannata koodia, kirjoita tämä avain sovellukseen:',
  codeLabel: 'Vahvistuskoodi',
  codePlaceholder: '6-numeroinen koodi',
  nameLabel: 'Laitteen nimi',
  verify: 'Vahvista MFA',
  verifying: 'Vahvistetaan…',
  starting: 'Haetaan…',
  cancel: 'Peruuta',
  codeRequired: 'Syötä koodi!',
  startFailed: 'Virhe MFA:n aloittamisessa',
  verifyFailed: 'Virhe MFA:n vahvistamisessa',
  rename: 'Nimeä',
  renamePrompt: 'Uusi nimi laitteelle',
  renameSave: 'Tallenna nimi',
  renameFailed: 'Laitteen nimeäminen epäonnistui',
  nameRequired: 'Anna laitteelle nimi.',
  remove: 'Poista',
  removeTitle: 'Poista MFA-laite',
  removeAsk: 'Haluatko poistaa tämän laitteen?',
  removeFailed: 'Laitteen poistaminen epäonnistui',
  refreshFailed: 'Laitelistan päivitys epäonnistui. Lataa sivu uudelleen.',
} as const;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

/** The one image format Flask sends: a PNG data URL of plain base64. Anything else is not rendered. */
const qrPattern = /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/;
/** A TOTP secret is base32. */
const secretPattern = /^[A-Z2-7]{16,128}$/;

export type MfaStart = { qr: string; secret: string };

/** Reads the `request_activation` answer. A malformed one, or one with an image that is not a PNG data URL, is `null`. */
export function readMfaStart(body: unknown): MfaStart | null {
  if (!isObject(body) || body.status !== 'pending') return null;
  const { qr_code: qr, secret } = body;
  if (typeof qr !== 'string' || typeof secret !== 'string' || qr.length > 200_000) return null;
  if (!qrPattern.test(qr) || !secretPattern.test(secret)) return null;
  return { qr, secret };
}

/** Spaces are allowed in what people copy; the code itself is six digits. Returns the code or `null`. */
export function cleanMfaCode(raw: string): string | null {
  const code = raw.replace(/[\s-]/g, '');
  return /^\d{6}$/.test(code) ? code : null;
}

/** The name Flask would store: trimmed and cut at 60 characters. `null` for an empty one. */
export function cleanDeviceName(raw: string): string | null {
  const name = Array.from(raw.trim()).slice(0, mfaDeviceNameMax).join('');
  return name ? name : null;
}

const jsonPost = (url: string, payload: unknown, fetcher: typeof fetch) =>
  fetcher(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload) });

export const mfaStartPayload = () => ({ step: 'request_activation' });
export const mfaVerifyPayload = (code: string, secret: string, deviceName: string) =>
  ({ step: 'verify_code', code, secret, device_name: deviceName });

export const mfaStartRequest = (fetcher: typeof fetch = fetch) => jsonPost(mfaApi, mfaStartPayload(), fetcher);
export const mfaVerifyRequest = (code: string, secret: string, deviceName: string, fetcher: typeof fetch = fetch) =>
  jsonPost(mfaApi, mfaVerifyPayload(code, secret, deviceName), fetcher);
/** Built fresh so a step-up retry sends it again. */
export const mfaRenameRequest = (deviceId: string, name: string, fetcher: typeof fetch = fetch) => (): Promise<Response> =>
  jsonPost(mfaRenameApi, { device_id: deviceId, name }, fetcher);
export const mfaRevokeRequest = (deviceId: string, fetcher: typeof fetch = fetch) => (): Promise<Response> =>
  jsonPost(mfaRevokeApi, { device_id: deviceId }, fetcher);

/** Flask's own text as returned (cut), a fallback otherwise. `null` means the user cancelled step-up. */
export async function readMfaResult(response: Response | null, fallback: string): Promise<{ ok: boolean; message: string; body: Record<string, unknown> } | null> {
  if (!response) return null;
  let body: unknown = null;
  try { body = await response.json(); } catch { /* HTML or empty answer */ }
  const raw = isObject(body) ? body : {};
  const message = typeof raw.message === 'string' && raw.message ? raw.message.slice(0, 300) : fallback;
  return { ok: response.ok && raw.status === 'success', message, body: raw };
}
