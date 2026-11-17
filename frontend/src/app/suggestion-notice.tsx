import { useEffect, useState } from 'react';

/**
 * Notice shown after the suggestion forms. The Go handlers cannot set Flask's
 * flash cookie, so they redirect with `?suggestion=<flag>` and this turns the
 * flag into the same message Flask flashes. Unknown flags show nothing.
 */
export type SuggestionNotice = { category: 'success' | 'error'; message: string };

const notices: Record<string, SuggestionNotice> = {
  saved: { category: 'success', message: 'Ehdotuksesi on tallennettu. Tarkistamme sen pian. Kiitos.' },
  sent: { category: 'success', message: 'Ehdotuksesi on vastaanotettu. Kiitos.' },
  empty: { category: 'error', message: 'Muuta vähintään yhden kentän tietoja tai kirjoita kommentti ennen lähettämistä.' },
  failed: { category: 'error', message: 'Ehdotuksen tallentaminen epäonnistui. Yritä uudelleen.' },
  too_long: { category: 'error', message: 'Kuvaus on liian pitkä. Lyhennä sitä ja yritä uudelleen.' },
};

export function suggestionNoticeFromSearch(search: string): SuggestionNotice | null {
  const flag = new URLSearchParams(search).get('suggestion');
  return flag !== null && Object.hasOwn(notices, flag) ? notices[flag] : null;
}

/** Read after hydration so the server-rendered and first client render match. */
export function SuggestionNoticeBanner() {
  const [notice, setNotice] = useState<SuggestionNotice | null>(null);
  useEffect(() => setNotice(suggestionNoticeFromSearch(window.location.search)), []);
  if (!notice) return null;
  return <div className={`flash-message flash-message--inline flash-${notice.category}`}
    role={notice.category === 'error' ? 'alert' : 'status'}><span className="flash-text">{notice.message}</span></div>;
}
