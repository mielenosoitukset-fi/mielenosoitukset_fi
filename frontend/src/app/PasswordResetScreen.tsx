import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppConfig } from './config';
import { FlashNotices, useFlashNotices } from './flash-notices';
import { generatePassword, passwordChecks } from './register-route';
import { passwordResetCopy as copy, passwordResetStatusApi, readResetStatus } from './password-reset-token-route';
import type { LinkState } from './password-reset-token-route';
import './information/information.css';
import './auth-pages/auth-pages.css';

/**
 * Password reset page. Flask checks the link (and logs the visit) before the form is shown;
 * a refused link opens the request page, where Flask's flash says why. The form posts to the
 * unchanged Flask handler, so it also works without JavaScript.
 */
export default function PasswordResetScreen({ config, token }: { config: AppConfig; token: string }) {
  const notices = useFlashNotices();
  const [link, setLink] = useState<LinkState>('checking');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [error, setError] = useState(0);
  const [busy, setBusy] = useState(false);
  const errorBox = useRef<HTMLParagraphElement>(null);
  const checks = passwordChecks(password, '', '');
  const mismatch = confirm.length > 0 && confirm !== password;
  const action = `/users/auth/password_reset/${token}`;

  useEffect(() => {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 10000);
    void fetch(passwordResetStatusApi, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      signal: controller.signal, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ token }) })
      .then(async response => {
        const state = readResetStatus(response.status, await response.json().catch(() => null));
        if (state === 'refused') window.location.replace(copy.requestPath);
        setLink(state);
      })
      .catch(() => { if (timedOut || !controller.signal.aborted) setLink('unknown'); })
      .finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [token]);

  useEffect(() => { if (error) errorBox.current?.focus(); }, [error]);
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => { if (event.persisted) setBusy(false); };
    window.addEventListener('pageshow', reset);
    return () => window.removeEventListener('pageshow', reset);
  }, []);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    if (!checks.ok || confirm !== password) {
      event.preventDefault();
      setError(count => count + 1);
      return;
    }
    setBusy(true);
  }

  function onGenerate() {
    const value = generatePassword(bytes => crypto.getRandomValues(bytes));
    setPassword(value);
    setConfirm(value);
    setShowPassword(true);
    setGenerated(true);
  }

  const requirement = (met: boolean, text: string) => <li className={met ? 'register-req register-req--met'
    : error > 0 ? 'register-req register-req--missing' : 'register-req'}>
    <span aria-hidden="true">{met ? '✓' : error > 0 ? '✕' : '•'}</span> {text}<span className="register-sr-only">: {met ? copy.met : copy.notMet}</span></li>;

  return <article className="public-page-shell information-page auth-page" aria-labelledby="reset-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="reset-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    <FlashNotices notices={notices} />
    {error > 0 && <p ref={errorBox} tabIndex={-1} className="flash-message flash-message--inline flash-error" role="alert">
      <span className="flash-text">{copy.fixErrors}</span></p>}
    <p className="auth-hint" role="status">{link === 'checking' ? copy.checking : link === 'unknown' ? copy.checkFailed : ''}</p>
    <form className="auth-form register-form" action={action} method="post" onSubmit={onSubmit} hidden={link === 'refused'}>
      <section className="public-prose-card auth-card register-card" aria-labelledby="reset-title">
        <label htmlFor="reset-password">{copy.password}</label>
        <div className="login-password">
          <input type={showPassword ? 'text' : 'password'} id="reset-password" name="password" required minLength={12}
            autoComplete="new-password" placeholder={copy.passwordPlaceholder} aria-describedby="reset-requirements"
            aria-invalid={error > 0 && !checks.ok ? true : undefined}
            value={password} onChange={event => { setPassword(event.target.value); setGenerated(false); }} />
          <button type="button" className="login-password__toggle" aria-pressed={showPassword}
            aria-controls="reset-password reset-confirm" onClick={() => setShowPassword(value => !value)}>{copy.showPassword}</button>
        </div>
        <div className="register-requirements" id="reset-requirements">
          <p>{copy.requirements}</p>
          <ul>
            {requirement(checks.length, copy.reqLength)}
            {requirement(checks.categories, copy.reqCategories)}
          </ul>
          <p className="auth-hint">{copy.reqIdentity}</p>
        </div>
        <label htmlFor="reset-confirm">{copy.confirm}</label>
        <input type={showPassword ? 'text' : 'password'} id="reset-confirm" name="password_confirm" required
          autoComplete="new-password" placeholder={copy.confirmPlaceholder} aria-describedby="reset-confirm-status"
          aria-invalid={mismatch ? true : undefined} value={confirm} onChange={event => setConfirm(event.target.value)} />
        <p className={`register-status register-status--${mismatch ? 'taken' : 'available'}`} id="reset-confirm-status"
          aria-live="polite">{confirm ? (mismatch ? copy.mismatch : copy.match) : ''}</p>
        <p className="auth-hint" role="status">{generated ? copy.generated : ''}</p>
        <div className="detail-actions register-actions">
          <button type="button" className="detail-button" onClick={onGenerate}>{copy.generate}</button>
          <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{copy.submit}</button>
        </div>
      </section>
    </form>
    <section className="public-prose-card auth-card auth-card--secondary" aria-label={copy.remembered}>
      <p>{copy.remembered}</p>
      <div className="detail-actions"><a className="detail-button" href={config.urls.login}>{copy.backToLogin}</a></div>
    </section>
  </article>;
}
