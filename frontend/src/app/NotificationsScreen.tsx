import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { AppConfig } from './config';
import type { SessionSummary } from './session';
import { Icon } from '../components/header/NavigationLink';
import { notificationIcons } from '../components/header/NotificationMenu';
import {
  classifyNoticesResponse, formatNoticeTime, noticeHref, notificationsCopy as copy, notificationsPageLimit, readNotices,
} from './notifications-route';
import type { Notice } from './notifications-route';
import './information/information.css';
import './notifications/notifications.css';

type Load = { state: 'loading' } | { state: 'anonymous' } | { state: 'unavailable' } | { state: 'ready'; notices: Notice[] };

const notice = (kind: 'error' | 'info', children: ReactNode) =>
  <div className={`flash-message flash-message--inline flash-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
    <span className="flash-text">{children}</span>
  </div>;

function NoticeRow({ item, locale }: { item: Notice; locale: string }) {
  const [href, setHref] = useState<string | null>(null);
  // The link is checked against the page's own origin, which exists only in the browser.
  useEffect(() => { setHref(noticeHref(item.link, window.location.origin)); }, [item.link]);
  const body = <>
    <span className="notifications-item__icon"><Icon name={notificationIcons[item.type] || 'fa-circle-info'} /></span>
    <span className="notifications-item__body">
      <span className="notifications-item__message">{item.message}</span>
      <span className="notifications-item__meta">
        <time dateTime={item.time}>{formatNoticeTime(item.time, locale)}</time>
        {!item.read && <span className="notifications-item__badge">{copy.unread}</span>}
      </span>
    </span>
  </>;
  const className = `notifications-item${item.read ? '' : ' is-unread'}`;
  return <li>{href ? <a className={className} href={href}>{body}</a> : <div className={className}>{body}</div>}</li>;
}

export default function NotificationsScreen({ config, session }: { config: AppConfig; session: SessionSummary }) {
  const signedIn = session.authenticated || Boolean(session.unverified);
  const [load, setLoad] = useState<Load>(signedIn ? { state: 'loading' } : { state: 'anonymous' });
  const [reloads, setReloads] = useState(0);
  const [marking, setMarking] = useState(false);
  const [markError, setMarkError] = useState(false);
  const locale = config.locale === 'fi' ? 'fi-FI' : config.locale;

  useEffect(() => {
    if (!signedIn) return;
    const controller = new AbortController();
    setLoad({ state: 'loading' });
    (async () => {
      try {
        const response = await fetch(`${config.urls.notifications.list}?limit=${notificationsPageLimit}`, {
          credentials: 'same-origin', cache: 'no-store', redirect: 'manual', signal: controller.signal, headers: { Accept: 'application/json' },
        });
        const kind = classifyNoticesResponse(response);
        if (kind === 'signed-out') { setLoad({ state: 'anonymous' }); return; }
        if (kind !== 'ok') throw new Error('unavailable');
        const notices = readNotices(await response.json());
        setLoad(notices ? { state: 'ready', notices } : { state: 'unavailable' });
      } catch {
        if (!controller.signal.aborted) setLoad({ state: 'unavailable' });
      }
    })();
    return () => controller.abort();
  }, [signedIn, reloads, config.urls.notifications.list]);

  async function markAllRead() {
    if (marking) return;
    setMarking(true); setMarkError(false);
    try {
      const response = await fetch(config.urls.notifications.markRead, {
        method: 'POST', credentials: 'same-origin', redirect: 'manual', headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error('mark read failed');
      setLoad(current => current.state === 'ready'
        ? { state: 'ready', notices: current.notices.map(item => ({ ...item, read: true })) } : current);
    } catch {
      setMarkError(true);
    } finally {
      setMarking(false);
    }
  }

  const unread = load.state === 'ready' ? load.notices.filter(item => !item.read).length : 0;
  return <article className="public-page-shell information-page notifications-page" aria-labelledby="notifications-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="notifications-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    {load.state === 'loading' && <p role="status">{copy.loading}</p>}
    {load.state === 'anonymous' && <>
      {notice('info', copy.loginLead)}
      <div className="detail-actions"><a className="detail-button detail-button--primary"
        href={config.urls.login}>{copy.login}</a></div>
    </>}
    {load.state === 'unavailable' && <>
      {notice('error', copy.unavailable)}
      <div className="detail-actions"><button type="button" className="detail-button"
        onClick={() => setReloads(count => count + 1)}>{copy.retry}</button></div>
    </>}
    {load.state === 'ready' && <section aria-label={copy.title}>
      {unread > 0 && <div className="notifications-toolbar">
        <button type="button" className="detail-button" disabled={marking} onClick={() => void markAllRead()}>
          {marking ? copy.marking : copy.markRead}
        </button>
      </div>}
      <div aria-live="polite">{markError && notice('error', copy.markError)}</div>
      {load.notices.length
        ? <ul className="notifications-list">{load.notices.map((item, index) => <NoticeRow key={item.id || index} item={item} locale={locale} />)}</ul>
        : <p className="notifications-empty notifications-list" role="status">{copy.empty}</p>}
    </section>}
  </article>;
}
