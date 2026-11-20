import { useEffect, useRef, useState } from 'react';
import { focusTargetFor } from './confirm-focus';
import { confirmEmail, emailConfirmCopy as c, type EmailConfirmOutcome } from './email-confirm';
import './information/information.css';
import './suggest-change/suggest-change.css';

type Phase = 'idle' | 'working' | EmailConfirmOutcome;

export default function EmailConfirmScreen({ token, loginUrl }: { token: string; loginUrl: string }) {
  const [phase, setPhase] = useState<Phase>('idle');
  const button = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const target = focusTargetFor(phase === 'idle' || phase === 'working' ? null : phase);
    if (target === 'button') button.current?.focus();
    if (target === 'heading') heading.current?.focus();
  }, [phase]);
  const act = async () => {
    setPhase('working');
    setPhase(await confirmEmail(token));
  };
  const done = phase === 'confirmed' || phase === 'invalid' || phase === 'user-not-found';
  const title = phase === 'confirmed' ? c.confirmedTitle : phase === 'invalid' ? c.invalidTitle : phase === 'user-not-found' ? c.notFoundTitle : c.title;
  return <article className="public-page-shell information-page suggest-change-page" aria-labelledby="email-confirm-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="email-confirm-title" ref={heading} tabIndex={-1}>{title}</h1>
    </div></header>
    {done ? <section className="public-prose-card" role="status">
      <p>{phase === 'confirmed' ? c.confirmed : phase === 'invalid' ? c.invalid : c.notFound}</p>
      {phase === 'invalid' && <p>{c.invalidHelp}</p>}
      <div className="detail-actions"><a className="detail-button detail-button--primary" href={loginUrl}>{c.login}</a></div>
    </section> : <section className="public-prose-card">
      <p>{c.intro}</p>
      <div className="detail-actions">
        <button type="button" className="detail-button detail-button--primary" ref={button} onClick={act} disabled={phase === 'working'}>
          {phase === 'working' ? c.working : c.button}
        </button>
      </div>
      <p role="status" className={phase === 'error' || phase === 'rate-limited' ? 'flash-text' : undefined}>
        {phase === 'error' ? c.error : phase === 'rate-limited' ? c.rateLimited : ''}
      </p>
    </section>}
  </article>;
}
