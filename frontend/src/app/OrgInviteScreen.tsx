import { useEffect, useState } from 'react';
import {
  acceptInvitation, invitedIntro, joinedText, loadInvitation, loginUrlFor, orgInviteCopy as c, orgInviteIdPattern,
  type AcceptOutcome, type InvitationState,
} from './org-invite';
import './information/information.css';
import './suggest-change/suggest-change.css';

type Phase = { kind: 'loading' } | { kind: 'working'; invite: Extract<InvitationState, { kind: 'invited' }> }
  | { kind: 'done'; outcome: AcceptOutcome } | { kind: 'read'; state: InvitationState; failure?: 'error' | 'rate-limited' | 'session' };

/** `organizationId` is null when the link has none. */
export default function OrgInviteScreen({ organizationId, loginUrl }: { organizationId: string | null; loginUrl: string }) {
  const valid = organizationId !== null && orgInviteIdPattern.test(organizationId);
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  useEffect(() => {
    if (!valid) return;
    let active = true;
    void loadInvitation(organizationId).then(state => { if (active) setPhase({ kind: 'read', state }); });
    return () => { active = false; };
  }, [organizationId, valid]);
  const join = async (invite: Extract<InvitationState, { kind: 'invited' }>) => {
    if (!valid) return;
    setPhase({ kind: 'working', invite });
    const outcome = await acceptInvitation(organizationId, invite.csrf);
    if (outcome.kind === 'error' || outcome.kind === 'rate-limited' || outcome.kind === 'session') setPhase({ kind: 'read', state: invite, failure: outcome.kind });
    else setPhase({ kind: 'done', outcome });
  };
  const notice = (text: string, extra?: React.ReactNode) => <section className="public-prose-card" role="status">
    <p>{text}</p>{extra}
  </section>;
  const home = <div className="detail-actions"><a className="detail-button" href="/">{c.home}</a></div>;
  let body: React.ReactNode;
  if (!organizationId) body = notice(c.missingId, home);
  else if (!valid) body = notice(c.noOrganization, home);
  else if (phase.kind === 'loading') body = <section className="public-prose-card"><p role="status">{c.loading}</p></section>;
  else if (phase.kind === 'done') {
    const outcome = phase.outcome;
    if (outcome.kind === 'joined') {
      body = notice(joinedText(outcome.name), <div className="detail-actions"><a className="detail-button detail-button--primary" href={`/organization/${organizationId}`}>{c.openOrganization}</a></div>);
    } else if (outcome.kind === 'signed-out') {
      body = notice(c.signedOut, <div className="detail-actions"><a className="detail-button detail-button--primary" href={loginUrlFor(loginUrl, organizationId)}>{c.login}</a></div>);
    } else {
      body = notice(outcome.kind === 'protected' ? c.protectedOrganization : outcome.kind === 'organization-not-found' ? c.noOrganization : c.notInvited, home);
    }
  } else {
    const state = phase.kind === 'working' ? phase.invite : phase.state;
    if (state.kind === 'signed-out') body = notice(c.signedOut, <div className="detail-actions"><a className="detail-button detail-button--primary" href={loginUrlFor(loginUrl, organizationId)}>{c.login}</a></div>);
    else if (state.kind === 'unavailable') body = notice(c.unavailable);
    else if (state.kind === 'invited') {
      const working = phase.kind === 'working';
      const failure = phase.kind === 'read' ? phase.failure : undefined;
      body = <section className="public-prose-card">
        <p>{invitedIntro(state.name)}</p>
        <div className="detail-actions">
          <button type="button" className="detail-button detail-button--primary" disabled={working} onClick={() => void join(state)}>{working ? c.working : c.button}</button>
        </div>
        <p role="status" className={failure ? 'flash-text' : undefined}>
          {failure === 'error' ? c.error : failure === 'rate-limited' ? c.rateLimited : failure === 'session' ? c.expiredSession : ''}
        </p>
      </section>;
    } else body = notice(state.kind === 'protected' ? c.protectedOrganization : state.kind === 'organization-not-found' ? c.noOrganization : c.notInvited, home);
  }
  return <article className="public-page-shell information-page suggest-change-page" aria-labelledby="org-invite-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="org-invite-title">{phase.kind === 'done' && phase.outcome.kind === 'joined' ? c.joinedTitle : c.title}</h1>
    </div></header>
    {body}
  </article>;
}
