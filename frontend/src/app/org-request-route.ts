/** Organization account requests (docs/organization-requests.md, slice O2). */
/** The request form. Must equal config.urls.orgRequest. */
export const orgRequestPath = '/organisaatiot/hae';
/** Same-origin POST target that Apache routes to Go's POST /api/organization-requests. */
export const orgRequestSubmitPath = '/organisaatiot/hae';
/** Same-origin GET of the signed-in account's requests, routed to Go. */
export const orgRequestMinePath = '/organisaatiot/hakemukset';
/** The status page. The link's fragment holds the token, so it never reaches a server. */
export const orgRequestStatusPath = '/organisaatiot/hakemus';
/** Same-origin POST that reads one request by its token, routed to Go. */
export const orgRequestStatusReadPath = '/organisaatiot/hakemus';

export const orgRequestCopy = {
  title: 'Hae organisaatiotiliä',
  description: 'Organisaatio voi ilmoittaa mielenosoituksensa omalla nimellään ja hallita tietojaan. Hakemus käsitellään ennen kuin tili avataan.',
  statusTitle: 'Hakemuksen tila',
  noscript: 'Lomake vaatii JavaScriptin. Voit myös ottaa yhteyttä ylläpitoon yhteydenottolomakkeella.',
};

export type OrgRequestSummary = {
  id: string; organization: string; represents: boolean; onboarding: boolean;
  access?: string; status: string; message?: string; created_at: string; updated_at: string;
};

export const orgRequestStatusLabels: Record<string, string> = {
  unconfirmed: 'Odottaa sähköpostin vahvistusta',
  received: 'Vastaanotettu',
  verifying: 'Organisaatiota vahvistetaan',
  in_review: 'Käsittelyssä',
  needs_info: 'Tarvitsemme lisätietoja',
  approved: 'Hyväksytty',
  rejected: 'Hylätty',
  withdrawn: 'Peruttu',
};

/** What each state means for the applicant, shown on the status page. */
export const orgRequestStatusHelp: Record<string, string> = {
  received: 'Hakemus on jonossa. Käsittelemme sen mahdollisimman pian.',
  verifying: 'Varmistamme, että organisaatio on hakemuksen takana. Ota yhteys organisaation sähköpostista tai virallisesta somesta, tai vahvista viestimme, jonka lähetämme organisaatiolle.',
  in_review: 'Ylläpito käsittelee hakemusta.',
  needs_info: 'Tarvitsemme lisätietoja. Saat meiltä viestin.',
  approved: 'Hakemus hyväksyttiin. Lähetämme kutsun tilin käyttöön ottamiseen.',
  rejected: 'Hakemusta ei hyväksytty. Saat perustelut sähköpostilla. Voit pyytää päätöksen uudelleenkäsittelyä 30 päivän kuluessa.',
  withdrawn: 'Hakemus on peruttu.',
};

export const orgRequestAccessLabels: Record<string, string> = {
  organization_account: 'Organisaatiotili',
  own_account: 'Oma käyttäjätilini',
  invite: 'Kutsulinkki toiseen osoitteeseen',
};
