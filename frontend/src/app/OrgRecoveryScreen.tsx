import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import type { SessionSummary } from './session';
import { orgRecoveryCopy, orgRecoveryNeeds, orgRecoverySubmitPath } from './org-request-route';
import { orgRecoveryBody } from './org-request';
import './information/information.css';
import './contact/contact.css';
import './city-admin/city-admin.css';
import './org-request/org-request.css';

type Result = { kind: 'success' | 'error'; message: string } | null;

/**
 * Access recovery form (O5). The answer never says whether the organization exists, so the
 * success text is the same for every accepted request. Go stores it; Hallinto decides.
 */
export default function OrgRecoveryScreen({ session }: { session: SessionSummary }) {
  const [need, setNeed] = useState('lost_access');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<Result>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [ready, setReady] = useState(false);
  const signedIn = session.authenticated;

  useEffect(() => {
    setReady(true);
    setStartedAt(Date.now());
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = orgRecoveryBody(new FormData(event.currentTarget), startedAt);
    setSending(true);
    setResult(null);
    try {
      const response = await fetch(orgRecoverySubmitPath, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.success) {
        setResult({ kind: 'success', message: data.status === 'received'
          ? 'Kiitos, pyyntö on vastaanotettu. Käsittelemme sen ja ilmoitamme asiasta organisaation tunnetuille osoitteille.'
          : 'Kiitos! Tarkista sähköpostisi: lähetimme vahvistuslinkin, jos pyynnön voi ottaa käsittelyyn. Avaa linkki 7 päivän kuluessa. Jos viestiä ei kuulu 30 minuutissa, tarkista roskaposti tai ota yhteyttä ylläpitoon yhteydenottolomakkeella.' });
      } else {
        setResult({ kind: 'error', message: typeof data.message === 'string' ? data.message : 'Pyyntöä ei voitu lähettää. Yritä uudelleen.' });
      }
    } catch {
      setResult({ kind: 'error', message: 'Pyyntöä ei voitu lähettää. Tarkista yhteys ja yritä uudelleen.' });
    } finally {
      setSending(false);
    }
  };

  return <article className="public-page-shell information-page city-admin-page org-request-page" aria-labelledby="org-recovery-title">
    <header className="public-page-header">
      <div className="public-page-header__content">
        <h1 className="public-page-header__title" id="org-recovery-title">{orgRecoveryCopy.title}</h1>
        <p className="public-page-header__description">{orgRecoveryCopy.description}</p>
      </div>
    </header>
    <div className="public-content-stack">
      <section className="public-prose-card" aria-labelledby="org-recovery-how-title">
        <h2 id="org-recovery-how-title">Miten pyyntö käsitellään?</h2>
        <ol>
          <li>Täytä lomake. Kirjautumista ei tarvita.</li>
          <li>Emme anna pääsyä pelkän pyynnön perusteella. Varmistamme, että organisaatio on pyynnön takana: organisaatio voi vahvistaa pyynnön julkisesta sähköpostiosoitteestaan tai virallisesta somestaan.</li>
          <li>Ilmoitamme pyynnöstä organisaation tunnetuille osoitteille. Organisaation nykyinen hallinnoija voi vastustaa pyyntöä 7 päivän ajan.</li>
          <li>Kun pyyntö hyväksytään, uusi hallinnoija saa kutsun sähköpostiinsa. Nykyiset hallinnoijat jäävät ylläpitäjiksi, joten kenenkään pääsyä ei poisteta.</li>
        </ol>
      </section>

      <section className="public-prose-card" aria-labelledby="org-recovery-form-title">
        <h2 id="org-recovery-form-title">Pyyntö</h2>
        {result ? <div className={`flash-message flash-message--inline flash-${result.kind === 'success' ? 'success' : 'error'}`}
          role={result.kind === 'success' ? 'status' : 'alert'}><span className="flash-text">{result.message}</span></div> : null}
        {result?.kind === 'success' ? null
        : !ready ? <p className="city-admin-nojs">Lomake vaatii JavaScriptin. Jos se ei ole käytössä, voit <a href="/contact">ottaa yhteyttä</a> ja kertoa tilanteesta.</p>
        : <form className="contact-form city-admin-form" onSubmit={submit}>
          <fieldset className="city-admin-fieldset">
            <legend>Mitä tarvitset?</legend>
            <div className="org-request-choices">
              {orgRecoveryNeeds.map(item => <label className="org-request-choice" key={item.value}>
                <input type="radio" name="need" value={item.value} checked={need === item.value} onChange={() => setNeed(item.value)} />
                <span><strong>{item.label}</strong><small>{item.hint}</small></span>
              </label>)}
            </div>
          </fieldset>

          <fieldset className="city-admin-fieldset">
            <legend>Organisaatio</legend>
            <label htmlFor="org-recovery-name">Organisaation nimi</label>
            <input id="org-recovery-name" name="organization_name" required minLength={2} maxLength={120} autoComplete="organization" />
            <label htmlFor="org-recovery-links">Organisaation omat verkkosivut ja some (vapaaehtoinen)</label>
            <p className="city-admin-hint" id="org-recovery-links-hint">Enintään 5 osoitetta, kukin omalle rivilleen. Auttaa meitä varmistamaan, että organisaatio on pyynnön takana.</p>
            <textarea id="org-recovery-links" name="links" rows={3} aria-describedby="org-recovery-links-hint" placeholder="https://esimerkki.fi" />
            <label htmlFor="org-recovery-description">Kerro tilanne lyhyesti</label>
            <textarea id="org-recovery-description" name="description" rows={4} maxLength={1500} />
          </fieldset>

          {need === 'lost_access' ? null : <fieldset className="city-admin-fieldset">
            <legend>{need === 'owner_change' ? 'Uusi hallinnoija' : 'Lisättävä henkilö'}</legend>
            <label htmlFor="org-recovery-person-email">Henkilön sähköpostiosoite</label>
            <p className="city-admin-hint" id="org-recovery-person-email-hint">Kutsu lähetetään tähän osoitteeseen. Henkilöllä pitää olla tai olla mahdollisuus luoda tili tällä osoitteella.</p>
            <input id="org-recovery-person-email" name="person_email" type="email" required maxLength={254} aria-describedby="org-recovery-person-email-hint" />
            {need === 'add_member' ? <>
              <label htmlFor="org-recovery-person-role">Rooli organisaatiossa</label>
              <select id="org-recovery-person-role" name="person_role" defaultValue="member">
                <option value="member">Jäsen</option>
                <option value="admin">Ylläpitäjä</option>
              </select>
            </> : null}
          </fieldset>}

          <fieldset className="city-admin-fieldset">
            <legend>Sinun tietosi</legend>
            <p className="city-admin-hint">Nämä tiedot näkyvät vain pyynnön käsittelijöille.</p>
            {signedIn ? <p className="city-admin-hint">Olet kirjautunut sisään. Käytämme tilisi sähköpostiosoitetta.</p> : null}
            <label htmlFor="org-recovery-applicant">Nimi</label>
            <input id="org-recovery-applicant" name="applicant_name" required minLength={2} maxLength={120} autoComplete="name" />
            {signedIn ? null : <>
              <label htmlFor="org-recovery-applicant-email">Sähköpostiosoite</label>
              <p className="city-admin-hint" id="org-recovery-applicant-email-hint">Lähetämme tähän osoitteeseen vahvistuslinkin. Linkistä näet myös pyynnön tilan.</p>
              <input id="org-recovery-applicant-email" name="applicant_email" type="email" required maxLength={254} autoComplete="email"
                aria-describedby="org-recovery-applicant-email-hint" />
            </>}
            <label htmlFor="org-recovery-role">Roolisi organisaatiossa (vapaaehtoinen)</label>
            <input id="org-recovery-role" name="applicant_role" maxLength={120} placeholder="Esim. hallituksen jäsen" />
          </fieldset>

          <div className="org-request-trap" aria-hidden="true">
            <label htmlFor="org-recovery-homepage">Älä täytä tätä kenttää</label>
            <input id="org-recovery-homepage" name="homepage" tabIndex={-1} autoComplete="off" />
          </div>

          <section className="city-admin-privacy" aria-labelledby="org-recovery-privacy-title">
            <h3 id="org-recovery-privacy-title">Miten käsittelemme tietojasi</h3>
            <ul>
              <li>Tietoja käsittelevät vain pyynnön käsittelijät.</li>
              <li>Vahvistamaton pyyntö poistetaan 7 päivän kuluttua.</li>
              <li>Ilmoitamme pyynnöstä organisaation tunnetuille osoitteille.</li>
            </ul>
            <p>Lue lisää <a href="/privacy">tietosuojaselosteesta</a>.</p>
          </section>
          <label className="city-admin-consent">
            <input type="checkbox" name="consent" required />
            <span>Hyväksyn, että ylläpito käsittelee pyynnön ja ottaa tarvittaessa yhteyttä organisaatioon.</span>
          </label>
          <button type="submit" className="detail-button detail-button--primary" disabled={sending}>
            {sending ? 'Lähetetään…' : 'Lähetä pyyntö'}
          </button>
        </form>}
      </section>
    </div>
  </article>;
}
