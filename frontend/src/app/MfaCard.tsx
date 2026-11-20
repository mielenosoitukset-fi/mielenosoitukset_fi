import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent, MouseEvent } from 'react';
import {
  accountCopy as copy, accountSettingsApi, classifyAccountResponse, readAccountSettings,
} from './account-settings-route';
import type { AccountMfaDevice } from './account-settings-route';
import {
  cleanDeviceName, cleanMfaCode, mfaCopy as text, mfaDeviceNameMax, mfaRenameRequest, mfaRevokeRequest, mfaStartRequest, mfaVerifyRequest,
  readMfaResult, readMfaStart,
} from './account-mfa';
import type { MfaStart } from './account-mfa';
import { useStepUp } from './StepUpDialog';
import { formatDeveloperDate } from './developer-route';

type Mfa = { enabled: boolean; devices: AccountMfaDevice[] };
type Message = { kind: 'error' | 'info'; text: string } | null;
/** Where focus goes once a form or confirmation closes: the device row's rename button, the card heading or the add button. */
type FocusTarget = { device: string } | 'heading' | 'begin';
type Mode =
  | { kind: 'idle' }
  | { kind: 'setup'; start: MfaStart }
  | { kind: 'rename'; id: string; name: string }
  | { kind: 'remove'; id: string };

const notice = (message: Message) => message && <div className={`flash-message flash-message--inline flash-${message.kind}`}
  role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</div>;

/**
 * MFA devices: set up, add, rename and remove. Flask keeps the endpoints and the rules (rename and
 * remove ask for step-up, the shared dialog handles it). After a change the list is read again from
 * the Go settings read, so what is shown is what is stored.
 */
export function MfaCard({ initial }: { initial: Mfa }) {
  const [mfa, setMfa] = useState<Mfa>(initial);
  const [mode, setMode] = useState<Mode>({ kind: 'idle' });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [code, setCode] = useState('');
  const [deviceName, setDeviceName] = useState('');
  const { withStepUp, dialog } = useStepUp();
  const ids = { title: useId(), code: useId(), name: useId(), rename: useId() };
  const codeField = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const beginButton = useRef<HTMLButtonElement>(null);
  const renameButtons = useRef(new Map<string, HTMLButtonElement>());
  const focusNext = useRef<FocusTarget | null>(null);
  // Closing a form removes the control that had focus; move it somewhere sensible instead of leaving it on the body.
  useEffect(() => {
    const target = focusNext.current;
    if (!target || busy || mode.kind !== 'idle') return;
    focusNext.current = null;
    const button = target === 'begin' ? beginButton.current : typeof target === 'object' ? renameButtons.current.get(target.device) : null;
    (button && !button.disabled ? button : heading.current)?.focus();
  });

  const refresh = async (): Promise<boolean> => {
    try {
      const response = await fetch(accountSettingsApi, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      if (classifyAccountResponse(response.status) !== 'ok') return false;
      const data = readAccountSettings(await response.json());
      if (!data) return false;
      setMfa(data.mfa);
      return true;
    } catch { return false; }
  };
  // A change is done once Flask said so; the list is only a re-read, so a failed re-read says so instead of showing stale data.
  const finish = async (ok: string, focus: FocusTarget = 'heading') => {
    focusNext.current = focus;
    setMode({ kind: 'idle' });
    setMessage((await refresh()) ? { kind: 'info', text: ok } : { kind: 'error', text: text.refreshFailed });
  };
  const close = () => {
    focusNext.current = mode.kind === 'rename' || mode.kind === 'remove' ? { device: mode.id } : 'begin';
    setMode({ kind: 'idle' }); setCode(''); setDeviceName(''); setMessage(null);
  };
  const escapeCloses = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && !busy) { event.preventDefault(); close(); }
  };

  const begin = async () => {
    setMessage(null); setBusy(true);
    let start: MfaStart | null = null; let failure: string = text.startFailed;
    try {
      const result = await readMfaResult(await mfaStartRequest(), text.startFailed);
      if (result) { start = readMfaStart(result.body); if (!start) failure = result.ok ? text.startFailed : result.message; }
    } catch { /* the fallback stands */ }
    setBusy(false);
    if (!start) { setMessage({ kind: 'error', text: failure }); return; }
    setCode(''); setDeviceName('');
    setMode({ kind: 'setup', start });
    requestAnimationFrame(() => codeField.current?.focus());
  };

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    if (mode.kind !== 'setup') return;
    const clean = cleanMfaCode(code);
    if (!clean) { setMessage({ kind: 'error', text: code.trim() ? text.verifyFailed : text.codeRequired }); return; }
    setMessage(null); setBusy(true);
    let result: Awaited<ReturnType<typeof readMfaResult>> = null;
    try { result = await readMfaResult(await mfaVerifyRequest(clean, mode.start.secret, cleanDeviceName(deviceName) ?? copy.mfaDevice), text.verifyFailed); }
    catch { result = { ok: false, message: text.verifyFailed, body: {} }; }
    setBusy(false);
    if (!result) return;
    if (!result.ok) { setCode(''); setMessage({ kind: 'error', text: result.message }); codeField.current?.focus(); return; }
    setCode(''); setDeviceName('');
    await finish(result.message);
  };

  const rename = async (event: FormEvent) => {
    event.preventDefault();
    if (mode.kind !== 'rename') return;
    const name = cleanDeviceName(mode.name);
    if (!name) { setMessage({ kind: 'error', text: text.nameRequired }); return; }
    setMessage(null); setBusy(true);
    let result: Awaited<ReturnType<typeof readMfaResult>> = null;
    // The control to refocus when the browser left focus on the body after a click (Safari).
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    try { result = await readMfaResult(await withStepUp(mfaRenameRequest(mode.id, name), submitter instanceof HTMLElement ? submitter : null), text.renameFailed); }
    catch { result = { ok: false, message: text.renameFailed, body: {} }; }
    setBusy(false);
    if (!result) return;
    if (!result.ok) { setMessage({ kind: 'error', text: result.message }); return; }
    await finish(result.message, { device: mode.id });
  };

  const remove = async (event: MouseEvent<HTMLButtonElement>) => {
    if (mode.kind !== 'remove') return;
    const button = event.currentTarget;
    setMessage(null); setBusy(true);
    let result: Awaited<ReturnType<typeof readMfaResult>> = null;
    try { result = await readMfaResult(await withStepUp(mfaRevokeRequest(mode.id), button), text.removeFailed); }
    catch { result = { ok: false, message: text.removeFailed, body: {} }; }
    setBusy(false);
    if (!result) return;
    if (!result.ok) { setMessage({ kind: 'error', text: result.message }); return; }
    await finish(result.message);
  };

  return <section className="public-prose-card account-card" aria-labelledby={ids.title}>
    <h2 id={ids.title} ref={heading} tabIndex={-1}>{copy.mfaTitle}</h2>
    <p className="account-muted">{copy.mfaLead}</p>
    <p><strong>{mfa.enabled ? copy.mfaOn : copy.mfaOff}</strong></p>
    {mfa.enabled && <ul className="account-list">
      {mfa.devices.map(device => <li key={device.id}>
        {mode.kind === 'rename' && mode.id === device.id
          ? <form className="suggest-change-form" onSubmit={rename} onKeyDown={escapeCloses}>
            <div className="suggest-change-field">
              <label htmlFor={ids.rename}>{text.renamePrompt}</label>
              <input id={ids.rename} type="text" maxLength={mfaDeviceNameMax} autoComplete="off" required autoFocus value={mode.name}
                onChange={event => setMode({ kind: 'rename', id: device.id, name: event.target.value })} />
            </div>
            <div className="detail-actions">
              <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{text.renameSave}</button>
              <button type="button" className="detail-button detail-button--danger" disabled={busy} onClick={close}>{text.cancel}</button>
            </div>
          </form>
          : <>
            <strong>{device.name || copy.mfaDevice}</strong>
            <div className="account-muted">{formatDeveloperDate(device.createdAt)}</div>
            {mode.kind === 'remove' && mode.id === device.id
              ? <div className="detail-actions" role="group" aria-label={text.removeTitle} onKeyDown={escapeCloses}>
                <span>{text.removeAsk}</span>
                <button type="button" className="detail-button detail-button--danger" disabled={busy} aria-busy={busy} onClick={remove}>{text.remove}</button>
                <button type="button" className="detail-button detail-button--danger" disabled={busy} autoFocus onClick={close}>{text.cancel}</button>
              </div>
              : <div className="detail-actions">
                <button type="button" className="detail-button" disabled={busy || mode.kind === 'setup'}
                  ref={element => { if (element) renameButtons.current.set(device.id, element); else renameButtons.current.delete(device.id); }}
                  aria-label={`${text.rename}: ${device.name || copy.mfaDevice}`}
                  onClick={() => { setMessage(null); setMode({ kind: 'rename', id: device.id, name: device.name }); }}>{text.rename}</button>
                <button type="button" className="detail-button detail-button--danger" disabled={busy || mode.kind === 'setup'}
                  aria-label={`${text.remove}: ${device.name || copy.mfaDevice}`}
                  onClick={() => { setMessage(null); setMode({ kind: 'remove', id: device.id }); }}>{text.remove}</button>
              </div>}
          </>}
      </li>)}
    </ul>}
    {mode.kind === 'setup'
      ? <form className="suggest-change-form" onSubmit={verify}>
        <h3>{text.scanTitle}</h3>
        <p>{text.scan}</p>
        <img src={mode.start.qr} alt={text.qrAlt} width={200} height={200} />
        <p className="account-muted">{text.secretHelp} <code>{mode.start.secret}</code></p>
        <div className="suggest-change-field">
          <label htmlFor={ids.name}>{text.nameLabel}</label>
          <input id={ids.name} type="text" maxLength={mfaDeviceNameMax} autoComplete="off" value={deviceName} onChange={event => setDeviceName(event.target.value)} />
        </div>
        <div className="suggest-change-field">
          <label htmlFor={ids.code}>{text.codeLabel}</label>
          <input id={ids.code} ref={codeField} type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={8} required
            placeholder={text.codePlaceholder} value={code} onChange={event => setCode(event.target.value)} />
        </div>
        <div className="detail-actions">
          <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{busy ? text.verifying : text.verify}</button>
          <button type="button" className="detail-button detail-button--danger" disabled={busy} onClick={close}>{text.cancel}</button>
        </div>
      </form>
      : <div className="detail-actions">
        <button type="button" ref={beginButton} className="detail-button detail-button--primary" disabled={busy || mode.kind !== 'idle'} aria-busy={busy} onClick={begin}>
          {busy ? text.starting : mfa.enabled ? text.addDevice : text.enable}
        </button>
      </div>}
    <div aria-live="polite">{notice(message)}</div>
    {dialog}
  </section>;
}
