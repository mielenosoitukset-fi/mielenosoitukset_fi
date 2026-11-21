import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppConfig } from './config';
import type { SessionSummary } from './session';
import { FlashNotices, useFlashNotices } from './flash-notices';
import { normalizeUsername, readUsernameFree, usernameFreeApi, usernameShapeOk } from './register-route';
import type { UsernameState } from './register-route';
import { displaynameOk, forcedIdentityCopy as copy, forcedIdentityPath } from './forced-route';
import { ForcedLogoutCard } from './ForcedLogoutCard';
import './information/information.css';
import './auth-pages/auth-pages.css';

const usernameText: Record<UsernameState, string> = {
  idle: '', checking: copy.checking, available: copy.available, taken: copy.taken,
  invalid: copy.invalidUsername, failed: copy.checkFailed,
};

/**
 * Required change of username and display name. A plain form posting to the unchanged Flask
 * handler, so it works without JavaScript; Flask decides (reserved names, uniqueness, both
 * names new) and mails the confirmation. The live checks only guide the visitor.
 */
export default function ForcedIdentityScreen({ config, session }: { config: AppConfig; session: SessionSummary }) {
  const notices = useFlashNotices();
  const [username, setUsername] = useState('');
  const [displayname, setDisplayname] = useState('');
  const [usernameState, setUsernameState] = useState<UsernameState>('idle');
  const [error, setError] = useState(0);
  const [busy, setBusy] = useState(false);
  const errorBox = useRef<HTMLParagraphElement>(null);
  const nameBad = error > 0 && !displaynameOk(displayname);

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
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => { if (event.persisted) setBusy(false); };
    window.addEventListener('pageshow', reset);
    return () => window.removeEventListener('pageshow', reset);
  }, []);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    // Block only what Flask would refuse anyway; a failed availability check never blocks.
    if (!usernameShapeOk(username) || usernameState === 'taken' || !displaynameOk(displayname)) {
      event.preventDefault();
      setError(count => count + 1);
      return;
    }
    setBusy(true);
  }

  return <article className="public-page-shell information-page auth-page" aria-labelledby="forced-identity-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="forced-identity-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    <FlashNotices notices={notices} />
    {error > 0 && <p ref={errorBox} tabIndex={-1} className="flash-message flash-message--inline flash-error" role="alert">
      <span className="flash-text">{copy.fixErrors}</span></p>}
    <form className="auth-form register-form" action={forcedIdentityPath} method="post" onSubmit={onSubmit}>
      <section className="public-prose-card auth-card register-card">
        <p className="auth-hint">{copy.reserved}</p>
        <label htmlFor="forced-username">{copy.username}</label>
        <input type="text" id="forced-username" name="username" required minLength={3} maxLength={30}
          autoComplete="username" autoCapitalize="none" spellCheck={false}
          aria-describedby="forced-username-hint forced-username-status"
          aria-invalid={usernameState === 'invalid' || usernameState === 'taken' || (error > 0 && !usernameShapeOk(username)) ? true : undefined}
          value={username} onChange={event => setUsername(event.target.value)} />
        <p className={`register-status register-status--${usernameState}`} id="forced-username-status" aria-live="polite">
          {usernameText[usernameState]}</p>
        <p className="auth-hint" id="forced-username-hint">{copy.usernameHint}</p>
        <label htmlFor="forced-displayname">{copy.displayname}</label>
        <input type="text" id="forced-displayname" name="displayname" required minLength={2} maxLength={80}
          autoComplete="nickname" aria-describedby="forced-displayname-hint" aria-invalid={nameBad ? true : undefined}
          value={displayname} onChange={event => setDisplayname(event.target.value)} />
        <p className={nameBad ? 'auth-hint register-status--taken' : 'auth-hint'} id="forced-displayname-hint">
          {nameBad ? copy.displaynameLength : copy.displaynameHint}</p>
        <div className="detail-actions register-actions">
          <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{copy.submit}</button>
        </div>
      </section>
    </form>
    <ForcedLogoutCard session={session} logoutUrl={config.urls.logout} />
  </article>;
}
