// Login page, GET /users/auth/login. Only the page moves to React: the password POST, the
// MFA step, throttling, the passkey API, the god flow and every redirect after sign-in stay
// in Flask, which still decides where a signed-in visitor goes (see loginLegacyLocation).
export const loginPath = '/users/auth/login';
export const loginApi = {
  mfaCheck: '/users/auth/2fa_check',
  resendConfirmation: '/users/auth/resend_confirmation',
  passwordReset: '/users/auth/password_reset_request',
  passkeyOptions: '/users/auth/api/v2/passkeys/login/options',
  passkeyVerify: '/users/auth/api/v2/passkeys/login/verify',
};

export const loginCopy = {
  title: 'Kirjaudu sisään',
  lead: 'Kirjaudu hallitsemaan ilmoituksiasi, organisaatioitasi ja käyttäjätiliäsi.',
  passkey: 'Jatka passkeyllä',
  passkeyHint: 'Jos laitteellesi on tallennettu passkey, selain voi ehdottaa sitä myös käyttäjänimikentässä.',
  divider: 'tai salasanalla',
  username: 'Käyttäjänimi',
  usernamePlaceholder: 'Syötä käyttäjänimesi',
  password: 'Salasana',
  passwordPlaceholder: 'Syötä salasanasi',
  showPassword: 'Näytä salasana',
  hidePassword: 'Piilota salasana',
  remember: 'Muista minut',
  forgot: 'Unohtuiko salasana?',
  submit: 'Kirjaudu sisään',
  helpTitle: 'Tarvitsetko apua tilillesi pääsyssä?',
  register: 'Luo uusi tili',
  resend: 'Lähetä uusi vahvistuslinkki',
  resendLead: 'Syötä sähköposti tai käyttäjänimi. Lähetämme uuden vahvistuslinkin, jos tili löytyy.',
  resendLabel: 'Sähköposti tai käyttäjänimi',
  resendSubmit: 'Lähetä linkki',
  close: 'Sulje',
  resendHint: 'Tarkista myös roskaposti. Linkki vanhenee, joten lähetämme aina uuden.',
  mfaTitle: 'Kaksivaiheinen tunnistautuminen',
  mfaLead: 'Syötä 6-numeroinen koodi todennussovelluksestasi.',
  mfaLabel: 'Vahvistuskoodi',
  mfaHint: 'Avaa todennussovelluksesi ja syötä näkyvä koodi.',
  mfaSubmit: 'Vahvista ja kirjaudu',
  back: 'Takaisin kirjautumiseen',
  unsupportedPasskey: 'Selaimesi ei tue passkey-kirjautumista.',
  loginSuccess: 'Kirjautuminen onnistui! Ohjataan…',
  invalidCredentials: 'Virheellinen käyttäjänimi tai salasana',
  loginError: 'Kirjautumisessa tapahtui virhe. Yritä uudelleen.',
  unverified: 'Sähköposti on vahvistamatta. Lähetimme uuden vahvistuslinkin.',
  throttled: 'Liian monta epäonnistunutta kirjautumisyritystä. Yritä uudelleen 15 minuutin kuluttua.',
  passkeyOptionsFailed: 'Passkey-asetuksia ei saatu haettua',
  passkeyFailed: 'Kirjautuminen passkeyllä epäonnistui',
};

/** Same-origin path or "/". Defence in depth: Flask already normalises its own redirects. */
export function safeRedirect(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')
    || /[\u0000-\u001f\u007f]/.test(value)) return '/';
  return value;
}

/**
 * Where the forms post: the login URL with only `next` and `popup` kept. `next` is always
 * present (default "/") so Flask never falls back to a target stored by an older sign-in.
 */
export function loginFormAction(search: string): string {
  const query = new URLSearchParams(search);
  const out = new URLSearchParams();
  out.set('next', query.get('next') || '/');
  if (query.get('popup') === '1') out.set('popup', '1');
  return `${loginPath}?${out}`;
}

/** The `next` sent with a passkey sign-in; Flask normalises it like the password POST. */
export function loginNext(search: string): string {
  return new URLSearchParams(search).get('next') || '/';
}

/** Flask adds mfa=1 when a verified first factor waits for its code (it still checks the session). */
export function pendingMfa(search: string): boolean {
  return new URLSearchParams(search).get('mfa') === '1';
}

/**
 * A signed-in visitor goes back to Flask's own login GET (Apache leaves legacy=1 there), which
 * redirects to the normalised `next` exactly as before.
 */
export function loginLegacyLocation(search: string): string {
  const query = new URLSearchParams(search);
  query.delete('legacy');
  query.delete('mfa');
  query.set('legacy', '1');
  return `${loginPath}?${query}`;
}

export type MfaCheck = 'mfa' | 'valid' | 'invalid' | 'throttled' | 'unverified' | 'error';

/** Reads Flask's /2fa_check answer: {enabled, valid, throttled?, unverified?}. */
export function classifyMfaCheck(status: number, value: unknown): MfaCheck {
  if (!value || typeof value !== 'object') return 'error';
  const data = value as Record<string, unknown>;
  if (data.throttled === true || status === 429) return 'throttled';
  if (data.unverified === true) return 'unverified';
  if (data.enabled === true) return 'mfa';
  if (data.valid === true) return 'valid';
  if (status >= 500) return 'error';
  return 'invalid';
}

function bufferToB64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlToBuffer(value: string): ArrayBuffer {
  const base = value.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(base + '='.repeat((4 - (base.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** The server sends base64url strings; WebAuthn wants buffers. */
export function decodeRequestOptions(options: unknown): PublicKeyCredentialRequestOptions {
  const copy = JSON.parse(JSON.stringify(options ?? {})) as Record<string, unknown>;
  if (typeof copy.challenge === 'string') copy.challenge = b64urlToBuffer(copy.challenge);
  if (Array.isArray(copy.allowCredentials)) {
    copy.allowCredentials = copy.allowCredentials.map(item => item && typeof item === 'object' && typeof (item as { id?: unknown }).id === 'string'
      ? { ...(item as object), id: b64urlToBuffer((item as { id: string }).id) } : item);
  }
  return copy as unknown as PublicKeyCredentialRequestOptions;
}

export function formatAssertion(credential: PublicKeyCredential) {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: credential.rawId ? bufferToB64url(credential.rawId) : credential.id,
    type: credential.type,
    response: {
      clientDataJSON: bufferToB64url(response.clientDataJSON),
      authenticatorData: bufferToB64url(response.authenticatorData),
      signature: bufferToB64url(response.signature),
      userHandle: response.userHandle ? bufferToB64url(response.userHandle) : null,
    },
  };
}

async function postJson(url: string, body: unknown, fetcher: typeof fetch): Promise<Record<string, unknown>> {
  const response = await fetcher(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => null);
  return data && typeof data === 'object' ? data as Record<string, unknown> : {};
}

/** The login half of static/js/webauthn.js, returning a same-origin redirect. */
export async function signInWithPasskey(username: string, next: string,
  extra: { mediation?: CredentialMediationRequirement; signal?: AbortSignal } = {}, fetcher: typeof fetch = fetch): Promise<string> {
  const options = await postJson(loginApi.passkeyOptions, { username }, fetcher);
  if (options.status !== 'success') throw new Error(typeof options.message === 'string' ? options.message : loginCopy.passkeyOptionsFailed);
  const request: CredentialRequestOptions = { publicKey: decodeRequestOptions(options.options) };
  if (extra.mediation) request.mediation = extra.mediation;
  if (extra.signal) request.signal = extra.signal;
  const assertion = await navigator.credentials.get(request) as PublicKeyCredential | null;
  if (!assertion) throw Object.assign(new Error(loginCopy.passkeyFailed), { name: 'NotAllowedError' });
  const verified = await postJson(loginApi.passkeyVerify, { credential: formatAssertion(assertion), next }, fetcher);
  if (verified.status !== 'success') throw new Error(typeof verified.message === 'string' ? verified.message : loginCopy.passkeyFailed);
  return safeRedirect(verified.redirect ?? '/');
}
