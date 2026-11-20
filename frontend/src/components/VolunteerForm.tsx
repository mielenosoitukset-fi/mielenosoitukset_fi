import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

type FieldName = 'name' | 'email' | 'city' | 'area' | 'notes';

/** Keys the Flask endpoint accepts; the labels are the page's copy. */
export const volunteerRoles = [
  { key: 'stickers', label: 'Tarrojen jakaminen' },
  { key: 'pride_banner', label: 'Banderollin kantaminen Pridessa' },
  { key: 'events', label: 'Tapahtumat ja markkinointiviikko' },
  { key: 'communications', label: 'Viestintä ja somejako' },
] as const;
type Feedback = { kind: 'success' | 'error'; message: string };

export interface VolunteerFormProps {
  endpoint: string;
  messages: {
    required: string;
    invalid: string;
    success: string;
    failed: string;
    network: string;
    sending: string;
  };
}

export function VolunteerForm({ endpoint, messages }: VolunteerFormProps) {
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [invalid, setInvalid] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [roles, setRoles] = useState<string[]>([]);
  // State updates are batched: the ref also guards two submits in the same tick.
  const requestPending = useRef(false);
  const message = useRef<HTMLDivElement>(null);
  const focusMessage = useRef(false);
  const revealMessage = useRef(false);
  // Server results move focus to the message, which sits above the fields, so
  // it is in view on a phone after the visitor taps the submit button below.
  useEffect(() => {
    if (feedback && focusMessage.current) message.current?.focus();
    // A validation error keeps focus on the first invalid field but brings the
    // message, which sits above the fields, into view below the sticky nav.
    if (feedback && revealMessage.current) message.current?.scrollIntoView({ block: 'start' });
    focusMessage.current = false;
    revealMessage.current = false;
  }, [feedback]);

  function clearInvalid(event: FormEvent<HTMLFormElement>) {
    const field = event.target;
    if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
      setInvalid((previous) => previous.filter((id) => id !== field.id));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestPending.current) return;

    const form = event.currentTarget;
    const fields = Array.from(form.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea'));
    const missing = fields.filter((field) => field.required && !field.value.trim());
    const invalidFields = fields.filter((field) => missing.includes(field) || !field.validity.valid);
    setInvalid(invalidFields.map((field) => field.id));
    if (invalidFields.length) {
      revealMessage.current = true;
      setFeedback({ kind: 'error', message: missing.length ? messages.required : messages.invalid });
      invalidFields[0].focus({ preventScroll: true });
      return;
    }

    const text = Object.fromEntries(fields.filter((field) => field.type !== 'checkbox').map((field) => [field.name, field.value.trim()]));
    const payload = { ...text, roles, ...(roles.includes('stickers') ? {} : { area: '' }) };
    requestPending.current = true;
    setPending(true);
    setFeedback(null);
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(payload),
      });
      // Login redirects and non-JSON server errors are failures, never successful signups.
      const result = await response.json().catch(() => null);
      focusMessage.current = true;
      if (response.ok && result?.success === true) {
        setFeedback({ kind: 'success', message: messages.success });
        form.reset();
        setRoles([]);
      } else {
        setFeedback({
          kind: 'error',
          message: typeof result?.error === 'string' && result.error ? result.error : messages.failed,
        });
      }
    } catch {
      focusMessage.current = true;
      setFeedback({ kind: 'error', message: messages.network });
    } finally {
      requestPending.current = false;
      setPending(false);
    }
  }

  const fieldProps = (name: FieldName) => ({
    id: `vol-${name}`,
    name,
    className: 'form-control',
    'aria-invalid': invalid.includes(`vol-${name}`) ? true : undefined,
  });

  return (
    <form id="volunteer-form" method="post" action={endpoint} noValidate
      aria-describedby="vol-msg" aria-busy={pending || undefined}
      onSubmit={submit} onInput={clearInvalid}>
      <p id="vol-msg" ref={message} className={`kv-banner${feedback?.kind === 'error' ? ' kv-banner--err' : ''}`}
        role={feedback?.kind === 'error' ? 'alert' : 'status'} aria-live="polite" aria-atomic="true" tabIndex={-1} hidden={!feedback}>
        <i className={`fa-solid ${feedback?.kind === 'error' ? 'fa-circle-exclamation' : 'fa-check'}`} aria-hidden="true" />
        <span id="vol-msg-text">{feedback?.message}</span>
      </p>
      <fieldset id="vol-fields" disabled={pending}>
        <legend className="visually-hidden">Ilmoittautumistiedot</legend>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="vol-name" className="form-label">Nimi <span className="kv-req">*</span></label>
            <input {...fieldProps('name')} autoComplete="name" required aria-required="true" />
          </div>
          <div className="form-group">
            <label htmlFor="vol-email" className="form-label">Sähköposti <span className="kv-req">*</span></label>
            <input {...fieldProps('email')} type="email" autoComplete="email" required aria-required="true" />
          </div>
        </div>
        <div className="form-group">
          <label htmlFor="vol-city" className="form-label">Kaupunki <span className="kv-opt">(valinnainen)</span></label>
          <input {...fieldProps('city')} autoComplete="address-level2" />
        </div>
        <div className="form-group">
          <span className="form-label kv-block" id="vol-roles-label">Missä haluaisit auttaa? <span className="kv-opt">(valinnainen)</span></span>
          <div className="kv-roles" role="group" aria-labelledby="vol-roles-label">
            {volunteerRoles.map((role) => <label key={role.key} className="kv-chipcheck">
              <input type="checkbox" name="roles" value={role.key} checked={roles.includes(role.key)}
                onChange={(event) => setRoles((previous) => event.target.checked ? [...previous, role.key] : previous.filter((key) => key !== role.key))} />
              <span>{role.label}</span>
            </label>)}
          </div>
        </div>
        {roles.includes('stickers') && <div className="form-group">
          <label htmlFor="vol-area" className="form-label">Alue, jossa jakaisit tarroja <span className="kv-opt">(valinnainen)</span></label>
          <input {...fieldProps('area')} placeholder="Esimerkiksi Tampereen keskusta" />
          <p className="kv-small kv-note">Sovimme jakamisesta yhdessä ja otamme sinuun yhteyttä myöhemmin. Tämä ei ole lupaus tarrojen toimituksesta tai aikataulusta.</p>
        </div>}
        <details className="kv-more">
          <summary>Haluatko kertoa jotain lisää?</summary>
          <div><textarea {...fieldProps('notes')} rows={3} aria-label="Lisätiedot" /></div>
        </details>
        <button className="btn btn-primary" id="vol-submit" type="submit" disabled={pending} aria-busy={pending || undefined}>
          <span id="vol-submit-label">{pending ? messages.sending : 'Lähetä ilmoittautuminen'}</span>
        </button>
      </fieldset>
    </form>
  );
}
