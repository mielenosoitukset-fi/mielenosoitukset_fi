import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppConfig } from './config';
import type { SessionSummary } from './session';
import { FlashNotices, useFlashNotices } from './flash-notices';
import { generatePassword, passwordChecks } from './register-route';
import { forcedPasswordCopy as copy, forcedPasswordPath } from './forced-route';
import { ForcedLogoutCard } from './ForcedLogoutCard';
import './information/information.css';
import './auth-pages/auth-pages.css';

/**
 * Required password change. A plain form posting to the unchanged Flask handler, so it works
 * without JavaScript; Flask checks the password again, logs the change and sends the visitor on.
 */
export default function ForcedPasswordScreen({ config, session }: { config: AppConfig; session: SessionSummary }) {
  const notices = useFlashNotices();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [error, setError] = useState(0);
  const [busy, setBusy] = useState(false);
  const errorBox = useRef<HTMLParagraphElement>(null);
  const checks = passwordChecks(password, '', '');
  const mismatch = confirm.length > 0 && confirm !== password;

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

  return <article className="public-page-shell information-page auth-page" aria-labelledby="forced-password-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="forced-password-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    <FlashNotices notices={notices} />
    {error > 0 && <p ref={errorBox} tabIndex={-1} className="flash-message flash-message--inline flash-error" role="alert">
      <span className="flash-text">{copy.fixErrors}</span></p>}
    <form className="auth-form register-form" action={forcedPasswordPath} method="post" noValidate onSubmit={onSubmit}>
      <section className="public-prose-card auth-card register-card">
        <label htmlFor="forced-password">{copy.password}</label>
        <div className="login-password">
          <input type={showPassword ? 'text' : 'password'} id="forced-password" name="new_password" required minLength={12}
            autoComplete="new-password" placeholder={copy.passwordPlaceholder} aria-describedby="forced-requirements"
            aria-invalid={error > 0 && !checks.ok ? true : undefined}
            value={password} onChange={event => { setPassword(event.target.value); setGenerated(false); }} />
          <button type="button" className="login-password__toggle" aria-pressed={showPassword}
            aria-controls="forced-password forced-confirm" onClick={() => setShowPassword(value => !value)}>{copy.showPassword}</button>
        </div>
        <div className="register-requirements" id="forced-requirements">
          <p>{copy.requirements}</p>
          <ul>
            {requirement(checks.length, copy.reqLength)}
            {requirement(checks.categories, copy.reqCategories)}
          </ul>
          <p className="auth-hint">{copy.reqIdentity}</p>
        </div>
        <label htmlFor="forced-confirm">{copy.confirm}</label>
        <input type={showPassword ? 'text' : 'password'} id="forced-confirm" name="confirm_password" required
          autoComplete="new-password" placeholder={copy.confirmPlaceholder} aria-describedby="forced-confirm-status"
          aria-invalid={mismatch ? true : undefined} value={confirm} onChange={event => setConfirm(event.target.value)} />
        <p className={`register-status register-status--${mismatch ? 'taken' : 'available'}`} id="forced-confirm-status"
          aria-live="polite">{confirm ? (mismatch ? copy.mismatch : copy.match) : ''}</p>
        <p className="auth-hint" role="status">{generated ? copy.generated : ''}</p>
        <div className="detail-actions register-actions">
          <button type="button" className="detail-button" onClick={onGenerate}>{copy.generate}</button>
          <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{copy.submit}</button>
        </div>
      </section>
    </form>
    <ForcedLogoutCard session={session} logoutUrl={config.urls.logout} />
  </article>;
}
