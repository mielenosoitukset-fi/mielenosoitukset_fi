import { useState } from 'react';
import { confirmationCopy } from '../data/campaign';
import { confirmVolunteer, volunteerConfirmCopy as c, type VolunteerConfirmOutcome } from './volunteer-confirm';
import './information/information.css';
import './suggest-change/suggest-change.css';

type Phase = 'idle' | 'working' | VolunteerConfirmOutcome;

export default function VolunteerConfirmScreen({ token }: { token: string }) {
  const [phase, setPhase] = useState<Phase>('idle');
  const verdict = phase === 'success' || phase === 'invalid' || phase === 'expired' ? confirmationCopy[phase] : null;
  const act = async () => {
    setPhase('working');
    setPhase(await confirmVolunteer(token));
  };
  return <article className="public-page-shell information-page suggest-change-page" aria-labelledby="volunteer-confirm-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="volunteer-confirm-title">{verdict ? verdict.title : c.title}</h1>
    </div></header>
    {verdict ? <section className="public-prose-card" role="status">
      <p>{verdict.description}</p>
      <p>{verdict.detail}</p>
      <div className="detail-actions">
        <a className="detail-button detail-button--primary" href="/kampanja/">Palaa kampanjasivulle</a>
        <a className="detail-button" href="/">Etusivulle</a>
      </div>
      {phase !== 'success' && <p><a href="mailto:tuki@mielenosoitukset.fi">tuki@mielenosoitukset.fi</a></p>}
    </section> : <section className="public-prose-card">
      <p>{c.intro}</p>
      <div className="detail-actions">
        <button type="button" className="detail-button detail-button--primary" onClick={act} disabled={phase === 'working'}>
          {phase === 'working' ? c.working : c.button}
        </button>
      </div>
      <p role="status" className={phase === 'error' || phase === 'rate-limited' ? 'flash-text' : undefined}>
        {phase === 'error' ? c.error : phase === 'rate-limited' ? c.rateLimited : ''}
      </p>
    </section>}
  </article>;
}
