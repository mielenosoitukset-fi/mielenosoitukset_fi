import type { SessionSummary } from './session';
import { forcedCommonCopy as copy } from './forced-route';

/** The way out of a forced step: sign out (a POST with the session's token) or ask for help. */
export function ForcedLogoutCard({ session, logoutUrl }: { session: SessionSummary; logoutUrl: string }) {
  return <section className="public-prose-card auth-card auth-card--secondary" aria-label={copy.logoutLead}>
    <p>{copy.logoutLead}</p>
    <form action={logoutUrl} method="post" className="detail-actions">
      {session.logoutToken && <input type="hidden" name="logout_token" value={session.logoutToken} />}
      <button type="submit" className="detail-button">{copy.logout}</button>
    </form>
    <p className="auth-hint">{copy.help} <a href={`mailto:${copy.helpEmail}`}>{copy.helpEmail}</a></p>
  </section>;
}
