import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { fetchStepUpMethods, runWithStepUp, stepUpCopy as copy, stepUpWithPasskey, stepUpWithPassword } from './account-stepup';
import type { StepUpMethods, StepUpResult } from './account-stepup';
import './account-settings/account-settings.css';

/**
 * Re-authentication dialog on the native dialog element: focus moves inside, Escape and Peruuta
 * cancel (`useStepUp` returns focus to the control that started the request). Errors show inside
 * the dialog. `finish(true)` means the
 * session is elevated and the caller may retry its request.
 */
export function StepUpDialog({ finish }: { finish: (ok: boolean) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const ids = { password: useId(), totp: useId() };
  const passwordField = useRef<HTMLInputElement>(null);
  const [methods, setMethods] = useState<StepUpMethods | null>(null);
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    ref.current?.showModal();
    return () => { ref.current?.close(); };
  }, []);
  useEffect(() => {
    let live = true;
    void fetchStepUpMethods(typeof window.PublicKeyCredential !== 'undefined').then(found => {
      if (!live) return;
      if (!found) setError(copy.optionsFailed);
      setMethods(found ?? { passkey: false, password: true, requiresTotp: false });
    });
    return () => { live = false; };
  }, []);

  const settle = (result: StepUpResult) => {
    setBusy(false);
    if (result.ok) { setPassword(''); setTotp(''); finish(true); return; }
    // A wrong password is never kept in the field: clear it and let the user type again.
    setPassword(''); setTotp(''); setError(result.message);
    passwordField.current?.focus();
  };
  const usePasskey = async () => { setBusy(true); setError(''); settle(await stepUpWithPasskey()); };
  const usePassword = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError('');
    settle(await stepUpWithPassword(password, totp));
  };

  return <dialog ref={ref} className="detail-action-dialog account-stepup" aria-labelledby={heading} aria-busy={busy}
    onCancel={event => { event.preventDefault(); finish(false); }}>
    <h2 id={heading}>{copy.title}</h2>
    <p className="account-muted">{copy.lead}</p>
    <div aria-live="polite">{error && <div className="flash-message flash-message--inline flash-error" role="alert"><span className="flash-text">{error}</span></div>}</div>
    {methods?.passkey && <div className="detail-actions">
      <button type="button" className="detail-button detail-button--primary" disabled={busy} onClick={usePasskey}>{copy.passkey}</button>
    </div>}
    {methods && <form className="suggest-change-form" onSubmit={usePassword}>
      <div className="suggest-change-field">
        <label htmlFor={ids.password}>{copy.password}</label>
        <input id={ids.password} ref={passwordField} type="password" autoComplete="current-password" placeholder={copy.passwordPlaceholder} required
          autoFocus={!methods.passkey} value={password} onChange={event => setPassword(event.target.value)} />
      </div>
      {methods.requiresTotp && <div className="suggest-change-field">
        <label htmlFor={ids.totp}>{copy.totp}</label>
        <input id={ids.totp} type="text" inputMode="numeric" maxLength={6} pattern="[0-9]{6}" autoComplete="one-time-code"
          placeholder={copy.totpPlaceholder} value={totp} onChange={event => setTotp(event.target.value)} />
      </div>}
      <div className="detail-actions">
        <button type="submit" className="detail-button" disabled={busy}>{busy ? copy.working : copy.withPassword}</button>
      </div>
    </form>}
    <div className="detail-actions">
      <button type="button" className="detail-button detail-button--danger" onClick={() => finish(false)}>{copy.cancel}</button>
    </div>
  </dialog>;
}

/**
 * `withStepUp(() => fetch(...))` runs the request, asks the user to re-authenticate if Flask
 * demands it, retries once, and resolves `null` when the user cancels. Render `dialog` once
 * anywhere in the component.
 */
export function useStepUp(): { withStepUp: (request: () => Promise<Response>) => Promise<Response | null>; dialog: ReactElement | null } {
  const [open, setOpen] = useState(false);
  const pending = useRef<((ok: boolean) => void) | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const finish = useCallback((ok: boolean) => { setOpen(false); pending.current?.(ok); pending.current = null; }, []);
  const ask = useCallback(() => new Promise<boolean>(resolve => { pending.current = resolve; setOpen(true); }), []);
  // The opener is captured before the first request: the button that started it is disabled while
  // it runs and loses focus, so reading document.activeElement later would find nothing.
  const withStepUp = useCallback(async (request: () => Promise<Response>) => {
    opener.current = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    try { return await runWithStepUp(request, ask); }
    finally {
      const target = opener.current;
      opener.current = null;
      // After the caller has re-enabled its controls and React has rendered.
      requestAnimationFrame(() => { if (target?.isConnected) target.focus({ preventScroll: true }); });
    }
  }, [ask]);
  useEffect(() => () => { pending.current?.(false); pending.current = null; }, []);
  return { withStepUp, dialog: open ? <StepUpDialog finish={finish} /> : null };
}
