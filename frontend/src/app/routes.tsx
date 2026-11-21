import type { AppConfig } from './config';
import type { Catalog } from './i18n';
import type { ListingRoute } from './listing';
import { currentMonth } from './calendar';
import { findCity } from './cities';
import { informationPageForPath, informationPages } from './information';
import { contactCopy, contactPath } from './contact-route';
import { campaignCopy, campaignPath } from './campaign-route';
import { cityAdminCopy, cityAdminPath } from './city-admin-route';
import { developerCopy, developerPath } from './developer-route';
import { volunteerConfirmCopy } from './volunteer-confirm';
import { emailConfirmCopy } from './email-confirm';
import { orgInviteCopy } from './org-invite';
import { notificationsCopy, notificationsPath } from './notifications-route';
import type { InformationPage } from './information';
import { passwordResetRequestCopy, passwordResetRequestPath } from './password-reset-route';
import { loginCopy, loginPath } from './login-route';
import { registerCopy, registerPath } from './register-route';
import { passwordResetCopy, resetTokenFromPath } from './password-reset-token-route';
import { forcedIdentityCopy, forcedIdentityPath, forcedPasswordCopy, forcedPasswordPath } from './forced-route';
import { nextStepsCopy, nextStepsPath } from './register-next-steps-route';
import { logoutCopy, logoutPath } from './logout-route';

/**
 * Public routes owned by the standalone React application.
 *
 * Only routes listed here are served by the React host. Everything else keeps
 * its existing Flask handler, so an unknown path is never claimed here. The
 * detail route claims exactly one path segment and the series route claims the
 * `/children` path; `/demonstration/<id>/ics`, `/demonstration/<id>/some` and a
 * trailing slash all stay on Flask.
 */
export type AppRoute =
  | { name: 'home' }
  | { name: 'status' }
  | { name: 'submit' }
  | { name: 'god-auth' }
  | { name: 'pride' }
  | { name: 'information'; page: InformationPage }
  | { name: 'contact' }
  | { name: 'city-admin' }
  | { name: 'api-docs' }
  | { name: 'campaign' }
  | { name: 'fundraising' }
  | { name: 'cities' }
  | { name: 'calendar' | 'calendar-year'; year: number; month: number; city?: string }
  | { name: 'today'; value?: string }
  | ListingRoute
  | { name: 'organization'; id: string }
  | { name: 'organization-fill'; id: string }
  | { name: 'suggest-change'; id: string }
  | { name: 'cancel-link'; token: string }
  | { name: 'volunteer-confirm'; token: string }
  | { name: 'email-confirm'; token: string }
  | { name: 'org-invite' }
  | { name: 'developer'; appId?: string }
  | { name: 'profile'; username: string }
  | { name: 'account-settings' }
  | { name: 'notifications' }
  | { name: 'password-reset-request' }
  | { name: 'login' }
  | { name: 'register' }
  | { name: 'forced-password' }
  | { name: 'forced-identity' }
  | { name: 'register-next-steps' }
  | { name: 'logout' }
  | { name: 'password-reset'; token: string }
  | { name: 'detail'; identifier: string }
  | { name: 'series'; parent: string };

export type RouteMeta = {
  title: string;
  description: string;
  robots: string;
  canonical: string | null;
  /** Social preview image; the default site image when absent. */
  image?: string | null;
};

/** Public usernames the page claims; `edit` is the old settings redirect and stays in Flask. */
const PROFILE_PATH = /^\/users\/profile\/([a-z0-9_-]{3,30})\/?$/;
const DETAIL_PATH = /^\/demonstration\/([^/]+)$/;
const SERIES_PATH = /^\/demonstration\/([^/]+)\/children$/;

function readIdentifier(raw: string): string | null {
  try {
    const identifier = decodeURIComponent(raw);
    // The API caps resolution at 200 characters; reject anything that cannot be
    // an identifier before the host makes a request.
    if (!identifier || identifier.length > 200) return null;
    if (/[\u0000-\u001f\u007f]/.test(identifier)) return null;
    return identifier;
  } catch {
    return null;
  }
}

export function routeForPath(path: string): AppRoute | null {
  if (path === '/status') return { name: 'status' };
  const informationPage = informationPageForPath(path);
  if (informationPage) return { name: 'information', page: informationPage };
  if (path === '/auth/god') return { name: 'god-auth' };
  if (path === contactPath) return { name: 'contact' };
  if (path === cityAdminPath) return { name: 'city-admin' };
  if (path === '/api-docs/') return { name: 'api-docs' };
  if (path === campaignPath) return { name: 'campaign' };
  if (path === '/keräys' || path === '/ker%C3%A4ys' || path === '/ker%c3%a4ys') return { name: 'fundraising' };
  const calendar = /^\/calendar(?:\/(\d{4})(?:\/(\d{1,2}))?)?\/?$/.exec(path);
  const scoped = /^\/city\/([^/]+)\/calendar(?:\/(\d{4})(?:\/(\d{1,2}))?)?\/?$/.exec(path);
  if (calendar || scoped) {
    const city = scoped ? findCity(readIdentifier(scoped[1]) ?? '') : undefined;
    if (scoped && !city) return null;
    const yearValue = scoped ? scoped[2] : calendar![1];
    const monthValue = scoped ? scoped[3] : calendar![2];
    const current = currentMonth();
    const year = yearValue ? Number(yearValue) : current.year;
    const month = monthValue ? Number(monthValue) : yearValue ? 0 : current.month;
    if (year < 2000 || year > 2100 || month < 0 || month > 12 || monthValue === '0' || monthValue === '00') return null;
    return { name: month ? 'calendar' : 'calendar-year', year, month, ...(city ? { city: city.key } : {}) };
  }
  if (path === '/submit') return { name: 'submit' };
  if (path === '/pride-nakyvaksi' || path === '/pride' || path === '/pride/') return { name: 'pride' };
  if (path === '/cities') return { name: 'cities' };
  if (path === '/mielenosoitukset-tanaan') return { name: 'today' };
  const today = /^\/city\/([^/]+)\/tanaan$/.exec(path);
  if (today) {
    const value = readIdentifier(today[1]);
    return value && !value.includes('/') ? { name: 'today', value } : null;
  }
  const organizationFill = /^\/organization\/([a-f\d]{24})\/fill$/i.exec(path);
  if (organizationFill) return { name: 'organization-fill', id: organizationFill[1].toLowerCase() };
  const suggestChange = /^\/suggest_change\/([a-f\d]{24})$/i.exec(path);
  if (suggestChange) return { name: 'suggest-change', id: suggestChange[1].toLowerCase() };
  if (path === developerPath || path === `${developerPath}/`) return { name: 'developer' };
  const developerApp = /^\/developer\/apps\/([a-f\d]{24})$/i.exec(path);
  if (developerApp) return { name: 'developer', appId: developerApp[1].toLowerCase() };
  if (path === '/users/auth/settings' || path === '/users/auth/settings/') return { name: 'account-settings' };
  if (path === notificationsPath) return { name: 'notifications' };
  if (path === passwordResetRequestPath) return { name: 'password-reset-request' };
  if (path === loginPath) return { name: 'login' };
  if (path === registerPath) return { name: 'register' };
  if (path === forcedPasswordPath) return { name: 'forced-password' };
  if (path === forcedIdentityPath) return { name: 'forced-identity' };
  if (path === nextStepsPath) return { name: 'register-next-steps' };
  if (path === logoutPath) return { name: 'logout' };
  const resetToken = resetTokenFromPath(path);
  if (resetToken) return { name: 'password-reset', token: resetToken };
  const profile = PROFILE_PATH.exec(path);
  if (profile) return profile[1] === 'edit' ? null : { name: 'profile', username: profile[1] };
  const cancelLink = /^\/cancel_demonstration\/([A-Za-z0-9_-]{1,128})$/.exec(path);
  if (cancelLink) return { name: 'cancel-link', token: cancelLink[1] };
  const volunteerConfirm = /^\/kampanja\/confirm\/([A-Za-z0-9_-]{1,128})$/.exec(path);
  if (volunteerConfirm) return { name: 'volunteer-confirm', token: volunteerConfirm[1] };
  if (path === '/users/orgs/accept_invite') return { name: 'org-invite' };
  const emailConfirm = /^\/users\/auth\/confirm_email\/([A-Za-z0-9_.-]{5,3100})$/.exec(path);
  if (emailConfirm) return { name: 'email-confirm', token: emailConfirm[1] };
  const organization = /^\/organization\/([a-f\d]{24})$/i.exec(path);
  if (organization) return { name: 'organization', id: organization[1].toLowerCase() };
  if (path === '/demonstrations') return { name: 'listing', scope: 'all' };
  const listing = /^\/(city|tag)\/([^/]+)$/.exec(path);
  if (listing) {
    const value = readIdentifier(listing[2]);
    return value && !value.includes('/') ? { name: 'listing', scope: listing[1] as 'city' | 'tag', value } : null;
  }
  const series = SERIES_PATH.exec(path);
  if (series) {
    const parent = readIdentifier(series[1]);
    return parent && /^[a-f\d]{24}$/i.test(parent) ? { name: 'series', parent } : null;
  }
  const match = DETAIL_PATH.exec(path);
  if (match) {
    const identifier = readIdentifier(match[1]);
    return identifier ? { name: 'detail', identifier } : null;
  }
  const clean = path.replace(/\/+$/, '') || '/';
  if (clean === '/') return { name: 'home' };
  return null;
}

/**
 * Metadata is the only part of the page rendered on the server. The home route
 * needs no event request; its head is static. The detail route supplies its own
 * metadata from one projected API read.
 */
export function routeMeta(route: AppRoute, config: AppConfig, text: Catalog): RouteMeta {
  if (route.name === 'api-docs') {
    return { title: 'API-dokumentaatio | Mielenosoitukset.fi',
      description: 'Mielenosoitukset.fi - Tietoa mielenosoituksista ja aktivismista Suomessa.',
      robots: 'index, follow', canonical: null };
  }
  if (route.name === 'developer') {
    return { title: `${developerCopy.title} | Mielenosoitukset.fi`, description: developerCopy.description,
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'volunteer-confirm') {
    return { title: `${volunteerConfirmCopy.title} | Mielenosoitukset.fi`, description: volunteerConfirmCopy.title,
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'email-confirm') {
    return { title: `${emailConfirmCopy.title} | Mielenosoitukset.fi`, description: emailConfirmCopy.title,
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'org-invite') {
    return { title: `${orgInviteCopy.title} | Mielenosoitukset.fi`, description: orgInviteCopy.title,
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'account-settings') {
    return { title: 'Asetukset | Mielenosoitukset.fi', description: 'Hallinnoi tilisi tietoja, turvallisuutta, profiilia ja API-avaimia yhdessä paikassa.',
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'notifications') {
    return { title: `${notificationsCopy.title} | Mielenosoitukset.fi`, description: notificationsCopy.lead,
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'login') {
    return { title: `${loginCopy.title} | Mielenosoitukset.fi`, description: loginCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'password-reset') {
    return { title: `${passwordResetCopy.title} | Mielenosoitukset.fi`, description: passwordResetCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'forced-password') {
    return { title: `${forcedPasswordCopy.title} | Mielenosoitukset.fi`, description: forcedPasswordCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'forced-identity') {
    return { title: `${forcedIdentityCopy.title} | Mielenosoitukset.fi`, description: forcedIdentityCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'register-next-steps') {
    return { title: `${nextStepsCopy.title} | Mielenosoitukset.fi`, description: nextStepsCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'logout') {
    return { title: `Kirjaudu ulos | Mielenosoitukset.fi`, description: logoutCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'register') {
    return { title: `${registerCopy.title} | Mielenosoitukset.fi`, description: registerCopy.lead, robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'password-reset-request') {
    return { title: `${passwordResetRequestCopy.title} | Mielenosoitukset.fi`, description: passwordResetRequestCopy.lead,
      robots: 'noindex, nofollow', canonical: null };
  }
  if (route.name === 'profile') {
    // The server replaces the title with the account's name once the profile is read.
    return { title: 'Profiili | Mielenosoitukset.fi', description: 'Mielenosoitukset.fi-käyttäjän profiili.',
      robots: 'noindex, follow', canonical: null };
  }
  if (route.name === 'city-admin') {
    return { title: `${cityAdminCopy.title} | Mielenosoitukset.fi`, description: cityAdminCopy.description,
      robots: 'index, follow', canonical: config.origin + cityAdminPath };
  }
  if (route.name === 'contact') {
    return { title: `${contactCopy.title} | Mielenosoitukset.fi`, description: contactCopy.description,
      robots: 'index, follow', canonical: config.origin + contactPath };
  }
  if (route.name === 'information') {
    const { path, title, description } = informationPages[route.page];
    return { title: `${title} | Mielenosoitukset.fi`, description,
      robots: 'index, follow', canonical: config.origin + path };
  }
  if (route.name === 'fundraising') {
    return { title: 'Pienkeräys – Mielenosoitukset.fi',
      description: 'Mielenosoitukset.fi:n pienkeräys: keräyksen tiedot, keräysaika ja tilanne.',
      robots: 'index, follow', canonical: `${config.origin}/ker%C3%A4ys` };
  }
  if (route.name === 'campaign') {
    return { title: campaignCopy.title, description: campaignCopy.description,
      robots: 'index, follow', canonical: config.origin + campaignPath, image: config.origin + campaignCopy.image };
  }
  return {
    title: text.meta.title,
    description: text.meta.description,
    robots: 'index, follow',
    canonical: route.name === 'home' ? `${config.origin}/` : null,
    image: null,
  };
}
