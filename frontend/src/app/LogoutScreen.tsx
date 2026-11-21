import { useEffect, useState } from 'react';
import type { AppConfig } from './config';
import type { SessionSummary } from './session';
import { logoutCopy as copy, logoutPath } from './logout-route';
import './information/information.css';
import './auth-pages/auth-pages.css';

/**
 * Sign-out confirmation. A plain form posting the signed token from the session to Flask,
 * so it works without JavaScript. Flask refuses a missing or foreign token and shows this
 * page again with a fresh one.
 */
export default function LogoutScreen({ config, session }: { config: AppConfig; session: SessionSummary }) {
  const [busy, setBusy] = useState(false);
  // A page restored by Back from the cache keeps its state: let the visitor submit again.
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => { if (event.persisted) setBusy(false); };
    window.addEventListener('pageshow', reset);
    return () => window.removeEventListener('pageshow', reset);
  }, []);

  return <article className="public-page-shell information-page auth-page" aria-labelledby="logout-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="logout-title">{copy.title}</h1>
    </div></header>
    <form className="public-prose-card auth-card logout-form" action={logoutPath} method="post" onSubmit={() => setBusy(true)}>
      {session.logoutToken && <input type="hidden" name="logout_token" value={session.logoutToken} />}
      <p>{copy.lead}</p>
      {/* Sign out comes first for keyboard and mobile; on wide screens it sits on the right. */}
      <div className="detail-actions logout-actions">
        <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>{copy.submit}</button>
        <a className="detail-button logout-cancel" href={config.urls.home}>{copy.cancel}</a>
      </div>
    </form>
  </article>;
}
