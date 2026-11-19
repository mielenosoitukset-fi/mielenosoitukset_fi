import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import type { AppConfig } from './config';
import type { SessionSummary } from './session';
import { useSession } from './useSession';
import {
  accountBioMax, accountBioWarnAt, accountCopy as copy, accountDeveloperPath, accountGeneralWriteApi, accountProfileWriteApi, accountSettingsApi,
  accountSettingsPath, accountStanding, accountTabs, accountTranslationsUrl, classifyAccountResponse, filterCities,
  generalPayload, nextTabIndex, postBioToGo, postGeneralToGo, postFlaskJson, profilePayload, readAccountSettings, safePicture, tabFromHash,
} from './account-settings-route';
import type { AccountSettingsRead, AccountTabId } from './account-settings-route';
import { formatDeveloperDate } from './developer-route';
import './information/information.css';
import './suggest-change/suggest-change.css';
import './account-settings/account-settings.css';

type Load = { state: 'loading' } | { state: 'anonymous' } | { state: 'unavailable' } | { state: 'ready'; data: AccountSettingsRead };
type Message = { kind: 'error' | 'info'; text: string } | null;

/** The save result, shown under the save button; an expired session offers a reload. */
function SaveMessage({ message }: { message: Message }) {
  return <div aria-live="polite">{message && notice(message.kind, <>
    {message.text}
    {message.text === copy.sessionExpired && <>{' '}<button type="button" className="detail-button" onClick={() => window.location.reload()}>{copy.reloadPage}</button></>}
  </>)}</div>;
}

const notice = (kind: 'error' | 'info', children: ReactNode) =>
  <div className={`flash-message flash-message--inline flash-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
    <span className="flash-text">{children}</span>
  </div>;

/** A searchable list of cities. The trigger opens a listbox; Escape closes it and returns focus to the trigger. */
function CityPicker({ id, value, cities, onChange }: { id: string; value: string; cities: string[]; onChange: (city: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const listId = useId();
  const matches = filterCities(cities, query);
  const close = (returnFocus: boolean) => { setOpen(false); setQuery(''); if (returnFocus) trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) close(false); };
    document.addEventListener('mousedown', outside);
    return () => document.removeEventListener('mousedown', outside);
  }, [open]);
  const options = () => Array.from(root.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
  const onKeys = (event: KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const items = options();
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === 'ArrowDown' ? at + 1 : at - 1;
    event.preventDefault();
    if (next < 0) search.current?.focus(); else items[Math.min(next, items.length - 1)]?.focus();
  };
  const pick = (city: string) => { onChange(city); close(true); };
  return <div className="account-city" ref={root} onKeyDown={onKeys}>
    <button type="button" id={id} ref={trigger} className="account-city__trigger" aria-haspopup="listbox" aria-expanded={open}
      aria-controls={listId} aria-label={`${copy.cityOpen}: ${value || copy.cityChoose}`} onClick={() => open ? close(false) : setOpen(true)}>
      {value || copy.cityChoose}
    </button>
    {open && <div className="account-city__menu">
      <input ref={search} type="search" value={query} aria-label={copy.citySearchLabel} placeholder={copy.citySearch}
        onChange={event => setQuery(event.target.value)} />
      <ul role="listbox" id={listId} aria-label={copy.city} className="account-city__list">
        {matches.map(city => <li key={city} role="option" tabIndex={-1} aria-selected={city === value}
          onClick={() => pick(city)}
          onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(city); } }}>{city}</li>)}
      </ul>
    </div>}
  </div>;
}

function GeneralPanel({ data, report, message }: { data: AccountSettingsRead; report: (message: Message) => void; message: Message }) {
  const [displayName, setDisplayName] = useState(data.general.displayName);
  const [city, setCity] = useState(data.general.city ?? '');
  const [darkMode, setDarkMode] = useState(data.general.darkMode === true);
  const [busy, setBusy] = useState(false);
  const ids = { name: useId(), nameHelp: useId(), city: useId(), language: useId(), dark: useId() };
  // A stored city that is not in today's list stays selectable.
  const cities = city && !data.cities.includes(city) ? [city, ...data.cities] : data.cities;
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    report(null); setBusy(true);
    const payload = generalPayload({ displayName, city, language: 'fi', darkMode });
    // With the Go write route on (token present) the settings go to Go, which also queues the change mail.
    const result = data.csrf ? await postGeneralToGo(data.csrf, payload, copy.saveFailed)
      : await postFlaskJson(accountGeneralWriteApi, payload, copy.saveFailed);
    setBusy(false);
    report({ kind: result.ok ? 'info' : 'error', text: result.message || (result.ok ? '' : copy.saveFailed) });
  };
  return <form className="suggest-change-form" onSubmit={submit}>
    <section className="public-prose-card account-card" aria-labelledby={`${ids.name}-title`}>
      <h2 id={`${ids.name}-title`}>{copy.generalTitle}</h2>
      <p className="account-muted">{copy.generalLead}</p>
      <div className="suggest-change-field">
        <label htmlFor={ids.name}>{copy.displayName}</label>
        <input id={ids.name} value={displayName} maxLength={40} placeholder={copy.displayNamePlaceholder}
          aria-describedby={ids.nameHelp} onChange={event => setDisplayName(event.target.value)} />
        <p className="account-muted" id={ids.nameHelp}>{copy.displayNameHelp}</p>
      </div>
      <div className="suggest-change-field">
        <label htmlFor={ids.city}>{copy.city}</label>
        <CityPicker id={ids.city} value={city} cities={cities} onChange={setCity} />
      </div>
      <div className="suggest-change-field">
        <label htmlFor={ids.language}>{copy.language}</label>
        <select id={ids.language} value="fi" onChange={() => undefined}><option value="fi">{copy.finnish}</option></select>
        {notice('info', <><strong>{copy.translationsTitle}</strong> {copy.translationsBody} {copy.translationsFollow}{' '}
          <a href={accountTranslationsUrl} target="_blank" rel="noopener noreferrer">{copy.translationsLink}</a></>)}
      </div>
      <label className="account-check" htmlFor={ids.dark}>
        <input id={ids.dark} type="checkbox" checked={darkMode} onChange={event => setDarkMode(event.target.checked)} />
        <span>{copy.darkMode}</span>
      </label>
      <div className="detail-actions">
        <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>
          {busy ? copy.saving : copy.save}
        </button>
      </div>
      <SaveMessage message={message} />
    </section>
  </form>;
}

function SecurityPanel({ data }: { data: AccountSettingsRead }) {
  return <div className="account-stack">
    <section className="public-prose-card account-card" aria-labelledby="account-security-title">
      <h2 id="account-security-title">{copy.securityTitle}</h2>
      <p className="account-muted">{copy.securityLead}</p>
      {notice('info', copy.securityLater)}
    </section>
    <section className="public-prose-card account-card" aria-labelledby="account-mfa-title">
      <h2 id="account-mfa-title">{copy.mfaTitle}</h2>
      <p className="account-muted">{copy.mfaLead}</p>
      <p><strong>{data.mfa.enabled ? copy.mfaOn : copy.mfaOff}</strong></p>
      {data.mfa.enabled && <ul className="account-list">
        {data.mfa.devices.map(device => <li key={device.id}>
          <strong>{device.name || copy.mfaDevice}</strong>
          <div className="account-muted">{formatDeveloperDate(device.createdAt)}</div>
        </li>)}
      </ul>}
    </section>
    <section className="public-prose-card account-card" aria-labelledby="account-passkeys-title">
      <h2 id="account-passkeys-title">{copy.passkeysTitle}</h2>
      <p className="account-muted">{copy.passkeysLead}</p>
      {data.passkeys.length ? <ul className="account-list">
        {data.passkeys.map(key => <li key={key.id}>
          <strong>{key.name || copy.passkeyDevice}</strong>
          <div className="account-muted">{formatDeveloperDate(key.createdAt)}
            {key.lastUsedAt && <> · {copy.lastUsed}: {formatDeveloperDate(key.lastUsedAt)}</>}</div>
        </li>)}
      </ul> : <p className="account-muted">{copy.noPasskeys}</p>}
    </section>
    <section className="public-prose-card account-card" aria-labelledby="account-history-title">
      <h2 id="account-history-title">{copy.historyTitle}</h2>
      <div className="account-table-wrap" tabIndex={0} role="region" aria-label={copy.historyTitle}>
        <table className="account-table">
          <thead><tr><th scope="col">{copy.time}</th><th scope="col">{copy.ip}</th><th scope="col">{copy.browser}</th><th scope="col">{copy.result}</th></tr></thead>
          <tbody>
            {data.logins.length ? data.logins.map((login, index) => <tr key={`${login.time}-${index}`}>
              <td>{formatDeveloperDate(login.time)}</td>
              <td>{login.ip}</td>
              <td className="account-table__browser" title={login.ua}>{login.ua.slice(0, 60)}</td>
              <td><span className={`account-badge account-badge--${login.ok ? 'ok' : 'fail'}`}>{login.ok ? copy.loginOk : copy.loginFailed}</span></td>
            </tr>) : <tr><td colSpan={4} className="account-muted">{copy.noLogins}</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  </div>;
}

function TokensPanel() {
  return <section className="public-prose-card account-card" aria-labelledby="account-tokens-title">
    <h2 id="account-tokens-title">{copy.tokensTitle}</h2>
    <p className="account-muted">{copy.tokensLead}</p>
    <div className="detail-actions"><a className="detail-button detail-button--primary" href={accountDeveloperPath}>{copy.tokensPanel}</a></div>
  </section>;
}

function ProfilePanel({ data, report, message }: { data: AccountSettingsRead; report: (message: Message) => void; message: Message }) {
  const [bio, setBio] = useState(data.bio ?? '');
  const [picture, setPicture] = useState(data.picture);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const ids = { bio: useId(), bioHelp: useId(), pic: useId(), picHelp: useId() };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    report(null); setBusy(true);
    let image: string | null = null;
    const chosen = file.current?.files?.[0];
    if (chosen) {
      try { image = await toBase64(chosen); } catch { report({ kind: 'error', text: copy.imageFailed }); setBusy(false); return; }
    }
    // A bio alone goes to Go when it has the write route on; a picture needs S3, so that stays in Flask.
    const viaGo = Boolean(data.csrf) && image === null;
    const result = viaGo ? await postBioToGo(data.csrf!, bio, copy.profileFailed)
      : await postFlaskJson(accountProfileWriteApi, profilePayload(bio, image), copy.profileFailed);
    setBusy(false);
    if (!result.ok) { report({ kind: 'error', text: result.message }); return; }
    const returned = result.body.data as { profile_picture?: unknown } | undefined;
    const next = safePicture(returned?.profile_picture);
    if (next) setPicture(next);
    if (file.current) file.current.value = '';
    report({ kind: 'info', text: result.message || copy.profileSaved });
  };
  return <form className="suggest-change-form" onSubmit={submit}>
    <section className="public-prose-card account-card" aria-labelledby={`${ids.bio}-title`}>
      <h2 id={`${ids.bio}-title`}>{copy.profileTitle}</h2>
      <p className="account-muted">{copy.profileLead}</p>
      <div className="suggest-change-field">
        <label htmlFor={ids.bio}>{copy.bio}</label>
        <textarea id={ids.bio} rows={5} value={bio} maxLength={accountBioMax} aria-describedby={ids.bioHelp} onChange={event => setBio(event.target.value)} />
        <p className="account-muted" id={ids.bioHelp}>{copy.bioHelp}</p>
        {data.csrf && <p className="account-muted" aria-hidden="true">{Array.from(bio).length} / {accountBioMax}</p>}
        {data.csrf && <p className="sr-only" role="status" aria-live="polite">{accountBioMax - Array.from(bio).length <= accountBioWarnAt ? `${accountBioMax - Array.from(bio).length} ${copy.charsLeft}` : ''}</p>}
      </div>
      <div className="suggest-change-field">
        <label htmlFor={ids.pic}>{copy.picture} <span className="account-muted">({copy.optional})</span></label>
        <input id={ids.pic} ref={file} type="file" accept="image/*" aria-describedby={ids.picHelp} />
        <p className="account-muted" id={ids.picHelp}>{copy.pictureHelp}</p>
        {picture && <img className="account-preview" src={picture} alt={copy.preview} referrerPolicy="no-referrer" />}
      </div>
      <div className="detail-actions">
        <button type="submit" className="detail-button detail-button--primary" disabled={busy} aria-busy={busy}>
          {busy ? copy.saving : copy.saveProfile}
        </button>
      </div>
      <SaveMessage message={message} />
    </section>
  </form>;
}

/** The base64 part of a data URL, as the Flask page sends it. */
function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function Tabs({ data }: { data: AccountSettingsRead }) {
  const [active, setActive] = useState<AccountTabId>('general');
  const [message, setMessage] = useState<Message>(null);
  const tabRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  // The hash is only readable in the browser, so the server render always shows the first tab.
  useEffect(() => {
    const read = () => setActive(tabFromHash(window.location.hash));
    read();
    window.addEventListener('hashchange', read);
    return () => window.removeEventListener('hashchange', read);
  }, []);
  const select = (id: AccountTabId, focus: boolean) => {
    setActive(id);
    history.replaceState(null, '', `#${accountTabs.find(tab => tab.id === id)!.panel}`);
    if (focus) tabRefs.current[accountTabs.findIndex(tab => tab.id === id)]?.focus();
  };
  return <>
    <ul className="account-tabs" role="tablist" aria-label={copy.tabsLabel}>
      {accountTabs.map((tab, index) => <li key={tab.id} role="presentation">
        <a id={`settings-tab-${tab.id}`} ref={element => { tabRefs.current[index] = element; }} href={`#${tab.panel}`} role="tab"
          aria-controls={tab.panel} aria-selected={active === tab.id} tabIndex={active === tab.id ? 0 : -1}
          onClick={event => { event.preventDefault(); select(tab.id, false); }}
          onKeyDown={event => {
            const target = nextTabIndex(event.key, index, accountTabs.length);
            if (target === null) return;
            event.preventDefault();
            select(accountTabs[target].id, true);
          }}>{tab.label}</a>
      </li>)}
    </ul>
    {accountTabs.map(tab => <div key={tab.id} id={tab.panel} role="tabpanel" aria-labelledby={`settings-tab-${tab.id}`}
      hidden={active !== tab.id} className="account-panel">
      {tab.id === 'general' && <GeneralPanel data={data} report={setMessage} message={message} />}
      {tab.id === 'security' && <SecurityPanel data={data} />}
      {tab.id === 'tokens' && <TokensPanel />}
      {tab.id === 'profile' && <ProfilePanel data={data} report={setMessage} message={message} />}
    </div>)}
  </>;
}

export default function AccountSettingsScreen({ config, session: initial }: { config: AppConfig; session: SessionSummary }) {
  const { session, status, refresh } = useSession(initial, { endpoint: config.urls.authMe });
  const [load, setLoad] = useState<Load>(initial.authenticated || initial.unverified ? { state: 'loading' } : { state: 'anonymous' });
  const [reloads, setReloads] = useState(0);
  const standing = accountStanding(initial, status, session);
  const bridgeFailed = standing === 'unknown' && status === 'unavailable';

  useEffect(() => {
    if (standing === 'signed-out') { setLoad({ state: 'anonymous' }); return; }
    if (standing === 'unknown') { setLoad(bridgeFailed ? { state: 'unavailable' } : { state: 'loading' }); return; }
    const controller = new AbortController();
    setLoad({ state: 'loading' });
    (async () => {
      try {
        const response = await fetch(accountSettingsApi,
          { credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' } });
        const kind = classifyAccountResponse(response.status);
        if (kind === 'signed-out') { setLoad({ state: 'anonymous' }); return; }
        if (kind !== 'ok') throw new Error('unavailable');
        const data = readAccountSettings(await response.json());
        setLoad(data ? { state: 'ready', data } : { state: 'unavailable' });
      } catch {
        if (!controller.signal.aborted) setLoad({ state: 'unavailable' });
      }
    })();
    return () => controller.abort();
  }, [standing, bridgeFailed, reloads]);

  return <article className="public-page-shell information-page suggest-change-page account-page" aria-labelledby="account-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="account-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    {load.state === 'loading' && <p role="status">{copy.loading}</p>}
    {load.state === 'anonymous' && <>
      {notice('info', copy.loginLead)}
      <div className="detail-actions"><a className="detail-button detail-button--primary"
        href={`${config.urls.login}?next=${encodeURIComponent(accountSettingsPath)}`}>{copy.login}</a></div>
    </>}
    {load.state === 'unavailable' && <>
      {notice('error', copy.unavailable)}
      <div className="detail-actions"><button type="button" className="detail-button"
        onClick={() => { if (standing === 'unknown') void refresh(); else setReloads(count => count + 1); }}>{copy.retry}</button></div>
    </>}
    {load.state === 'ready' && <Tabs data={load.data} />}
  </article>;
}
