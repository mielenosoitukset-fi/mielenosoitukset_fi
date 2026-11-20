/**
 * Passkey management for the React settings page. The endpoints are Flask's, unchanged
 * (`/users/auth/api/v2/passkeys/*`): registration options, rename and delete need step-up (the
 * shared dialog handles it), the registration verify does not. The browser ceremony is ported from
 * `static/js/webauthn.js`. Pure logic, no React: PasskeysCard.tsx is the interface.
 */
import { base64UrlToBuffer, bufferToBase64Url } from './account-stepup.ts';

export const passkeyApi = {
  registerOptions: '/users/auth/api/v2/passkeys/register/options',
  registerVerify: '/users/auth/api/v2/passkeys/register/verify',
  rename: '/users/auth/api/v2/passkeys/rename',
  remove: '/users/auth/api/v2/passkeys/delete',
};

// Finnish texts are the Flask settings page's own (settings.html) where one exists.
export const passkeyCopy = {
  add: 'Lisää passkey',
  adding: 'Lisätään…',
  nameLabel: 'Passkeyn nimi',
  unsupported: 'Selaimesi ei tue passkeyjä.',
  added: 'Passkey lisätty onnistuneesti.',
  addFailed: 'Passkeyn lisääminen epäonnistui',
  optionsFailed: 'Rekisteröintiasetuksia ei saatu haettua',
  saveFailed: 'Passkeyn tallentaminen epäonnistui',
  rename: 'Nimeä',
  renamePrompt: 'Uusi nimi passkeylle',
  renameSave: 'Tallenna nimi',
  renameFailed: 'Passkeyn nimeäminen epäonnistui',
  nameRequired: 'Anna passkeylle nimi.',
  remove: 'Poista',
  removeTitle: 'Poista passkey',
  removeAsk: 'Haluatko poistaa tämän passkeyn?',
  removeFailed: 'Passkeyn poistaminen epäonnistui',
  cancel: 'Peruuta',
  refreshFailed: 'Passkey-listan päivitys epäonnistui. Lataa sivu uudelleen.',
} as const;

export const passkeyDefaultName = 'Authenticator';

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

/** Server options (base64url strings) to the shape `navigator.credentials.create` needs: challenge, user id and excluded ids are binary. */
export function decodeCreationOptions(options: unknown): PublicKeyCredentialCreationOptions {
  const copy = JSON.parse(JSON.stringify(options)) as Record<string, unknown>;
  if (typeof copy.challenge === 'string') copy.challenge = base64UrlToBuffer(copy.challenge);
  if (isObject(copy.user) && typeof copy.user.id === 'string') copy.user = { ...copy.user, id: base64UrlToBuffer(copy.user.id) };
  for (const key of ['allowCredentials', 'excludeCredentials']) {
    const list = copy[key];
    if (Array.isArray(list)) copy[key] = list.map(item => isObject(item) && typeof item.id === 'string' ? { ...item, id: base64UrlToBuffer(item.id) } : item);
  }
  return copy as unknown as PublicKeyCredentialCreationOptions;
}

/** The new credential as JSON for `/register/verify`. */
export function formatRegistrationCredential(credential: PublicKeyCredential) {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: credential.rawId ? bufferToBase64Url(credential.rawId) : credential.id,
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64Url(response.clientDataJSON),
      attestationObject: bufferToBase64Url(response.attestationObject),
      transports: typeof response.getTransports === 'function' ? response.getTransports() : [],
    },
  };
}

const jsonPost = (url: string, payload: unknown, fetcher: typeof fetch) =>
  fetcher(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload) });

/** Built fresh so a step-up retry sends them again. */
export const passkeyOptionsRequest = (fetcher: typeof fetch = fetch) => (): Promise<Response> => jsonPost(passkeyApi.registerOptions, {}, fetcher);
export const passkeyRenameRequest = (id: string, name: string, fetcher: typeof fetch = fetch) => (): Promise<Response> =>
  jsonPost(passkeyApi.rename, { id, name }, fetcher);
export const passkeyDeleteRequest = (id: string, fetcher: typeof fetch = fetch) => (): Promise<Response> => jsonPost(passkeyApi.remove, { id }, fetcher);

async function readBody(response: Response): Promise<Record<string, unknown>> {
  try { const body: unknown = await response.json(); return isObject(body) ? body : {}; } catch { return {}; }
}
const messageOf = (body: Record<string, unknown>, fallback: string) =>
  typeof body.message === 'string' && body.message ? body.message.slice(0, 300) : fallback;

/** Flask's own text as returned (cut), a fallback otherwise. `null` means the user cancelled step-up. */
export async function readPasskeyResult(response: Response | null, fallback: string): Promise<{ ok: boolean; message: string } | null> {
  if (!response) return null;
  const body = await readBody(response);
  return { ok: response.ok && body.status === 'success', message: messageOf(body, fallback) };
}

export type RegisterResult = { ok: boolean; message: string } | null;

/**
 * Add a passkey: options (step-up when asked), `navigator.credentials.create`, verify. `null` means the user
 * cancelled the step-up. Dismissing the browser's own prompt is an ordinary failure with the fallback text.
 * `create` and `fetcher` are injectable for tests.
 */
export async function registerPasskey(
  name: string,
  withStepUp: (request: () => Promise<Response>) => Promise<Response | null>,
  fetcher: typeof fetch = fetch,
  create: (options: CredentialCreationOptions) => Promise<Credential | null> = options => navigator.credentials.create(options),
): Promise<RegisterResult> {
  try {
    const optionsResponse = await withStepUp(passkeyOptionsRequest(fetcher));
    if (!optionsResponse) return null;
    const optionsBody = await readBody(optionsResponse);
    if (!optionsResponse.ok || optionsBody.status !== 'success' || !isObject(optionsBody.options)) {
      return { ok: false, message: messageOf(optionsBody, passkeyCopy.optionsFailed) };
    }
    const credential = await create({ publicKey: decodeCreationOptions(optionsBody.options) });
    if (!credential) return { ok: false, message: passkeyCopy.addFailed };
    const verifyResponse = await jsonPost(passkeyApi.registerVerify,
      { credential: formatRegistrationCredential(credential as PublicKeyCredential), name: name || passkeyDefaultName }, fetcher);
    const verifyBody = await readBody(verifyResponse);
    if (verifyResponse.ok && verifyBody.status === 'success') return { ok: true, message: passkeyCopy.added };
    return { ok: false, message: messageOf(verifyBody, passkeyCopy.saveFailed) };
  } catch {
    return { ok: false, message: passkeyCopy.addFailed };
  }
}
