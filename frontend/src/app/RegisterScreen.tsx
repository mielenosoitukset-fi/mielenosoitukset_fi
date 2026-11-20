import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppConfig } from './config';
import { FlashNotices, useFlashNotices } from './flash-notices';
import {
  generatePassword, normalizeUsername, passwordChecks, readUsernameFree, registerCopy as copy, registerPath,
  usernameFreeApi, usernameShapeOk,
} from './register-route';
import type { UsernameState } from './register-route';
import './information/information.css';
import './auth-pages/auth-pages.css';

const usernameText: Record<UsernameState, string> = {
  idle: '', checking: copy.checking, available: copy.available, taken: copy.taken,
  invalid: copy.invalidUsername, failed: copy.checkFailed,
};

/**
 * Registration page. A plain form posting to the unchanged Flask handler, so it works without
 * JavaScript; Flask validates again, creates the account, mails the link and redirects to the
 * next-steps page (or back here with a flash). The live checks only guide the visitor.
 */
export default function RegisterScreen({ config }: { config: AppConfig }) {
  const notices = useFlashNotices();
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [usernameState, setUsernameState] = useState<UsernameState>('idle');
  const [generated, setGenerated] = useState(false);
  // Counts refused submits, so a repeated refusal moves focus to the message again.
  const [error, setError] = useState(0);
  const [busy, setBusy] = useState(false);
  const errorBox = useRef<HTMLParagraphElement>(null);
  const checks = passwordChecks(password, username, email);
  const mismatch = confirm.length > 0 && confirm !== password;

  useEffect(() => {
    const name = normalizeUsername(username);
    if (!name) { setUsernameState('idle'); return; }
    if (!usernameShapeOk(name)) { setUsernameState('invalid'); return; }
    setUsernameState('checking');
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void fetch(`${usernameFreeApi}?username=${encodeURIComponent(name)}`, { credentials: 'same-origin',
        cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' } })
        .then(async response => setUsernameState(readUsernameFree(response.status, await response.json().catch(() => null))))
        .catch(() => { if (!controller.signal.aborted) setUsernameState('failed'); });
    }, 400);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [username]);

  useEffect(() => { if (error) errorBox.current?.focus(); }, [error]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    // Block only what Flask would refuse anyway; a failed availability check never blocks.
    if (!checks.ok || mismatch || confirm !== password || usernameState === 'invalid' || usernameState === 'taken') {
      event.preventDefault();
      setError(count => count + 1);
      return;
    }
    setBusy(true);
  }

  function onGenerate() {
    const value = generatePassword(bytes => crypto.getRandomValues(bytes), username, email);
    setPassword(value);
    setConfirm(value);
    setShowPassword(true);
    setGenerated(true);
  }

  const requirement = (met: boolean, text: string) => <li className={met ? 'register-req register-req--met' : 'register-req'}>
    <span aria-hidden="true">{met ? '✓' : '•'}</span> {text}<span className="register-sr-only">: {met ? copy.met : copy.notMet}</span></li>;

  return <article className="public-page-shell information-page auth-page" aria-labelledby="register-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="register-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    <FlashNotices notices={notices} />
    {error > 0 && <p ref={errorBox} tabIndex={-1} className="flash-message flash-message--inline flash-error" role="alert">
      <span className="flash-text">{copy.fixErrors}</span></p>}
    <form className="auth-form register-form" action={registerPath} method="post" onSubmit={onSubmit}>
      <section className="public-prose-card auth-card register-card" aria-labelledby="register-details-title">
        <h2 id="register-details-title">{copy.detailsTitle}</h2>
        <p className="auth-hint">{copy.detailsLead}</p>
        <label htmlFor="register-username">{copy.username}</label>
        <input type="text" id="register-username" name="username" required minLength={3} maxLength={30}
          autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder={copy.usernamePlaceholder}
          aria-describedby="register-username-hint register-username-status"
          aria-invalid={usernameState === 'invalid' || usernameState === 'taken' ? true : undefined}
          value={username} onChange={event => setUsername(event.target.value)} />
        <p className="auth-hint" id="register-username-hint">{copy.usernameHint}</p>
        <p className={`register-status register-status--${usernameState}`} id="register-username-status" aria-live="polite">
          {usernameText[usernameState]}</p>
        <label htmlFor="register-email">{copy.email}</label>
        <input type="email" id="register-email" name="email" required maxLength={254} autoComplete="email"
          placeholder={copy.emailPlaceholder} aria-describedby="register-email-hint"
          value={email} onChange={event => setEmail(event.target.value)} />
        <p className="auth-hint" id="register-email-hint">{copy.emailHint}</p>
      </section>
      <section className="public-prose-card auth-card register-card" aria-labelledby="register-password-title">
        <h2 id="register-password-title">{copy.passwordTitle}</h2>
        <p className="auth-hint">{copy.passwordLead}</p>
        <label htmlFor="register-password">{copy.password}</label>
        <div className="login-password">
          <input type={showPassword ? 'text' : 'password'} id="register-password" name="password" required minLength={12}
            autoComplete="new-password" placeholder={copy.passwordPlaceholder} aria-describedby="register-requirements"
            aria-invalid={password && !checks.ok ? true : undefined}
            value={password} onChange={event => { setPassword(event.target.value); setGenerated(false); }} />
          <button type="button" className="login-password__toggle" aria-pressed={showPassword}
            aria-controls="register-password register-confirm" onClick={() => setShowPassword(value => !value)}>{copy.showPassword}</button>
        </div>
        <div className="register-requirements" id="register-requirements">
          <p>{copy.requirements}</p>
          <ul>
            {requirement(checks.length, copy.reqLength)}
            {requirement(checks.categories, copy.reqCategories)}
            {requirement(password.length > 0 && checks.identity, copy.reqIdentity)}
          </ul>
        </div>
        <label htmlFor="register-confirm">{copy.confirm}</label>
        <input type={showPassword ? 'text' : 'password'} id="register-confirm" name="password_confirm" required
          autoComplete="new-password" placeholder={copy.confirmPlaceholder} aria-describedby="register-confirm-status"
          aria-invalid={mismatch ? true : undefined} value={confirm} onChange={event => setConfirm(event.target.value)} />
        <p className={`register-status register-status--${mismatch ? 'taken' : 'available'}`} id="register-confirm-status"
          aria-live="polite">{confirm ? (mismatch ? copy.mismatch : copy.match) : ''}</p>
        {generated && <p className="auth-hint" role="status">{copy.generated}</p>}
        <div className="detail-actions register-actions">
          <button type="button" className="detail-button" onClick={onGenerate}>{copy.generate}</button>
          <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{copy.submit}</button>
        </div>
        <p className="auth-hint">{copy.terms} <a href="/terms">{copy.termsLink}</a> {copy.and} <a href="/privacy">{copy.privacyLink}</a>.</p>
      </section>
    </form>
    <section className="public-prose-card auth-card auth-card--secondary" aria-label={copy.haveAccount}>
      <p>{copy.haveAccount}</p>
      <div className="detail-actions"><a className="detail-button" href={config.urls.login}>{copy.login}</a></div>
    </section>
  </article>;
}
