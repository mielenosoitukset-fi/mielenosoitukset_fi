import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { AppConfig } from './config';
import type { SessionSummary } from './session';
import {
  classifyProfileResponse, defaultProfilePicture, profileActionApi, profileActionMode, profileApi, profileCopy as copy,
  profilePath, profileShareMessage, readProfile,
} from './profile-route';
import type { ProfileAction, ProfileInitial, ProfilePerson, ProfileRead } from './profile-route';
import './profile/profile.css';

type Dialog = 'followers' | 'following' | 'share' | null;

function Avatar({ src, alt, className }: { src: string | null; alt: string; className: string }) {
  const [failed, setFailed] = useState(false);
  return <img className={className} src={failed || !src ? defaultProfilePicture : src} alt={alt} loading="lazy"
    referrerPolicy="no-referrer" onError={() => setFailed(true)} />;
}

const notice = (kind: 'error' | 'info', children: ReactNode) =>
  <div className={`flash-message flash-message--inline flash-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
    <span className="flash-text">{children}</span>
  </div>;

/** A modal built on the native dialog element: focus stays inside, Escape closes, focus returns to the opener. */
function ProfileDialog({ title, close, children }: { title: string; close(): void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useId();
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.showModal();
    return () => { ref.current?.close(); if (trigger?.isConnected) trigger.focus({ preventScroll: true }); };
  }, []);
  return <dialog ref={ref} className="detail-action-dialog profile-dialog" aria-labelledby={heading}
    onCancel={event => { event.preventDefault(); close(); }}
    onClick={event => { if (event.target === ref.current) close(); }}>
    <header>
      <h2 id={heading}>{title}</h2>
      <button type="button" className="detail-button detail-button--quiet" onClick={close}>{copy.close}</button>
    </header>
    {children}
  </dialog>;
}

function PeopleList({ people, empty }: { people: ProfilePerson[]; empty: string }) {
  if (!people.length) return <p className="profile-muted">{empty}</p>;
  return <ul className="profile-people">
    {people.map((person, index) => <li key={`${person.username}-${index}`}>
      <a className="profile-person" href={profilePath(person.username)}>
        <Avatar className="profile-person__avatar" src={person.picture} alt={`${copy.picture}: ${person.displayname}`} />
        <span className="profile-person__names">
          <span className="profile-person__display">{person.displayname}</span>
          <span className="profile-person__username">@{person.username}</span>
        </span>
      </a>
    </li>)}
  </ul>;
}

function ShareDialog({ url, close }: { url: string; close(): void }) {
  const message = profileShareMessage(url);
  const linkId = useId();
  const messageId = useId();
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const copyText = async () => {
    try { await navigator.clipboard.writeText(message); setState('copied'); } catch { setState('failed'); }
  };
  return <ProfileDialog title={copy.shareTitle} close={close}>
    <p>{copy.shareLead}</p>
    <div className="detail-form">
      <label htmlFor={linkId}>{copy.shareLink}</label>
      <input id={linkId} type="text" readOnly value={url} onFocus={event => event.currentTarget.select()} />
      <label htmlFor={messageId}>{copy.shareMessage}</label>
      <textarea id={messageId} rows={4} readOnly value={message} onFocus={event => event.currentTarget.select()} />
    </div>
    <p className="profile-toast" role="status">{state === 'copied' ? copy.copied : state === 'failed' ? copy.copyFailed : ''}</p>
    <div className="detail-actions">
      <button type="button" className="detail-button" onClick={close}>{copy.close}</button>
      <button type="button" className="detail-button detail-button--primary" onClick={copyText}>{copy.copy}</button>
    </div>
  </ProfileDialog>;
}

function OwnLists({ profile, config }: { profile: ProfileRead; config: AppConfig }) {
  return <>
    <section className="profile-section" aria-labelledby="profile-organizations">
      <h2 id="profile-organizations">{copy.organizations}</h2>
      {profile.organizations.length ? <div className="profile-cards">
        {profile.organizations.map(org => <a key={org.id} className="profile-item" href={`/organization/${org.id}`}>
          <span className="profile-item__head">
            <span className="profile-item__mark" aria-hidden="true">
              {org.logo ? <img src={org.logo} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <i className="fa-solid fa-building" />}
            </span>
            <span className="profile-item__title">
              <span className="profile-item__name">{org.name}</span>
              {org.city && <span className="profile-muted">{org.city}</span>}
            </span>
          </span>
          {org.description && <span className="profile-item__text">{org.description}</span>}
          <span className="profile-item__link">{copy.showOrganization}</span>
        </a>)}
      </div> : <p className="profile-muted">{copy.noOrganizations}</p>}
    </section>
    <section className="profile-section" aria-labelledby="profile-recurring">
      <h2 id="profile-recurring">{copy.recurring}</h2>
      {profile.recurring.length ? <div className="profile-cards">
        {profile.recurring.map(demo => <a key={demo.id} className="profile-item"
          href={config.urls.series.replace('__ID__', demo.id)}>
          <span className="profile-item__head">
            <span className="profile-item__mark" aria-hidden="true"><i className="fa-solid fa-bullhorn" /></span>
            <span className="profile-item__title">
              <span className="profile-item__name">{demo.title}</span>
              {demo.date && <span className="profile-muted">{demo.date}</span>}
            </span>
          </span>
          <span className="profile-item__text">{[demo.city, demo.address].filter(Boolean).join(' · ')}</span>
          <span className="profile-item__link">{copy.openRecurring}</span>
        </a>)}
      </div> : <p className="profile-muted">{copy.noRecurring}</p>}
    </section>
  </>;
}

type Props = { config: AppConfig; session: SessionSummary; username: string; initial: ProfileInitial };

export default function ProfileScreen({ config, session, username, initial }: Props) {
  // The server read has no cookie. `fresh` is the browser's own read, which knows the owner and the viewer.
  const [fresh, setFresh] = useState<ProfileRead | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [reloads, setReloads] = useState(0);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [dialog, setDialog] = useState<Dialog>(null);
  const missing = initial.status === 'not-found';

  useEffect(() => {
    if (missing) return;
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(profileApi(username), { credentials: 'same-origin', cache: 'no-store',
          signal: controller.signal, headers: { Accept: 'application/json' } });
        if (classifyProfileResponse(response.status, response.headers.get('content-type')) !== 'ok') throw new Error('unavailable');
        const profile = readProfile(await response.json(), username);
        if (!profile) throw new Error('malformed');
        setFresh(profile); setReadFailed(false);
      } catch {
        if (!controller.signal.aborted) setReadFailed(true);
      }
    })();
    return () => controller.abort();
  }, [username, missing, reloads]);

  if (initial.status === 'not-found') {
    return <article className="profile-page" aria-labelledby="profile-missing">
      <h1 id="profile-missing" className="profile-missing">{copy.notFound}</h1>
      <div className="detail-actions"><a className="detail-button" href={config.urls.home}>{copy.home}</a></div>
    </article>;
  }

  const profile = fresh ?? initial.profile;
  const mode = profileActionMode(fresh, session);
  const pageUrl = `${config.origin}${profilePath(profile.username)}`;
  const loginUrl = `${config.urls.login}?next=${encodeURIComponent(profilePath(profile.username))}`;
  const act = async (action: ProfileAction) => {
    setBusy(true); setActionError('');
    try {
      const response = await fetch(profileActionApi[action], { method: 'POST', credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: profile.username }) });
      if (!response.ok) setActionError(copy.actionFailed);
    } catch { setActionError(copy.actionFailed); }
    setBusy(false);
    setReloads(count => count + 1);
  };
  const button = (label: string, action: ProfileAction, className = 'detail-button') =>
    <button type="button" className={className} disabled={busy} onClick={() => void act(action)}>{label}</button>;

  return <article className="profile-page" aria-labelledby="profile-name">
    <section className="profile-header">
      <div className="profile-banner" aria-hidden="true" />
      <div className="profile-header__body">
        <Avatar className="profile-avatar" src={profile.picture} alt={`${copy.picture}: ${profile.displayname}`} />
        <div className="profile-details">
          <h1 id="profile-name" className="profile-name">{profile.displayname}</h1>
          {profile.adminBadge && <p className="profile-badge"><i className="fa-solid fa-shield-halved" aria-hidden="true" /> {copy.adminBadge}</p>}
          {profile.bio && <p className="profile-bio">{profile.bio}</p>}
          <ul className="profile-stats">
            <li className="profile-stat">
              <span className="profile-stat__value">{profile.postsCount}</span>
              <span className="profile-stat__label">{copy.posts}</span>
            </li>
            <li><button type="button" className="profile-stat profile-stat--button" aria-haspopup="dialog" onClick={() => setDialog('followers')}>
              <span className="profile-stat__value">{profile.followersCount}</span>
              <span className="profile-stat__label">{copy.followers}</span>
            </button></li>
            <li><button type="button" className="profile-stat profile-stat--button" aria-haspopup="dialog" onClick={() => setDialog('following')}>
              <span className="profile-stat__value">{profile.followingCount}</span>
              <span className="profile-stat__label">{copy.following}</span>
            </button></li>
          </ul>
          {mode === 'self' && <div className="detail-actions profile-actions">
            <a className="detail-button" href={config.urls.settings}>{copy.edit}</a>
            <button type="button" className="detail-button detail-button--primary" aria-haspopup="dialog" onClick={() => setDialog('share')}>{copy.share}</button>
          </div>}
          {mode === 'viewer' && fresh?.viewer && <div className="detail-actions profile-actions">
            {fresh.viewer.following
              ? button(copy.unfollow, 'unfollow')
              : button(copy.follow, 'follow', 'detail-button detail-button--primary')}
            {fresh.viewer.friendState === 'none' && button(copy.addFriend, 'sendRequest')}
            {fresh.viewer.friendState === 'incoming_request' && <>
              {button(copy.accept, 'accept', 'detail-button detail-button--primary')}
              {button(copy.reject, 'reject', 'detail-button detail-button--danger')}
            </>}
            {fresh.viewer.friendState === 'outgoing_request' && <button type="button" className="detail-button" disabled>{copy.requestSent}</button>}
            {fresh.viewer.friendState === 'friends' && <span className="profile-friend">{copy.friend}</span>}
          </div>}
          {mode === 'anonymous' && <div className="detail-actions profile-actions">
            <a className="detail-button detail-button--primary" href={loginUrl} title={copy.loginNeeded}>{copy.follow}</a>
            <a className="detail-button" href={loginUrl} title={copy.loginNeeded}>{copy.addFriend}</a>
          </div>}
          {actionError && notice('error', actionError)}
          {readFailed && <>
            {notice('error', copy.unavailable)}
            <div className="detail-actions"><button type="button" className="detail-button" onClick={() => setReloads(count => count + 1)}>{copy.retry}</button></div>
          </>}
        </div>
      </div>
    </section>

    <section className="profile-section" aria-labelledby="profile-posts">
      <h2 id="profile-posts">{copy.posts}</h2>
      <p className="profile-muted">{copy.noPosts}</p>
    </section>

    {profile.self && <OwnLists profile={profile} config={config} />}

    {dialog === 'followers' && <ProfileDialog title={copy.followers} close={() => setDialog(null)}>
      <PeopleList people={profile.followers} empty={copy.noFollowers} />
    </ProfileDialog>}
    {dialog === 'following' && <ProfileDialog title={copy.following} close={() => setDialog(null)}>
      <PeopleList people={profile.following} empty={copy.noFollowing} />
    </ProfileDialog>}
    {dialog === 'share' && profile.self && <ShareDialog url={pageUrl} close={() => setDialog(null)} />}
  </article>;
}
