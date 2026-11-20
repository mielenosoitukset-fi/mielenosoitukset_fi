import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent, MouseEvent } from 'react';
import { accountCopy as copy, accountSettingsApi, classifyAccountResponse, readAccountSettings } from './account-settings-route';
import type { AccountPasskey } from './account-settings-route';
import { cleanDeviceName, mfaDeviceNameMax } from './account-mfa';
import {
  passkeyCopy as text, passkeyDeleteRequest, passkeyRenameRequest, readPasskeyResult, registerPasskey,
} from './account-passkeys';
import { useStepUp } from './StepUpDialog';
import { formatDeveloperDate } from './developer-route';

type Message = { kind: 'error' | 'info'; text: string } | null;
/** Where focus goes once a form or confirmation closes: the passkey row's rename button, the card heading or the add button. */
type FocusTarget = { key: string } | 'heading' | 'add';
type Mode = { kind: 'idle' } | { kind: 'rename'; id: string; name: string } | { kind: 'remove'; id: string };

const notice = (message: Message) => message && <div className={`flash-message flash-message--inline flash-${message.kind}`}
  role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</div>;

/**
 * Passkeys: add, rename and remove. Flask keeps the endpoints and the rules (adding starts with a step-up
 * check, rename and remove ask for step-up; the shared dialog handles it). After a change the list is read
 * again from the Go settings read, so what is shown is what is stored.
 */
export function PasskeysCard({ initial }: { initial: AccountPasskey[] }) {
  const [keys, setKeys] = useState<AccountPasskey[]>(initial);
  const [mode, setMode] = useState<Mode>({ kind: 'idle' });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [name, setName] = useState('');
  const { withStepUp, dialog } = useStepUp();
  const ids = { title: useId(), name: useId(), rename: useId() };
  const heading = useRef<HTMLHeadingElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const renameButtons = useRef(new Map<string, HTMLButtonElement>());
  const focusNext = useRef<FocusTarget | null>(null);
  // Closing a form removes the control that had focus; move it somewhere sensible instead of leaving it on the body.
  useEffect(() => {
    const target = focusNext.current;
    if (!target || busy || mode.kind !== 'idle') return;
    focusNext.current = null;
    const button = target === 'add' ? addButton.current : typeof target === 'object' ? renameButtons.current.get(target.key) : null;
    (button && !button.disabled ? button : heading.current)?.focus();
  });

  const refresh = async (): Promise<boolean> => {
    try {
      const response = await fetch(accountSettingsApi, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      if (classifyAccountResponse(response.status) !== 'ok') return false;
      const data = readAccountSettings(await response.json());
      if (!data) return false;
      setKeys(data.passkeys);
      return true;
    } catch { return false; }
  };
  const finish = async (ok: string, focus: FocusTarget = 'heading') => {
    focusNext.current = focus;
    setMode({ kind: 'idle' });
    setMessage((await refresh()) ? { kind: 'info', text: ok } : { kind: 'error', text: text.refreshFailed });
  };
  const close = () => {
    focusNext.current = mode.kind === 'rename' || mode.kind === 'remove' ? { key: mode.id } : 'heading';
    setMode({ kind: 'idle' }); setMessage(null);
  };
  const escapeCloses = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && !busy) { event.preventDefault(); close(); }
  };

  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (typeof window.PublicKeyCredential === 'undefined') { setMessage({ kind: 'error', text: text.unsupported }); return; }
    // The control to refocus when the browser left focus on the body after a click (Safari).
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const opener = submitter instanceof HTMLElement ? submitter : null;
    setMessage(null); setBusy(true);
    const result = await registerPasskey(cleanDeviceName(name) ?? '', request => withStepUp(request, opener));
    setBusy(false);
    if (!result) return;
    if (!result.ok) { setMessage({ kind: 'error', text: result.message }); return; }
    setName('');
    await finish(result.message, 'add');
  };

  const rename = async (event: FormEvent) => {
    event.preventDefault();
    if (mode.kind !== 'rename') return;
    const next = cleanDeviceName(mode.name);
    if (!next) { setMessage({ kind: 'error', text: text.nameRequired }); return; }
    setMessage(null); setBusy(true);
    let result: Awaited<ReturnType<typeof readPasskeyResult>> = null;
    // The control to refocus when the browser left focus on the body after a click (Safari).
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const opener = submitter instanceof HTMLElement ? submitter : null;
    try { result = await readPasskeyResult(await withStepUp(passkeyRenameRequest(mode.id, next), opener), text.renameFailed); }
    catch { result = { ok: false, message: text.renameFailed }; }
    setBusy(false);
    if (!result) return;
    if (!result.ok) { setMessage({ kind: 'error', text: result.message }); return; }
    await finish(result.message, { key: mode.id });
  };

  const remove = async (event: MouseEvent<HTMLButtonElement>) => {
    if (mode.kind !== 'remove') return;
    const button = event.currentTarget;
    setMessage(null); setBusy(true);
    let result: Awaited<ReturnType<typeof readPasskeyResult>> = null;
    try { result = await readPasskeyResult(await withStepUp(passkeyDeleteRequest(mode.id), button), text.removeFailed); }
    catch { result = { ok: false, message: text.removeFailed }; }
    setBusy(false);
    if (!result) return;
    if (!result.ok) { setMessage({ kind: 'error', text: result.message }); return; }
    await finish(result.message);
  };

  return <section className="public-prose-card account-card" aria-labelledby={ids.title}>
    <h2 id={ids.title} ref={heading} tabIndex={-1}>{copy.passkeysTitle}</h2>
    <p className="account-muted">{copy.passkeysLead}</p>
    {keys.length ? <ul className="account-list">
      {keys.map(key => <li key={key.id}>
        {mode.kind === 'rename' && mode.id === key.id
          ? <form className="suggest-change-form" onSubmit={rename} onKeyDown={escapeCloses}>
            <div className="suggest-change-field">
              <label htmlFor={ids.rename}>{text.renamePrompt}</label>
              <input id={ids.rename} type="text" maxLength={mfaDeviceNameMax} autoComplete="off" required autoFocus value={mode.name}
                onChange={event => setMode({ kind: 'rename', id: key.id, name: event.target.value })} />
            </div>
            <div className="detail-actions">
              <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{text.renameSave}</button>
              <button type="button" className="detail-button detail-button--danger" disabled={busy} onClick={close}>{text.cancel}</button>
            </div>
          </form>
          : <>
            <strong>{key.name || copy.passkeyDevice}</strong>
            <div className="account-muted">{formatDeveloperDate(key.createdAt)}
              {key.lastUsedAt && <> · {copy.lastUsed}: {formatDeveloperDate(key.lastUsedAt)}</>}</div>
            {mode.kind === 'remove' && mode.id === key.id
              ? <div className="detail-actions" role="group" aria-label={text.removeTitle} onKeyDown={escapeCloses}>
                <span>{text.removeAsk}</span>
                <button type="button" className="detail-button detail-button--danger" disabled={busy} aria-busy={busy} onClick={remove}>{text.remove}</button>
                <button type="button" className="detail-button detail-button--danger" disabled={busy} autoFocus onClick={close}>{text.cancel}</button>
              </div>
              : <div className="detail-actions">
                <button type="button" className="detail-button" disabled={busy || mode.kind !== 'idle'}
                  ref={element => { if (element) renameButtons.current.set(key.id, element); else renameButtons.current.delete(key.id); }}
                  aria-label={`${text.rename}: ${key.name || copy.passkeyDevice}`}
                  onClick={() => { setMessage(null); setMode({ kind: 'rename', id: key.id, name: key.name }); }}>{text.rename}</button>
                <button type="button" className="detail-button detail-button--danger" disabled={busy || mode.kind !== 'idle'}
                  aria-label={`${text.remove}: ${key.name || copy.passkeyDevice}`}
                  onClick={() => { setMessage(null); setMode({ kind: 'remove', id: key.id }); }}>{text.remove}</button>
              </div>}
          </>}
      </li>)}
    </ul> : <p className="account-muted">{copy.noPasskeys}</p>}
    <form className="suggest-change-form" onSubmit={add}>
      <div className="suggest-change-field">
        <label htmlFor={ids.name}>{text.nameLabel}</label>
        <input id={ids.name} type="text" maxLength={mfaDeviceNameMax} autoComplete="off" value={name} onChange={event => setName(event.target.value)} />
      </div>
      <div className="detail-actions">
        <button type="submit" ref={addButton} className="detail-button detail-button--primary" disabled={busy || mode.kind !== 'idle'} aria-busy={busy}>
          {busy ? text.adding : text.add}
        </button>
      </div>
    </form>
    <div aria-live="polite">{notice(message)}</div>
    {dialog}
  </section>;
}
