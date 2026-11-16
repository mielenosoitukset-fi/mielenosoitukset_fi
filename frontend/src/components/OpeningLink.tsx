import { useEffect, useState } from 'react';
import { confirmOpening, openingLinkCopy as c, readOpeningLink, removeOpening } from '../app/opening-interest';
import type { OpeningLinkMode } from '../app/opening-interest';

/**
 * Confirmation and removal links from the opening interest mails. It works whatever the form
 * switch says, so a removal link never dies. A mail scanner that opens the link changes nothing:
 * the action needs a button press, and the token is in the fragment.
 */
export function OpeningLink() {
  const [link, setLink] = useState<{ mode: OpeningLinkMode; token: string } | null>(null);
  const [state, setState] = useState<'idle' | 'working' | 'done' | 'expired'>('idle');
  const [message, setMessage] = useState('');
  useEffect(() => { setLink(readOpeningLink(window.location.search, window.location.hash)); }, []);
  if (!link) return null;
  const confirming = link.mode === 'vahvista';
  const act = async () => {
    setState('working');
    const result = await (confirming ? confirmOpening : removeOpening)(link.token);
    if (result.ok) {
      setState('done');
      setMessage(confirming ? c.confirmed : c.removed);
    } else {
      const gone = result.status === 400 || result.status === 404 || result.status === 410;
      setState(gone ? 'expired' : 'idle');
      setMessage(gone ? (confirming ? c.expired : c.removeExpired) : c.error);
    }
  };
  return <section className="kv-sec kv-linkpanel" aria-labelledby="opening-link-title">
    <div className="kv-form">
      <h2 id="opening-link-title" className="kv-h2 kv-h2--tight">{state === 'done' ? (confirming ? c.doneConfirmTitle : c.doneRemoveTitle) : confirming ? c.confirmTitle : c.removeTitle}</h2>
      {state === 'done' ? <p role="status">{message}</p> : <>
        {!link.token ? <p role="alert">{c.badLink}</p> : state === 'expired' ? null : <>
          <p>{confirming ? c.confirmIntro : c.removeIntro}</p>
          <button type="button" className="btn-primary" onClick={act} disabled={state === 'working'}>{confirming ? c.confirmButton : c.removeButton}</button>
        </>}
        {message && <p className="kv-err" role="alert">{message}</p>}
      </>}
    </div>
  </section>;
}
