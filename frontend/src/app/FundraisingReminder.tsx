import { useEffect, useState } from 'react';
import { confirmReminder, readReminderLink, reminderCopy as c, removeReminder } from './fundraising-reminder';
import type { ReminderMode } from './fundraising-reminder';

type Outcome = { kind: 'ok' | 'error'; text: string } | null;

/** The page behind the links in the reminder mails: confirm the signup, or delete the address. */
export function ReminderLinkPanel() {
  const [link, setLink] = useState<{ mode: ReminderMode; token: string } | null>(null);
  const [state, setState] = useState<'idle' | 'working' | 'done'>('idle');
  const [outcome, setOutcome] = useState<Outcome>(null);
  useEffect(() => { setLink(readReminderLink(window.location.search, window.location.hash)); }, []);
  if (!link) return null;
  const confirming = link.mode === 'vahvista';
  const act = async () => {
    setState('working');
    const result = await (confirming ? confirmReminder : removeReminder)(link.token);
    if (result.ok) {
      setState('done');
      setOutcome({ kind: 'ok', text: confirming ? c.confirmed : c.removed });
    } else {
      setState('idle');
      setOutcome({ kind: 'error', text: result.status === 410 || !link.token ? result.message || c.badLink : result.message || c.error });
    }
  };
  return <section className="public-content__card fundraising-reminder-link" aria-labelledby="reminder-link-title">
    <h2 id="reminder-link-title">{confirming ? c.confirmTitle : c.removeTitle}</h2>
    {state !== 'done' && <>
      <p>{confirming ? c.confirmIntro : c.removeIntro}</p>
      <button type="button" className="fundraising-calendar" onClick={act} disabled={state === 'working' || !link.token}>{confirming ? c.confirmButton : c.removeButton}</button>
    </>}
    <p className={`fundraising-reminder__status${outcome?.kind === 'error' ? ' fundraising-reminder__status--error' : ''}`} role="status">
      {outcome?.text ?? (link.token ? '' : c.badLink)}
    </p>
  </section>;
}
