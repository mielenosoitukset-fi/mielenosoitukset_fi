import { useEffect, useRef, useState } from 'react';
import type { AppConfig } from './config';
import { FlashNotices, useFlashNotices } from './flash-notices';
import { classifyMfaCheck, loginApi, loginCopy as copy, loginFormAction, loginNext, pendingMfa, signInWithPasskey } from './login-route';
import './information/information.css';
import './auth-pages/auth-pages.css';

type Message = { kind: 'error' | 'success'; text: string } | null;

const quiet = (error: unknown) => error instanceof Error && (error.name === 'NotAllowedError' || error.name === 'AbortError');

/**
 * Login page. Forms post to the unchanged Flask handler; the password is checked first through
 * /2fa_check so an MFA account gets its code step without a second page load, as before.
 * Without JavaScript the forms still post; Flask's MFA redirect adds mfa=1 for the code step.
 */
export default function LoginScreen({ config, search }: { config: AppConfig; search: string }) {
  const notices = useFlashNotices();
  const action = loginFormAction(search);
  const next = loginNext(search);
  const [step, setStep] = useState<'password' | 'mfa'>(() => pendingMfa(search) ? 'mfa' : 'password');
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<'password' | 'passkey' | 'mfa' | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [code, setCode] = useState('');
  const loginForm = useRef<HTMLFormElement>(null);
  const mfaForm = useRef<HTMLFormElement>(null);
  const conditional = useRef<AbortController | null>(null);
  const resendDialog = useRef<HTMLDialogElement>(null);

  const stopConditional = () => { conditional.current?.abort(); conditional.current = null; };
  const finish = (redirect: string) => { setMessage({ kind: 'success', text: copy.loginSuccess }); window.location.assign(redirect); };

  // Browser autofill offers saved passkeys in the username field (conditional mediation).
  useEffect(() => {
    if (step !== 'password' || typeof window === 'undefined' || !window.PublicKeyCredential
      || typeof PublicKeyCredential.isConditionalMediationAvailable !== 'function') return;
    let active = true;
    void PublicKeyCredential.isConditionalMediationAvailable().then(async available => {
      if (!available || !active) return;
      const controller = new AbortController();
      conditional.current = controller;
      try {
        finish(await signInWithPasskey('', next, { mediation: 'conditional', signal: controller.signal }));
      } catch (error) {
        if (!quiet(error)) console.warn('Conditional passkey sign-in unavailable');
      } finally {
        if (conditional.current === controller) conditional.current = null;
      }
    }).catch(() => {});
    return () => { active = false; stopConditional(); };
  }, [step, next]);

  useEffect(() => { if (step === 'mfa') document.getElementById('login-mfa-code')?.focus(); }, [step]);

  async function onPassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    stopConditional();
    setBusy('password');
    setMessage(null);
    const form = event.currentTarget;
    try {
      const response = await fetch(loginApi.mfaCheck, { method: 'POST', body: new FormData(form), credentials: 'same-origin',
        cache: 'no-store', headers: { Accept: 'application/json' } });
      const result = classifyMfaCheck(response.status, await response.json().catch(() => null));
      if (result === 'valid') {
        setMessage({ kind: 'success', text: copy.loginSuccess });
        form.submit();
        return;
      }
      if (result === 'mfa') {
        (form.elements.namedItem('password') as HTMLInputElement).value = '';
        setStep('mfa');
      } else {
        setMessage({ kind: 'error', text: result === 'throttled' ? copy.throttled : result === 'unverified' ? copy.unverified
          : result === 'invalid' ? copy.invalidCredentials : copy.loginError });
      }
    } catch {
      setMessage({ kind: 'error', text: copy.loginError });
    }
    setBusy(null);
  }

  async function onPasskey() {
    if (!window.PublicKeyCredential) { setMessage({ kind: 'error', text: copy.unsupportedPasskey }); return; }
    stopConditional();
    setBusy('passkey');
    setMessage(null);
    try {
      const username = (loginForm.current?.elements.namedItem('username') as HTMLInputElement | null)?.value.trim() ?? '';
      finish(await signInWithPasskey(username, next));
    } catch (error) {
      if (!quiet(error)) setMessage({ kind: 'error', text: error instanceof Error && error.message ? error.message : copy.loginError });
      setBusy(null);
    }
  }

  function onCode(value: string) {
    const digits = value.replace(/[^0-9]/g, '').slice(0, 6);
    setCode(digits);
    if (digits.length === 6 && busy !== 'mfa') { setBusy('mfa'); queueMicrotask(() => mfaForm.current?.requestSubmit()); }
  }

  function backToPassword() {
    setStep('password');
    setCode('');
    setMessage(null);
    setBusy(null);
    queueMicrotask(() => document.getElementById('login-username')?.focus());
  }

  const status = message && <div className={`flash-message flash-message--inline flash-${message.kind}`}
    role={message.kind === 'error' ? 'alert' : 'status'}><span className="flash-text">{message.text}</span></div>;

  return <article className="public-page-shell information-page auth-page login-page" aria-labelledby="login-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="login-title">{step === 'mfa' ? copy.mfaTitle : copy.title}</h1>
      <p className="public-page-header__description">{step === 'mfa' ? copy.mfaLead : copy.lead}</p>
    </div></header>
    <FlashNotices notices={notices} />
    {status}
    <section className="public-prose-card auth-card" hidden={step !== 'password'} aria-labelledby="login-title">
      <button type="button" className="detail-button detail-button--primary login-passkey" onClick={onPasskey}
        disabled={busy !== null} aria-describedby="login-passkey-hint">{copy.passkey}</button>
      <p className="auth-hint" id="login-passkey-hint">{copy.passkeyHint}</p>
      <p className="login-divider"><span>{copy.divider}</span></p>
      <form ref={loginForm} className="auth-form" action={action} method="post" onSubmit={onPassword}>
        <label htmlFor="login-username">{copy.username}</label>
        <input type="text" id="login-username" name="username" required autoComplete="username webauthn"
          placeholder={copy.usernamePlaceholder} autoCapitalize="none" spellCheck={false} />
        <label htmlFor="login-password">{copy.password}</label>
        <div className="login-password">
          <input type={showPassword ? 'text' : 'password'} id="login-password" name="password" required
            autoComplete="current-password" placeholder={copy.passwordPlaceholder} />
          <button type="button" className="login-password__toggle" aria-pressed={showPassword}
            aria-label={showPassword ? copy.hidePassword : copy.showPassword} onClick={() => setShowPassword(value => !value)}>
            {showPassword ? copy.hidePassword : copy.showPassword}
          </button>
        </div>
        <div className="login-row">
          <label className="login-remember"><input type="checkbox" name="remember" /> {copy.remember}</label>
          <a href={loginApi.passwordReset}>{copy.forgot}</a>
        </div>
        <div className="detail-actions">
          <button type="submit" className="detail-button detail-button--primary" disabled={busy !== null}>{copy.submit}</button>
        </div>
      </form>
    </section>
    <section className="public-prose-card auth-card" hidden={step !== 'mfa'} aria-labelledby="login-title">
      <form ref={mfaForm} className="auth-form" action={action} method="post" onSubmit={() => setBusy('mfa')}>
        <label htmlFor="login-mfa-code">{copy.mfaLabel}</label>
        <input type="text" id="login-mfa-code" name="2fa_code" placeholder="000000" maxLength={6} pattern="[0-9]{6}"
          inputMode="numeric" autoComplete="one-time-code" required={step === 'mfa'} value={code}
          onChange={event => onCode(event.target.value)} aria-describedby="login-mfa-hint" />
        <p className="auth-hint" id="login-mfa-hint">{copy.mfaHint}</p>
        <div className="detail-actions">
          <button type="submit" className="detail-button detail-button--primary" disabled={busy === 'mfa'}>{copy.mfaSubmit}</button>
          <button type="button" className="detail-button" onClick={backToPassword}>{copy.back}</button>
        </div>
      </form>
    </section>
    <section className="public-prose-card auth-card auth-card--secondary" hidden={step !== 'password'} aria-label={copy.helpTitle}>
      <p>{copy.helpTitle}</p>
      <div className="detail-actions">
        <button type="button" className="detail-button" onClick={() => resendDialog.current?.showModal()}>{copy.resend}</button>
        <a className="detail-button" href={config.urls.register}>{copy.register}</a>
      </div>
    </section>
    <dialog ref={resendDialog} className="login-dialog" aria-labelledby="login-resend-title" aria-describedby="login-resend-lead"
      onClick={event => { if (event.target === event.currentTarget) event.currentTarget.close(); }}>
      <div className="login-dialog__body">
        <h2 id="login-resend-title">{copy.resend}</h2>
        <p id="login-resend-lead">{copy.resendLead}</p>
        <form className="auth-form" action={loginApi.resendConfirmation} method="post">
          <label htmlFor="login-resend-identity">{copy.resendLabel}</label>
          <input type="text" id="login-resend-identity" name="email_or_username" required autoComplete="username" />
          <div className="detail-actions">
            <button type="submit" className="detail-button detail-button--primary">{copy.resendSubmit}</button>
            <button type="button" className="detail-button" onClick={() => resendDialog.current?.close()}>{copy.close}</button>
          </div>
        </form>
        <p className="auth-hint">{copy.resendHint}</p>
      </div>
    </dialog>
  </article>;
}
