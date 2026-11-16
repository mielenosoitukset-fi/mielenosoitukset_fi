import data from '../../../mielenosoitukset_fi/utils/data/fundraising.json';
import qrcode from 'qrcode-generator';
import type { AppConfig } from './config';
import { fundraisingCopy } from './fundraising-copy';
import { useEffect, useState } from 'react';
import { activeMobilepayUrl, fundraisingDetails, fundraisingExtras } from './fundraising';
import type { FundraisingDetails, FundraisingExtras } from './fundraising';

const finnishDate = (iso: string) => iso.split('-').reverse().map(part => String(Number(part))).join('.');

function Arrow() {
  return <i className="fas fa-arrow-right" aria-hidden="true" />;
}

/** QR code for the active MobilePay link, drawn as one SVG path so it needs no script. */
function MobilepayQr({ url }: { url: string }) {
  const qr = qrcode(0, 'M');
  qr.addData(url);
  qr.make();
  const size = qr.getModuleCount();
  let path = '';
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (qr.isDark(y, x)) path += `M${x} ${y}h1v1h-1z`;
  return <figure className="fundraising-qr">
    <svg viewBox={`-4 -4 ${size + 8} ${size + 8}`} role="img" aria-label="QR-koodi MobilePay-lahjoitukseen" shapeRendering="crispEdges">
      <rect x="-4" y="-4" width={size + 8} height={size + 8} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
    <figcaption>Skannaa puhelimella</figcaption>
  </figure>;
}

/** One value with its own copy button; the button label and status name the field for assistive technology. */
function CopyField({ label, value, copyLabel, doneText, mono }: { label: string; value: string; copyLabel: string; doneText: string; mono?: boolean }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setState('copied');
    } catch {
      setState('failed');
    }
    window.setTimeout(() => setState('idle'), 2500);
  };
  return <div className="fundraising-field">
    <div className="fundraising-field__text">
      <dt>{label}</dt>
      <dd className={mono ? 'fundraising-iban' : undefined}>{mono ? value.replace(/ (\S+)$/, '\u00a0$1') : value}</dd>
      <span className="fundraising-copy__status" role="status">{state === 'copied' ? doneText : state === 'failed' ? 'Kopiointi ei onnistunut. Valitse teksti ja kopioi se itse.' : ''}</span>
    </div>
    <button type="button" className="fundraising-copy" onClick={copy} aria-label={copyLabel}>{state === 'copied' ? 'Kopioitu' : 'Kopioi'}</button>
  </div>;
}

/** Native share sheet where the browser has one, otherwise a copied link. */
function ShareButton({ url, title }: { url: string; title: string }) {
  const [done, setDone] = useState('');
  const share = async () => {
    try {
      if (typeof navigator.share === 'function') await navigator.share({ title, url });
      else {
        await navigator.clipboard.writeText(url);
        setDone('Linkki kopioitu');
      }
    } catch {
      // The visitor closed the share sheet, or the browser blocked copying: nothing to report.
    }
  };
  return <>
    <button type="button" className="fundraising-copy fundraising-copy--ghost" onClick={share}>Jaa sivu</button>
    <span className="fundraising-copy__status" role="status">{done}</span>
  </>;
}

/** Mobile bar with the main donate action; shown only while the donate card is off screen. */
function StickyDonate({ href, external }: { href: string; external: boolean }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const card = document.getElementById('lahjoita');
    if (!card || typeof IntersectionObserver === 'undefined') return;
    const watch = new IntersectionObserver(([entry]) => setVisible(!entry.isIntersecting && entry.boundingClientRect.top < 0));
    watch.observe(card);
    return () => watch.disconnect();
  }, []);
  return <div className="fundraising-sticky" data-visible={visible} aria-hidden={!visible}>
    <a className="fundraising-button" href={href} tabIndex={visible ? 0 : -1} {...(external ? { rel: 'noopener noreferrer' } : {})}>
      {external ? 'Lahjoita MobilePaylla' : 'Lahjoita'}
    </a>
  </div>;
}

/** The fundraising page: donating comes first, then purpose, trust facts and questions. */
export function FundraisingPage({ details, extras, config }: { details: FundraisingDetails; extras: FundraisingExtras; config: AppConfig }) {
  const f = details;
  const pre = extras.pre_launch;
  const opens = extras.opens_date;
  const mobilepayUrl = activeMobilepayUrl(f.mobilepay_links, extras.mobilepay_active);
  const open = !pre && f.can_donate;
  const status = pre ? `Aukeaa ${opens ? finnishDate(opens) : ''}` : f.is_preparation ? 'Valmistelussa' : f.can_donate ? 'Keräys avoinna' : 'Lahjoittaminen suljettu';
  const period = f.display_start_date && f.display_end_date
    ? <><time dateTime={f.display_start_date}>{finnishDate(f.display_start_date)}</time>–<time dateTime={f.display_end_date}>{finnishDate(f.display_end_date)}</time></>
    : 'Vahvistetaan myöhemmin';
  const pageUrl = `${config.origin}/ker%C3%A4ys`;
  return <article className="public-content campaign-page fundraising-page" lang="fi">
    <section className="campaign-fundraising-intro" aria-labelledby="fundraising-title">
      <h1 id="fundraising-title">Auta {'mielen\u00adosoituksia'} löytymään</h1>
      <p className="fundraising-lead">{fundraisingCopy.pageLead}</p>
    </section>

    {open ? <section className="public-content__card fundraising-donate" id="lahjoita" aria-labelledby="donate-title">
      <h2 id="donate-title">Näin lahjoitat</h2>
      <div className="fundraising-donate__grid">
        {mobilepayUrl && <div className="fundraising-donate__mobilepay">
          <h3>MobilePay</h3>
          <a className="fundraising-button" href={mobilepayUrl} rel="noopener noreferrer">Lahjoita MobilePaylla</a>
          <p className="fundraising-note">Valitse summa sovelluksessa. Lahjoitus on vapaaehtoinen.</p>
          <MobilepayQr url={mobilepayUrl} />
        </div>}
        {f.iban && <div className="fundraising-donate__bank">
          <h3>Tilisiirto</h3>
          <dl className="fundraising-fields">
            {f.recipient && <CopyField label="Saaja" value={f.recipient} copyLabel="Kopioi saajan nimi" doneText="Saajan nimi kopioitu" />}
            <CopyField label="Tilinumero (IBAN)" value={f.iban} copyLabel="Kopioi tilinumero" doneText="Tilinumero kopioitu" mono />
            {extras.bic && <CopyField label="BIC" value={extras.bic} copyLabel="Kopioi BIC" doneText="BIC kopioitu" />}
            {f.reference
              ? <CopyField label="Viite" value={f.reference} copyLabel="Kopioi viite" doneText="Viite kopioitu" />
              : <CopyField label="Viesti" value="Pienkeräys" copyLabel="Kopioi viesti" doneText="Viesti kopioitu" />}
          </dl>
          <p className="fundraising-note">Viesti auttaa tunnistamaan lahjoituksen, mutta sitä ei vaadita.</p>
        </div>}
      </div>
      {f.payment_url && <p><a className="public-content__link" href={f.payment_url} rel="noopener noreferrer">Siirry lahjoittamaan <Arrow /></a></p>}
      <p className="fundraising-deadline">Lahjoita vain keräysaikana {finnishDate(f.start_date!)}–{finnishDate(f.end_date!)}.</p>
    </section> : <section className="public-content__card fundraising-donate" id="lahjoita" aria-labelledby="donate-title">
      <h2 id="donate-title">{pre && opens ? `Pienkeräys aukeaa ${finnishDate(opens)}` : f.is_preparation || pre ? 'Lahjoittaminen ei ole vielä avoinna' : 'Lahjoittaminen on suljettu'}</h2>
      <p>{pre ? 'Tilinumero ja lahjoitusohjeet julkaistaan tällä sivulla, kun keräys alkaa. Lahjoita vasta, kun keräysaika on alkanut.'
        : f.confirmed ? 'Lahjoittaa voi vain vahvistettuna keräysaikana, kun keräys on avoinna.' : 'Lahjoitusohjeet julkaistaan, kun tiedot on vahvistettu.'}</p>
    </section>}

    <nav aria-label="Sivun osiot"><ul className="fundraising-chips">
      <li><a href="#miksi">{fundraisingCopy.whyTitle}</a></li>
      <li><a href="#kaytto">Mihin raha menee</a></li>
      <li><a href="#keraus">Keräyksen tiedot</a></li>
      <li><a href="#kysy">Kysy lisää</a></li>
      <li><a href="#jaa">Jaa sivu</a></li>
    </ul></nav>

    <section className="public-content__card" id="miksi" aria-labelledby="why-title">
      <h2 id="why-title">{fundraisingCopy.whyTitle}</h2>
      <ul className="fundraising-why">{fundraisingCopy.why.map(line => <li key={line}>{line}</li>)}</ul>
    </section>

    <section className="public-content__card" id="kaytto" aria-labelledby="purpose-title">
      <h2 id="purpose-title">Mihin raha menee</h2>
      <p>{fundraisingCopy.purposeIntro} {fundraisingCopy.planIntro}</p>
      <div className="fundraising-tiles">
        <div><h3>{fundraisingCopy.stickersTitle}</h3><p>{fundraisingCopy.stickersLead}</p></div>
        <div><h3>{fundraisingCopy.bannersTitle}</h3><p>{fundraisingCopy.banners}</p></div>
        <div><h3>{fundraisingCopy.upkeepTitle}</h3><p>{fundraisingCopy.upkeep}</p></div>
      </div>
      <h3>{fundraisingCopy.secondaryTitle}</h3>
      <p>{fundraisingCopy.secondaryFriendly}</p>
      <details className="fundraising-official">
        <summary>{fundraisingCopy.officialTitle}</summary>
        <p>{fundraisingCopy.officialPrimary}</p>
        <p>{fundraisingCopy.officialSecondary}</p>
      </details>
    </section>

    <section className="public-content__card" id="keraus" aria-labelledby="summary-title">
      <h2 id="summary-title">Keräyksen tiedot</h2>
      <dl className="fundraising-facts">
        <div><dt>Pienkeräysnumero</dt><dd>{f.collection_number || 'Vahvistetaan myöhemmin'}</dd></div>
        <div><dt>Keräysaika</dt><dd>{period}</dd></div>
        <div><dt>Keräysalue</dt><dd>{extras.area || 'Vahvistetaan myöhemmin'}</dd></div>
        <div><dt>Tilanne</dt><dd>{status}</dd></div>
      </dl>
      <h3>Mikä on Mielenosoitukset.fi?</h3>
      <p>Mielenosoitukset.fi kokoaa Suomessa järjestettäviä mielenosoituksia yhteen paikkaan. Palvelusta voit etsiä tulevia tapahtumia ja ilmoittaa mielenosoituksesta muiden löydettäväksi.</p>
      <p>Palvelu on riippumaton, voittoa tavoittelematon vapaaehtoisprojekti. Tavoitteena on helpottaa tapahtumien löytämistä ja osallistumista.</p>
      <a className="public-content__link" href={config.urls.info}>Lue lisää palvelusta <Arrow /></a>
    </section>

    <section className="public-content__card" id="kysy" aria-labelledby="faq-title">
      <h2 id="faq-title">Kysy lisää</h2>
      <details className="fundraising-official"><summary>Voinko lahjoittaa keräysajan jälkeen?</summary>
        <p>Et voi. Lahjoita vain keräysaikana. Keräysajan päätyttyä lahjoitusohjeet poistuvat tältä sivulta.</p></details>
      <details className="fundraising-official"><summary>Mitä jos varoja jää yli?</summary>
        <p>{fundraisingCopy.secondaryFriendly}</p></details>
      <p className="fundraising-contact">Onko sinulla kysyttävää keräyksestä? Kirjoita osoitteeseen <a href="mailto:tuki@mielenosoitukset.fi">tuki@mielenosoitukset.fi</a> tai käytä <a href={config.urls.contact}>yhteydenottolomaketta</a>.</p>
    </section>

    <section className="public-content__card" id="jaa" aria-labelledby="share-title">
      <h2 id="share-title">Jaa sivu</h2>
      <p>Kerro keräyksestä tutuillesi.</p>
      <ShareButton url={pageUrl} title="Pienkeräys – Mielenosoitukset.fi" />
    </section>

    {open && (mobilepayUrl || f.iban) && <StickyDonate href={mobilepayUrl || '#lahjoita'} external={!!mobilepayUrl} />}
  </article>;
}

export const fundraisingData: unknown = data;

/** `data` is the API copy; the bundled file is the fallback when the API is unreachable. */
export default function FundraisingScreen({ config, today, data }: { config: AppConfig; today: string; data?: Record<string, unknown> | null }) {
  return <FundraisingPage details={fundraisingDetails(data ?? fundraisingData, today)} extras={fundraisingExtras(data ?? fundraisingData, today)} config={config} />;
}
