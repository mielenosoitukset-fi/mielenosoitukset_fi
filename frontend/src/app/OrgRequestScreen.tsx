import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import type { SessionSummary } from './session';
import {
  orgRequestAccessLabels, orgRequestCopy, orgRequestMinePath, orgRequestPath, orgRequestStatusLabels,
  orgRequestSubmitPath,
} from './org-request-route';
import type { OrgRequestSummary } from './org-request-route';
import { orgRequestBody } from './org-request';
import './information/information.css';
import './contact/contact.css';
import './city-admin/city-admin.css';
import './org-request/org-request.css';

type Result = { kind: 'success' | 'error'; message: string } | null;

function MyRequests({ requests }: { requests: OrgRequestSummary[] }) {
  if (!requests.length) return null;
  return <section className="public-prose-card" aria-labelledby="org-request-mine-title">
    <h2 id="org-request-mine-title">Hakemuksesi</h2>
    <ul className="city-admin-applications">
      {requests.map(item => <li key={item.id}>
        <span>{item.organization}{item.represents ? '' : ' (ehdotus)'}</span>
        <span className={`city-admin-status city-admin-status--${item.status}`}>{orgRequestStatusLabels[item.status] ?? item.status}</span>
        <span className="city-admin-date">{new Date(item.created_at).toLocaleDateString('fi-FI')}</span>
      </li>)}
    </ul>
  </section>;
}

/** The public request form. Go stores the request; Hallinto handles it as a case. */
export default function OrgRequestScreen({ session }: { session: SessionSummary }) {
  const [requests, setRequests] = useState<OrgRequestSummary[]>([]);
  const [represents, setRepresents] = useState(true);
  const [access, setAccess] = useState('organization_account');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<Result>(null);
  const [startedAt, setStartedAt] = useState(0);
  // The form only works with JavaScript, so the server renders a fallback instead of an inert form.
  const [ready, setReady] = useState(false);
  const signedIn = session.authenticated;

  useEffect(() => {
    setReady(true);
    setStartedAt(Date.now());
    if (!signedIn) return;
    fetch(orgRequestMinePath, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(response => response.ok ? response.json() : null)
      .then(data => { if (data && Array.isArray(data.requests)) setRequests(data.requests); })
      .catch(() => { /* The form works without the list. */ });
  }, [signedIn]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = orgRequestBody(form, startedAt);
    if (!body.links.length) { setResult({ kind: 'error', message: 'Anna vähintään yksi linkki organisaation verkkosivuille tai someen.' }); return; }
    setSending(true);
    setResult(null);
    try {
      const response = await fetch(orgRequestSubmitPath, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.success) {
        if (data.status === 'received' && data.id) {
          const now = new Date().toISOString();
          setRequests([{ id: data.id, organization: body.organization_name, represents: body.represents, onboarding: body.onboarding,
            access: body.access || undefined, status: 'received', created_at: now, updated_at: now }, ...requests]);
          setResult({ kind: 'success', message: 'Kiitos, hakemus on vastaanotettu. Näet sen tilan tällä sivulla.' });
        } else {
          setResult({ kind: 'success', message: `Kiitos! Lähetimme vahvistuslinkin osoitteeseen ${body.applicant_email}. Avaa linkki 7 päivän kuluessa, niin hakemus siirtyy käsittelyyn. Samasta linkistä näet hakemuksen tilan.` });
        }
      } else {
        setResult({ kind: 'error', message: typeof data.message === 'string' ? data.message : 'Hakemusta ei voitu lähettää. Yritä uudelleen.' });
      }
    } catch {
      setResult({ kind: 'error', message: 'Hakemusta ei voitu lähettää. Tarkista yhteys ja yritä uudelleen.' });
    } finally {
      setSending(false);
    }
  };

  const loginHref = `/users/auth/login?next=${encodeURIComponent(orgRequestPath)}`;
  return <article className="public-page-shell information-page city-admin-page org-request-page" aria-labelledby="org-request-title">
    <header className="public-page-header">
      <div className="public-page-header__content">
        <h1 className="public-page-header__title" id="org-request-title">{orgRequestCopy.title}</h1>
        <p className="public-page-header__description">{orgRequestCopy.description}</p>
      </div>
    </header>
    <div className="public-content-stack">
      <section className="public-prose-card" aria-labelledby="org-request-how-title">
        <h2 id="org-request-how-title">Miten hakemus käsitellään?</h2>
        <ol>
          <li>Täytä lomake. Kirjautumista ei tarvita.</li>
          <li>Varmistamme, että organisaatio on hakemuksen takana. Organisaatio voi lähettää meille viestin julkisesta sähköpostiosoitteestaan tai virallisesta somestaan, vahvistaa viestimme tai tavata meidät. Pelkkä nimi ei riitä.</li>
          <li>Ylläpito käsittelee hakemuksen samoilla periaatteilla kuin mielenosoitusilmoitukset. Emme avaa tiliä esimerkiksi äärioikeistolaisille, syrjiville tai väkivaltaa hyväksyville ryhmille.</li>
          <li>Kun hakemus hyväksytään, lähetämme kutsun tilin käyttöön ottamiseen.</li>
        </ol>
      </section>

      <MyRequests requests={requests} />

      <section className="public-prose-card" aria-labelledby="org-request-form-title">
        <h2 id="org-request-form-title">Hakemus</h2>
        {result ? <div className={`flash-message flash-message--inline flash-${result.kind === 'success' ? 'success' : 'error'}`}
          role={result.kind === 'success' ? 'status' : 'alert'}><span className="flash-text">{result.message}</span></div> : null}
        {result?.kind === 'success' ? null
        : !ready ? <p className="city-admin-nojs">Lomake vaatii JavaScriptin. Jos se ei ole käytössä, voit <a href="/contact">ottaa yhteyttä</a> ja kertoa organisaatiostasi.</p>
        : <form className="contact-form city-admin-form" onSubmit={submit}>
          <fieldset className="city-admin-fieldset">
            <legend>Kuka hakee?</legend>
            <div className="org-request-choices">
              <label className="org-request-choice">
                <input type="radio" name="represents" value="yes" checked={represents} onChange={() => setRepresents(true)} />
                <span><strong>Edustan organisaatiota</strong><small>Haen tiliä organisaation puolesta.</small></span>
              </label>
              <label className="org-request-choice">
                <input type="radio" name="represents" value="no" checked={!represents} onChange={() => setRepresents(false)} />
                <span><strong>En edusta organisaatiota</strong><small>Ehdotan, että otatte yhteyttä organisaatioon. Et saa organisaation oikeuksia.</small></span>
              </label>
            </div>
          </fieldset>

          <fieldset className="city-admin-fieldset">
            <legend>Organisaatio</legend>
            <label htmlFor="org-request-name">Organisaation nimi</label>
            <input id="org-request-name" name="organization_name" required minLength={2} maxLength={120} autoComplete="organization" />
            <label htmlFor="org-request-email">Organisaation julkinen sähköpostiosoite{represents ? '' : ' (vapaaehtoinen)'}</label>
            <p className="city-admin-hint" id="org-request-email-hint">Osoitteen pitää näkyä organisaation omilla sivuilla tai somessa.</p>
            <input id="org-request-email" name="organization_email" type="email" required={represents} maxLength={254} aria-describedby="org-request-email-hint" />
            <label htmlFor="org-request-links">Verkkosivut ja some</label>
            <p className="city-admin-hint" id="org-request-links-hint">1–5 osoitetta, kukin omalle rivilleen.</p>
            <textarea id="org-request-links" name="links" rows={3} required aria-describedby="org-request-links-hint"
              placeholder="https://esimerkki.fi" />
            <label htmlFor="org-request-business-id">Y-tunnus (vapaaehtoinen)</label>
            <input id="org-request-business-id" name="business_id" inputMode="numeric" pattern="[0-9]{7}-[0-9]" placeholder="1234567-8" />
            <label htmlFor="org-request-description">Kerro lyhyesti organisaation toiminnasta</label>
            <textarea id="org-request-description" name="description" rows={4} maxLength={1500} />
          </fieldset>

          <fieldset className="city-admin-fieldset">
            <legend>Sinun tietosi</legend>
            <label htmlFor="org-request-applicant">Nimi</label>
            <input id="org-request-applicant" name="applicant_name" required minLength={2} maxLength={120} autoComplete="name" />
            {signedIn ? <p className="city-admin-hint">Olet kirjautunut sisään. Käytämme tilisi sähköpostiosoitetta, ja näet hakemuksen tilan tällä sivulla.</p> : <>
              <label htmlFor="org-request-applicant-email">Sähköpostiosoite</label>
              <p className="city-admin-hint" id="org-request-applicant-email-hint">Lähetämme tähän osoitteeseen vahvistuslinkin. Linkistä näet myös hakemuksen tilan.</p>
              <input id="org-request-applicant-email" name="applicant_email" type="email" required maxLength={254} autoComplete="email"
                aria-describedby="org-request-applicant-email-hint" />
            </>}
            {represents ? <>
              <label htmlFor="org-request-role">Roolisi organisaatiossa (vapaaehtoinen)</label>
              <input id="org-request-role" name="applicant_role" maxLength={120} placeholder="Esimerkiksi hallituksen jäsen tai tiedottaja" />
            </> : null}
          </fieldset>

          {represents ? <fieldset className="city-admin-fieldset">
            <legend>Kuka hallinnoi organisaatiota palvelussa?</legend>
            <p className="city-admin-hint">Hallinnoija voi kutsua muita organisaation jäseniä omilla tileillään. Valintaa voi muuttaa käsittelyn aikana.</p>
            <div className="org-request-choices">
              <label className="org-request-choice">
                <input type="radio" name="access" value="organization_account" checked={access === 'organization_account'} onChange={() => setAccess('organization_account')} />
                <span><strong>{orgRequestAccessLabels.organization_account}</strong><small>Uusi tili organisaation sähköpostiosoitteelle. Pääsy säilyy, vaikka ihmiset vaihtuvat.</small></span>
              </label>
              <label className={`org-request-choice${signedIn ? '' : ' org-request-choice--disabled'}`}>
                <input type="radio" name="access" value="own_account" disabled={!signedIn} checked={access === 'own_account'} onChange={() => setAccess('own_account')} />
                <span><strong>{orgRequestAccessLabels.own_account}</strong><small>{signedIn ? 'Sinusta tulee organisaation hallinnoija.' : <>Vaatii kirjautumisen. <a href={loginHref}>Kirjaudu sisään</a>.</>}</small></span>
              </label>
              <label className="org-request-choice">
                <input type="radio" name="access" value="invite" checked={access === 'invite'} onChange={() => setAccess('invite')} />
                <span><strong>{orgRequestAccessLabels.invite}</strong><small>Lähetämme kutsun osoitteeseen, jonka organisaatio vahvistaa.</small></span>
              </label>
            </div>
            {access === 'invite' ? <>
              <label htmlFor="org-request-invite-email">Osoite, johon kutsu lähetetään</label>
              <input id="org-request-invite-email" name="invite_email" type="email" required maxLength={254} />
            </> : null}
          </fieldset> : null}

          <label className="city-admin-consent">
            <input type="checkbox" name="onboarding" />
            <span>{represents ? 'Haluamme perehdytyksen palvelun käyttöön.' : 'Organisaatio voisi tarvita perehdytyksen palvelun käyttöön.'}</span>
          </label>

          <div className="org-request-trap" aria-hidden="true">
            <label htmlFor="org-request-homepage">Älä täytä tätä kenttää</label>
            <input id="org-request-homepage" name="homepage" tabIndex={-1} autoComplete="off" />
          </div>

          <section className="city-admin-privacy" aria-labelledby="org-request-privacy-title">
            <h3 id="org-request-privacy-title">Miten käsittelemme tietojasi</h3>
            <ul>
              <li>Tietoja käsittelevät vain hakemuksen käsittelijät.</li>
              <li>Vahvistamaton hakemus poistetaan 7 päivän kuluttua.</li>
              <li>Hylätyn hakemuksen tiedot poistetaan, kun uudelleenkäsittelyn määräaika on päättynyt.</li>
            </ul>
            <p>Lue lisää <a href="/privacy">tietosuojaselosteesta</a>.</p>
          </section>
          <label className="city-admin-consent">
            <input type="checkbox" name="consent" required />
            <span>Hyväksyn, että ylläpito käsittelee hakemuksen ja ottaa tarvittaessa yhteyttä organisaatioon.</span>
          </label>
          <button type="submit" className="detail-button detail-button--primary" disabled={sending}>
            {sending ? 'Lähetetään…' : 'Lähetä hakemus'}
          </button>
        </form>}
      </section>
    </div>
  </article>;
}
