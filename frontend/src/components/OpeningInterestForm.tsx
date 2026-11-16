import { useState } from 'react';
import type { FormEvent } from 'react';
import { emailLooksValid, openingCopy, submitOpeningInterest } from '../app/opening-interest';
import type { OpeningInterest } from '../app/opening-interest';

type Choice<T extends string> = { value: T; label: string };
const willing: Choice<'yes' | 'maybe' | 'no'>[] = [{ value: 'yes', label: 'Kyllä' }, { value: 'maybe', label: 'Ehkä' }, { value: 'no', label: 'En' }];
const bands: Choice<'under10' | '10to20' | 'over20'>[] = [
  { value: 'under10', label: 'alle 10 euroa' }, { value: '10to20', label: '10–20 euroa' }, { value: 'over20', label: 'yli 20 euroa' }];

function Radios<T extends string>({ name, choices, value, onChange }: { name: string; choices: Choice<T>[]; value: string; onChange: (value: T) => void }) {
  return <div className="kx-radios">{choices.map(choice => <label key={choice.value} className="kx-radio">
    <input type="radio" name={name} value={choice.value} checked={value === choice.value} onChange={() => onChange(choice.value)} />
    <span>{choice.label}</span>
  </label>)}</div>;
}

export function OpeningInterestForm({ privacyUrl }: { privacyUrl: string }) {
  const [email, setEmail] = useState('');
  const [city, setCity] = useState('');
  const [consent, setConsent] = useState(false);
  const [pay, setPay] = useState<OpeningInterest['willingToPay']>('');
  const [band, setBand] = useState<OpeningInterest['priceBand']>('');
  const [errors, setErrors] = useState<{ email?: string; consent?: string }>({});
  const [banner, setBanner] = useState('');
  const [pending, setPending] = useState(false);
  const [sentTo, setSentTo] = useState('');
  const [trap, setTrap] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const next = {
      ...(emailLooksValid(email) ? {} : { email: openingCopy.invalidEmail }),
      ...(consent ? {} : { consent: openingCopy.consentRequired }),
    };
    setErrors(next);
    setBanner('');
    if (next.email || next.consent) return;
    setPending(true);
    // The ticket questions are optional and only sent when answered; the band needs a "yes" or "maybe".
    const answered = pay === 'yes' || pay === 'maybe';
    const result = await submitOpeningInterest({
      email: email.trim(), city: city.trim(), consent, willingToPay: pay, priceBand: answered ? band : '', website: trap });
    setPending(false);
    if (result.ok) setSentTo(email.trim());
    else setBanner(result.message);
  }

  if (sentTo) return <div className="kv-form" role="status">
    <span className="kv-ok"><i className="fa-solid fa-check" aria-hidden="true" /></span>
    <h3>Kiitos, vahvista sähköpostisi</h3>
    <p className="kv-done-text">Lähetimme viestin osoitteeseen <b>{sentTo}</b>. Avaa viestistä vahvistuslinkki, niin ilmoittautumisesi tulee voimaan.</p>
    <p className="kv-small kv-done-text">Eikö viesti tullut? Tarkista roskaposti. Voit perua ilmoittautumisen milloin tahansa viestin linkistä.</p>
  </div>;

  return <form className="kv-form" onSubmit={submit} noValidate aria-busy={pending || undefined}>
    <h3>Ilmoittaudu kiinnostuneeksi</h3>
    <p className="kv-small">Ei sitoumusta. Kerromme paikan, kun se varmistuu.</p>
    {banner && <p className="kv-banner kv-banner--err" role="alert"><i className="fa-solid fa-circle-exclamation" aria-hidden="true" /><span>{banner}</span></p>}
    <fieldset className="kv-bare" disabled={pending}>
      <div className="kv-trap" aria-hidden="true"><label>Älä täytä tätä kenttää<input name="website" tabIndex={-1} autoComplete="off" value={trap} onChange={event => setTrap(event.target.value)} /></label></div>
      <div className="form-group">
        <label className="form-label" htmlFor="ie">Sähköposti <span className="kv-req">*</span></label>
        <input id="ie" name="email" className="form-control" type="email" autoComplete="email" placeholder="nimi@esimerkki.fi"
          value={email} onChange={event => setEmail(event.target.value)} aria-invalid={errors.email ? true : undefined} aria-describedby={errors.email ? 'ie-err' : undefined} />
        {errors.email && <p id="ie-err" className="kv-err" role="alert"><i className="fa-solid fa-circle-exclamation" aria-hidden="true" /> {errors.email}</p>}
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="ic">Paikkakunta <span className="kv-opt">(valinnainen)</span></label>
        <input id="ic" name="city" className="form-control" autoComplete="address-level2" placeholder="Esimerkiksi Tampere" value={city} onChange={event => setCity(event.target.value)} />
      </div>
      <details className="kv-more">
        <summary>Kerro halutessasi lisää</summary>
        <div>
          <fieldset className="kv-q"><legend>Olisitko valmis maksamaan pääsylipusta? <span className="kv-opt">(valinnainen)</span></legend>
            <Radios name="willing" choices={willing} value={pay} onChange={setPay} /></fieldset>
          {(pay === 'yes' || pay === 'maybe') && <fieldset className="kv-q"><legend>Kuinka paljon? <span className="kv-opt">(valinnainen)</span></legend>
            <Radios name="band" choices={bands} value={band} onChange={setBand} /></fieldset>}
          <p className="kv-small kv-qnote">Nyt ei makseta mitään eikä luvata mitään. Vastaus auttaa meitä valitsemaan sopivan paikan. Paikka ja hinta tarkentuvat myöhemmin.</p>
        </div>
      </details>
      <label className="kv-consent">
        <input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} aria-invalid={errors.consent ? true : undefined} />
        <span>Sallin sähköpostiosoitteeni käytön avajaisten viesteihin ja kampanjan päivityksiin. Vahvistan ilmoittautumisen sähköpostitse ja voin perua sen milloin tahansa yhdellä klikkauksella. Tiedot poistetaan avajaisten jälkeen, ellen anna uutta suostumusta. <a href={privacyUrl}>Tietosuojaseloste</a></span>
      </label>
      {errors.consent && <p className="kv-err" role="alert"><i className="fa-solid fa-circle-exclamation" aria-hidden="true" /> {errors.consent}</p>}
      <button className="btn-primary" type="submit">{pending ? 'Lähetetään…' : 'Ilmoittaudu kiinnostuneeksi'}</button>
    </fieldset>
  </form>;
}
