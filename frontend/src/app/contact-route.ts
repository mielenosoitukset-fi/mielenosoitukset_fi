export const contactPath = '/contact';

export const contactCopy = {
  title: 'Ota yhteyttä',
  description: 'Jos sinulla on kysyttävää tai tarvitset apua, voit ottaa meihin yhteyttä seuraavilla tavoilla:',
};

export type ContactNotice = { category: 'success' | 'error' | 'warning'; message: string };

/**
 * The Go `POST /contact` cannot set Flask's flash cookie, so it redirects with
 * `?contact=<flag>`. Same texts as the Flask flashes; unknown flags show nothing.
 */
const contactNotices: Record<string, ContactNotice> = {
  sent: { category: 'success', message: 'Yhteydenottopyyntö välitetty onnistuneesti.' },
  invalid: { category: 'error', message: 'Tarkista lomakkeen tiedot ja yritä uudelleen.' },
  disabled: { category: 'warning', message: 'Yhteydenottolomake on tilapäisesti poissa käytöstä. Voit lähettää sähköpostia tukeen.' },
};

export function contactNoticeFromSearch(search: string): ContactNotice | null {
  const flag = new URLSearchParams(search).get('contact');
  return flag !== null && Object.hasOwn(contactNotices, flag) ? contactNotices[flag] : null;
}
