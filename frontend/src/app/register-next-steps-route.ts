// Page after registering, GET /users/auth/register/next_steps. Only the page moves to React.
// Flask keeps the address this browser registered with in its session and answers
// GET /users/auth/api/register/status from that session only; resending the link stays in
// Flask's /users/auth/resend_confirmation, which caps resends per account.
export const nextStepsPath = '/users/auth/register/next_steps';
export const registerStatusApi = '/users/auth/api/register/status';
export const resendConfirmationPath = '/users/auth/resend_confirmation';
// Poll every 5 s for at most 15 minutes; the visitor can reload the page after that.
export const pollIntervalMs = 5000;
export const pollLimit = 180;

export const nextStepsCopy = {
  title: 'Kiitos rekisteröitymisestäsi!',
  lead: 'Tilisi on luotu. Vahvista vielä sähköpostiosoitteesi, niin voit kirjautua sisään.',
  stepsLabel: 'Rekisteröitymisen vaiheet',
  created: 'Tili luotu',
  createdText: 'Tilisi on luotu onnistuneesti.',
  confirm: 'Vahvista sähköpostiosoitteesi',
  confirmText: 'Lähetimme sinulle vahvistusviestin. Avaa viesti ja valitse siinä oleva linkki.',
  sentTo: 'Vahvistus lähetettiin osoitteeseen',
  ready: 'Valmis',
  readyText: 'Kun sähköpostisi on vahvistettu, voit kirjautua sisään ja aloittaa palvelun käytön.',
  profile: 'Viimeistele profiilisi halutessasi',
  profileText: 'Voit täydentää profiilisi tietoja myöhemmin. Se ei ole pakollista.',
  done: 'valmis',
  current: 'nykyinen vaihe',
  todo: 'tulossa',
  verified: 'Sähköpostisi on vahvistettu! Voit nyt kirjautua sisään.',
  login: 'Kirjaudu sisään',
  helpTitle: 'Eikö viestiä kuulunut?',
  helpText: 'Tarkista roskapostikansio. Voit myös pyytää uuden vahvistuslinkin.',
  resendField: 'Sähköposti tai käyttäjätunnus',
  resend: 'Lähetä vahvistusviesti uudelleen',
  resendFailed: 'Vahvistusviestin lähetys epäonnistui. Yritä hetken kuluttua uudelleen.',
  network: 'Verkkovirhe. Yritä uudelleen.',
  support: 'Tarvitsetko apua?',
  contact: 'Ota yhteyttä tukeen',
};

export type RegistrationStatus = { email: string | null; verified: boolean } | null;

/** Reads `{email, verified}`; anything else counts as unknown (null). */
export function readRegistrationStatus(status: number, body: unknown): RegistrationStatus {
  if (status !== 200 || !body || typeof body !== 'object') return null;
  const { email, verified } = body as { email?: unknown; verified?: unknown };
  if (typeof verified !== 'boolean') return null;
  return { email: typeof email === 'string' && email ? email : null, verified };
}

/** Reads the resend answer: {status: 'success', message}. Returns the message and whether it worked. */
export function readResend(status: number, body: unknown): { ok: boolean; message: string } {
  const data = body && typeof body === 'object' ? body as { status?: unknown; message?: unknown } : {};
  const message = typeof data.message === 'string' && data.message ? data.message : '';
  const ok = status === 200 && data.status === 'success';
  return { ok, message: message || (ok ? '' : nextStepsCopy.resendFailed) };
}
