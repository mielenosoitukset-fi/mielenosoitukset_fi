/**
 * Step-up ("sudo") for account writes that stay in Flask. Flask answers a guarded route with
 * 403 and {"error": "step_up_required"} when the session has no recent re-authentication
 * (`sudo_required` in mielenosoitukset_fi/utils/step_up.py). The helper below re-authenticates
 * with a passkey or the password (plus an MFA code when the account has one) and then retries
 * the original request once. Pure logic, no React: StepUpDialog.tsx is the interface.
 */

const stepUpBase = '/users/auth/api/v2/step-up';
export const stepUpApi = {
  options: `${stepUpBase}/options`,
  passkeyOptions: `${stepUpBase}/webauthn/options`,
  passkeyVerify: `${stepUpBase}/webauthn/verify`,
  password: `${stepUpBase}/password`,
};

// Finnish texts are the Flask settings page's own (settings.html), so both pages say the same.
export const stepUpCopy = {
  title: 'Vahvista henkilöllisyytesi',
  lead: 'Tämä toiminto vaatii tuoreen tunnistautumisen. Vahvista alla.',
  passkey: 'Vahvista passkeyllä',
  password: 'Salasana',
  passwordPlaceholder: 'Kirjoita salasana',
  totp: 'MFA-koodi',
  totpPlaceholder: '6-numeroinen koodi',
  withPassword: 'Vahvista salasanalla',
  cancel: 'Peruuta',
  working: 'Vahvistetaan…',
  passkeyFailed: 'Passkey-tunnistautuminen epäonnistui',
  passkeyFailedTryPassword: 'Passkey-tunnistautuminen epäonnistui. Yritä salasanalla.',
  passwordFailed: 'Virheellinen salasana',
  failed: 'Tunnistautuminen epäonnistui',
  optionsFailed: 'Vahvistustapojen haku epäonnistui. Yritä salasanalla.',
} as const;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

/** True for the one answer that asks for step-up: status 403 and `error: "step_up_required"`. */
export function isStepUpRequired(status: number, body: unknown): boolean {
  return status === 403 && isObject(body) && body.error === 'step_up_required';
}

/** Reads the answer without consuming it, so the caller can still read the body. A non-JSON answer is `null`. */
export async function needsStepUp(response: Response): Promise<boolean> {
  if (response.status !== 403) return false;
  let body: unknown = null;
  try { body = await response.clone().json(); } catch { /* a non-JSON 403 is an ordinary refusal */ }
  return isStepUpRequired(response.status, body);
}

export type StepUpMethods = { passkey: boolean; password: boolean; requiresTotp: boolean };

/** What `/step-up/options` offers. A malformed answer still allows the password, which Flask always accepts. */
export function readStepUpMethods(body: unknown, passkeySupported: boolean): StepUpMethods {
  const list = isObject(body) && Array.isArray(body.methods) ? body.methods.filter(isObject) : [];
  return {
    passkey: passkeySupported && list.some(method => method.type === 'webauthn'),
    password: true,
    requiresTotp: list.some(method => method.type === 'password_totp' && method.requires_totp === true),
  };
}

/** The JSON body for `/step-up/password`; the MFA code is sent only when one was typed. */
export function passwordStepUpPayload(password: string, totp: string) {
  const code = totp.trim();
  return { password, ...(code ? { totp_code: code } : {}) };
}

/**
 * Runs `request`; when Flask asks for step-up, calls `askUser` (which resolves true once the user
 * re-authenticated) and runs `request` once more. Resolves `null` when the user cancels.
 */
export async function runWithStepUp(request: () => Promise<Response>, askUser: () => Promise<boolean>): Promise<Response | null> {
  const first = await request();
  if (!(await needsStepUp(first))) return first;
  if (!(await askUser())) return null;
  return request();
}

// WebAuthn ceremony helpers, ported from static/js/webauthn.js. The server sends binary values as base64url.

export function bufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function base64UrlToBuffer(value: string): ArrayBuffer {
  const base = value.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(base + (base.length % 4 ? '='.repeat(4 - (base.length % 4)) : ''));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** Server options (base64url strings) to the shape `navigator.credentials.get` needs. */
export function decodePublicKeyOptions(options: unknown): PublicKeyCredentialRequestOptions {
  const copy = JSON.parse(JSON.stringify(options)) as Record<string, unknown>;
  if (typeof copy.challenge === 'string') copy.challenge = base64UrlToBuffer(copy.challenge);
  for (const key of ['allowCredentials', 'excludeCredentials']) {
    const list = copy[key];
    if (Array.isArray(list)) copy[key] = list.map(item => isObject(item) && typeof item.id === 'string' ? { ...item, id: base64UrlToBuffer(item.id) } : item);
  }
  return copy as unknown as PublicKeyCredentialRequestOptions;
}

/** The assertion as JSON for `/step-up/webauthn/verify`. */
export function formatAssertionCredential(credential: PublicKeyCredential) {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: credential.rawId ? bufferToBase64Url(credential.rawId) : credential.id,
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64Url(response.clientDataJSON),
      authenticatorData: bufferToBase64Url(response.authenticatorData),
      signature: bufferToBase64Url(response.signature),
      userHandle: response.userHandle ? bufferToBase64Url(response.userHandle) : null,
    },
  };
}

export type StepUpResult = { ok: true } | { ok: false; message: string };

const postJson = (url: string, payload: unknown, fetcher: typeof fetch) =>
  fetcher(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload ?? {}) });

async function readJson(response: Response): Promise<Record<string, unknown>> {
  try { const body: unknown = await response.json(); return isObject(body) ? body : {}; } catch { return {}; }
}

const serverMessage = (body: Record<string, unknown>, fallback: string) =>
  typeof body.message === 'string' && body.message ? body.message.slice(0, 300) : fallback;

/** Asks which methods the account can step up with. */
export async function fetchStepUpMethods(passkeySupported: boolean, fetcher: typeof fetch = fetch): Promise<StepUpMethods | null> {
  try {
    const response = await postJson(stepUpApi.options, {}, fetcher);
    return response.ok ? readStepUpMethods(await readJson(response), passkeySupported) : null;
  } catch { return null; }
}

/** Step up with the password, and the MFA code when the account has MFA. */
export async function stepUpWithPassword(password: string, totp: string, fetcher: typeof fetch = fetch): Promise<StepUpResult> {
  try {
    const response = await postJson(stepUpApi.password, passwordStepUpPayload(password, totp), fetcher);
    const body = await readJson(response);
    if (response.ok && body.status === 'success') return { ok: true };
    return { ok: false, message: serverMessage(body, stepUpCopy.passwordFailed) };
  } catch { return { ok: false, message: stepUpCopy.failed }; }
}

/** Step up with a passkey: options, `navigator.credentials.get`, verify. `getCredential` is injectable for tests. */
export async function stepUpWithPasskey(
  fetcher: typeof fetch = fetch,
  getCredential: (options: CredentialRequestOptions) => Promise<Credential | null> = options => navigator.credentials.get(options),
): Promise<StepUpResult> {
  try {
    const optionsResponse = await postJson(stepUpApi.passkeyOptions, {}, fetcher);
    const optionsBody = await readJson(optionsResponse);
    if (!optionsResponse.ok || optionsBody.status !== 'success') return { ok: false, message: serverMessage(optionsBody, stepUpCopy.passkeyFailed) };
    const assertion = await getCredential({ publicKey: decodePublicKeyOptions(optionsBody.options) });
    if (!assertion) return { ok: false, message: stepUpCopy.passkeyFailedTryPassword };
    const verifyResponse = await postJson(stepUpApi.passkeyVerify, { credential: formatAssertionCredential(assertion as PublicKeyCredential) }, fetcher);
    const verifyBody = await readJson(verifyResponse);
    if (verifyResponse.ok && verifyBody.status === 'success') return { ok: true };
    return { ok: false, message: serverMessage(verifyBody, stepUpCopy.passkeyFailed) };
  } catch { return { ok: false, message: stepUpCopy.passkeyFailedTryPassword }; }
}
