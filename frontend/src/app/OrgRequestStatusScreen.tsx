import { useEffect, useState } from 'react';
import {
  orgRequestAccessLabels, orgRequestCopy, orgRequestPath, orgRequestStatusHelp, orgRequestStatusLabels,
} from './org-request-route';
import { readStatus, statusToken } from './org-request';
import type { OrgRequestState } from './org-request';
import './information/information.css';
import './city-admin/city-admin.css';
import './org-request/org-request.css';

const date = (value: string) => new Date(value).toLocaleDateString('fi-FI');

/** The private status page. Opening it the first time confirms the applicant's address. */
export default function OrgRequestStatusScreen() {
  const [state, setState] = useState<OrgRequestState>({ kind: 'loading' });

  useEffect(() => {
    const token = statusToken(window.location.hash);
    if (!token) { setState({ kind: 'missing' }); return; }
    void readStatus(token).then(setState);
  }, []);

  return <article className="public-page-shell information-page city-admin-page org-request-page" aria-labelledby="org-request-status-title">
    <header className="public-page-header">
      <div className="public-page-header__content">
        <h1 className="public-page-header__title" id="org-request-status-title">{orgRequestCopy.statusTitle}</h1>
      </div>
    </header>
    <div className="public-content-stack">
      <section className="public-prose-card org-request-status" aria-live="polite">
        {state.kind === 'loading' ? <p>Haetaan hakemusta…</p>
        : state.kind === 'missing' ? <>
          <p>Hakemusta ei löytynyt. Tarkista, että avasit koko linkin sähköpostista. Vahvistamaton hakemus poistetaan 7 päivän kuluttua.</p>
          <p><a href={orgRequestPath}>Tee uusi hakemus</a></p>
        </>
        : state.kind === 'error' ? <div className="flash-message flash-message--inline flash-error" role="alert"><span className="flash-text">{state.message}</span></div>
        : <>
          <h2>{state.request.organization}</h2>
          <p><span className={`city-admin-status city-admin-status--${state.request.status}`}>{orgRequestStatusLabels[state.request.status] ?? state.request.status}</span></p>
          {orgRequestStatusHelp[state.request.status] ? <p>{orgRequestStatusHelp[state.request.status]}</p> : null}
          {state.request.message ? <p>{state.request.message}</p> : null}
          <dl>
            <dt>Hakemus</dt><dd>{state.request.represents ? 'Organisaation oma hakemus' : 'Ehdotus organisaatiosta'}</dd>
            {state.request.access ? <><dt>Hallinnointi</dt><dd>{orgRequestAccessLabels[state.request.access] ?? state.request.access}</dd></> : null}
            <dt>Perehdytys</dt><dd>{state.request.onboarding ? 'Pyydetty' : 'Ei pyydetty'}</dd>
            <dt>Lähetetty</dt><dd>{date(state.request.created_at)}</dd>
            <dt>Päivitetty</dt><dd>{date(state.request.updated_at)}</dd>
          </dl>
          <p className="city-admin-hint">Linkki on henkilökohtainen. Säilytä se, niin voit tarkistaa tilan myöhemmin.</p>
        </>}
      </section>
    </div>
  </article>;
}
