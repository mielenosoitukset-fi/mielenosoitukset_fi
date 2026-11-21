import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppConfig } from './config';
import { FlashNotices, useFlashNotices } from './flash-notices';
import {
  nextStepsCopy as copy, pollIntervalMs, pollLimit, readRegistrationStatus, readResend, registerStatusApi,
  resendConfirmationPath,
} from './register-next-steps-route';
import type { RegistrationStatus } from './register-next-steps-route';
import './information/information.css';
import './auth-pages/auth-pages.css';

type Step = { title: string; text: string; state: 'done' | 'current' | 'todo' };

/**
 * Page after registering. Flask answers from this browser's session which address was used
 * and whether it is confirmed; the page polls that until the link is opened. The resend form
 * is a plain POST to Flask, so it also works without JavaScript.
 */
export default function RegisterNextStepsScreen({ config }: { config: AppConfig }) {
  const notices = useFlashNotices();
  const [status, setStatus] = useState<RegistrationStatus>(null);
  const [identity, setIdentity] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const edited = useRef(false);
  const verified = status?.verified === true;

  useEffect(() => {
    let rounds = 0;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function poll() {
      rounds += 1;
      try {
        const response = await fetch(registerStatusApi, { credentials: 'same-origin', cache: 'no-store',
          signal: controller.signal, headers: { Accept: 'application/json' } });
        const next = readRegistrationStatus(response.status, await response.json().catch(() => null));
        if (stopped) return;
        if (next) {
          setStatus(next);
          if (next.email && !edited.current) setIdentity(next.email);
          // Nothing to wait for: confirmed already, or this browser has no registration.
          if (next.verified || !next.email) return;
        }
      } catch { if (stopped) return; }
      if (rounds < pollLimit && document.visibilityState !== 'hidden') timer = setTimeout(poll, pollIntervalMs);
      else if (rounds < pollLimit) document.addEventListener('visibilitychange', resume, { once: true });
    }
    function resume() { if (!stopped) void poll(); }
    void poll();
    return () => {
      stopped = true; controller.abort(); clearTimeout(timer);
      document.removeEventListener('visibilitychange', resume);
    };
  }, []);

  async function onResend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setResult(null);
    try {
      const response = await fetch(resendConfirmationPath, { method: 'POST', credentials: 'same-origin',
        body: new FormData(event.currentTarget), headers: { 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' } });
      setResult(readResend(response.status, await response.json().catch(() => null)));
    } catch {
      setResult({ ok: false, message: copy.network });
    } finally {
      setBusy(false);
    }
  }

  const steps: Step[] = [
    { title: copy.created, text: copy.createdText, state: 'done' },
    { title: copy.confirm, text: copy.confirmText, state: verified ? 'done' : 'current' },
    { title: copy.ready, text: verified ? copy.readyTextVerified : copy.readyText, state: verified ? 'current' : 'todo' },
    { title: copy.profile, text: copy.profileText, state: 'todo' },
  ];

  return <article className="public-page-shell information-page auth-page" aria-labelledby="next-steps-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="next-steps-title">{copy.title}</h1>
      <p className="public-page-header__description">{verified ? copy.leadVerified : copy.lead}</p>
    </div></header>
    <FlashNotices notices={notices} />
    <div className="next-steps-live" role="status">{verified && <p className="flash-message flash-message--inline flash-success">
      <span className="flash-text">{copy.verified}</span></p>}</div>
    <section className="public-prose-card auth-card" aria-label={copy.stepsLabel}>
      <ol className="next-steps">
        {steps.map(step => <li key={step.title} className={`next-steps__step next-steps__step--${step.state}`}
          aria-current={step.state === 'current' ? 'step' : undefined}>
          <span className="next-steps__marker" aria-hidden="true">{step.state === 'done' ? '✓' : ''}</span>
          <div>
            <h2>{step.title}<span className="register-sr-only"> ({copy[step.state]})</span></h2>
            <p>{step.text}</p>
            {step.title === copy.confirm && status?.email && <p className="auth-hint">
              {copy.sentTo} <strong className="next-steps__email">{status.email}</strong></p>}
          </div>
        </li>)}
      </ol>
      <div className="detail-actions"><a className={verified ? 'detail-button detail-button--primary' : 'detail-button'}
        href={config.urls.login}>{copy.login}</a></div>
    </section>
    {!verified && <section className="public-prose-card auth-card" aria-labelledby="next-steps-help">
      <h2 id="next-steps-help" className="next-steps__help-title">{copy.helpTitle}</h2>
      <p>{copy.helpText}</p>
      <form className="auth-form" action={resendConfirmationPath} method="post" onSubmit={onResend}>
        <label htmlFor="resend-identity">{copy.resendField}</label>
        <input id="resend-identity" name="email_or_username" type="text" required maxLength={254}
          autoComplete="email" autoCapitalize="none" spellCheck={false} value={identity}
          onChange={event => { edited.current = true; setIdentity(event.target.value); }} />
        <p className={`register-status register-status--${result?.ok ? 'available' : 'taken'}`} role="status">
          {result?.message ?? ''}</p>
        <div className="detail-actions">
          <button type="submit" className="detail-button" disabled={busy} aria-busy={busy}>{copy.resend}</button>
        </div>
      </form>
    </section>}
    <section className="public-prose-card auth-card auth-card--secondary" aria-label={copy.support}>
      <p>{copy.support}</p>
      <div className="detail-actions"><a className="detail-button" href="/contact">{copy.contact}</a></div>
    </section>
  </article>;
}
