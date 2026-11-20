// Public profile page, GET /users/profile/<username>. The data comes from the Go
// read route (go-api/profile.go); the copy is the Flask template's Finnish msgids.
// The server renders the public part without a cookie. After hydration the browser
// reads the route again with its own cookie to learn whether this is the owner or
// another signed-in visitor. Follow and friend actions keep posting to the
// unchanged Flask JSON endpoints.
export const profileApi = (username: string) => `/api/profile/${encodeURIComponent(username)}`;
export const profilePath = (username: string) => `/users/profile/${encodeURIComponent(username)}`;
export const profileActionApi = {
  follow: '/users/profile/api/follow/',
  unfollow: '/users/profile/api/unfollow/',
  sendRequest: '/users/profile/api/send_friend_request/',
  accept: '/users/profile/api/accept_friend_request/',
  reject: '/users/profile/api/reject_friend_request/',
} as const;
export type ProfileAction = keyof typeof profileActionApi;
export const defaultProfilePicture = 'https://cdn2.mielenosoitukset.fi/default-pfp.jpg';

export const profileCopy = {
  notFound: 'Käyttäjäprofiilia ei löytynyt.',
  home: 'Takaisin etusivulle',
  unavailable: 'Profiilia ei saatu ladattua. Yritä hetken kuluttua uudelleen.',
  retry: 'Yritä uudelleen',
  actionFailed: 'Toiminto epäonnistui. Yritä uudelleen.',
  adminBadge: 'Järjestelmävalvoja',
  posts: 'Postaukset',
  followers: 'Seuraajat',
  following: 'Seuratut',
  noPosts: 'Tällä käyttäjällä ei ole vielä postauksia.',
  noFollowers: 'Tällä käyttäjällä ei ole vielä seuraajia.',
  noFollowing: 'Tämä käyttäjä ei seuraa vielä ketään.',
  organizations: 'Seuraamani organisaatiot',
  noOrganizations: 'Et seuraa vielä yhtään organisaatiota.',
  showOrganization: 'Näytä organisaatio',
  recurring: 'Seuraamani toistuvat mielenosoitukset',
  noRecurring: 'Et seuraa vielä yhtään toistuvaa mielenosoitusta.',
  openRecurring: 'Avaa mielenosoitus',
  edit: 'Muokkaa profiilia',
  share: 'Jaa profiilisi',
  shareTitle: 'Jaa profiilisi muille',
  shareLead: 'Haluatko, että kaverit ja seuraajat löytävät sinut helpommin? Lähetä heille linkkisi ja valmiiksi kirjoitettu viesti:',
  shareLink: 'Profiililinkki',
  shareMessage: 'Valmis viesti',
  close: 'Sulje',
  copy: 'Kopioi linkki ja viesti',
  copied: 'Linkki ja viesti kopioitu leikepöydälle.',
  copyFailed: 'Kopiointi epäonnistui. Valitse teksti ja kopioi se itse.',
  follow: 'Seuraa',
  unfollow: 'Lopeta seuraaminen',
  addFriend: 'Lisää ystäväksi',
  accept: 'Hyväksy pyyntö',
  reject: 'Hylkää pyyntö',
  requestSent: 'Ystäväpyyntö lähetetty',
  friend: 'Ystävä',
  loginNeeded: 'Sinun tulee kirjautua sisään käyttääksesi tätä',
  picture: 'Profiilikuva',
} as const;

export type FriendState = 'friends' | 'incoming_request' | 'outgoing_request' | 'none';
const friendStates: readonly string[] = ['friends', 'incoming_request', 'outgoing_request', 'none'];

export type ProfilePerson = { username: string; displayname: string; picture: string | null };
export type ProfileOrganization = { id: string; name: string; logo: string | null; city: string; description: string };
export type ProfileRecurring = { id: string; title: string; date: string; city: string; address: string };
export type ProfileViewer = { following: boolean; friendState: FriendState };

export type ProfileRead = {
  username: string;
  displayname: string;
  picture: string | null;
  bio: string | null;
  adminBadge: boolean;
  postsCount: number;
  followers: ProfilePerson[];
  following: ProfilePerson[];
  followersCount: number;
  followingCount: number;
  /** The visitor is this account; the two lists below are then present. */
  self: boolean;
  organizations: ProfileOrganization[];
  recurring: ProfileRecurring[];
  /** Follow and friend state of a different signed-in visitor; `null` for anonymous visitors. */
  viewer: ProfileViewer | null;
  /** The visitor sent a cookie but Go could not read the session. Not the same as signed out. */
  viewerUnverified: boolean;
};

/** What the server hands to the page: the cookie-less public read, or a missing profile. */
export type ProfileInitial = { status: 'ready'; profile: ProfileRead } | { status: 'not-found' };
export type ProfileLoad = ProfileInitial | { status: 'unavailable' };

const text = (value: unknown, max = 500) => typeof value === 'string' ? value.slice(0, max) : '';
const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 1e9;

/** Pictures and logos come from the database; only web addresses and site paths become an image source. */
export function safeImage(value: unknown): string | null {
  return typeof value === 'string' && value.length <= 2000 && /^(https?:\/\/|\/(?!\/))/i.test(value) ? value : null;
}

function readPerson(value: unknown): ProfilePerson | null {
  if (!isObject(value) || typeof value.username !== 'string' || !value.username || value.username.length > 64) return null;
  return { username: value.username, displayname: text(value.displayname, 200) || value.username, picture: safeImage(value.profile_picture) };
}

function readOrganization(value: unknown): ProfileOrganization | null {
  if (!isObject(value) || typeof value.id !== 'string' || !/^[a-f\d]{24}$/i.test(value.id)) return null;
  return { id: value.id.toLowerCase(), name: text(value.name, 200), logo: safeImage(value.logo), city: text(value.city, 100),
    description: text(value.description, 400) };
}

function readRecurring(value: unknown): ProfileRecurring | null {
  if (!isObject(value) || typeof value.id !== 'string' || !/^[a-f\d]{24}$/i.test(value.id)) return null;
  return { id: value.id.toLowerCase(), title: text(value.title, 200), date: text(value.date, 100), city: text(value.city, 100),
    address: text(value.address, 200) };
}

function readList<T>(value: unknown, read: (item: unknown) => T | null, max: number): T[] | null {
  if (!Array.isArray(value)) return null;
  return value.slice(0, max).flatMap(item => read(item) ?? []);
}

/**
 * Turn a Go answer into page data. Anything that does not match the contract is
 * `null`, never a half-empty profile. `requested` makes a mismatched username a failure.
 */
export function readProfile(body: unknown, requested?: string): ProfileRead | null {
  if (!isObject(body) || typeof body.username !== 'string' || !body.username || body.username.length > 64) return null;
  if (requested !== undefined && body.username !== requested) return null;
  if (typeof body.displayname !== 'string' || typeof body.admin_badge !== 'boolean' || typeof body.self !== 'boolean') return null;
  if (!count(body.posts_count) || !count(body.followers_count) || !count(body.following_count)) return null;
  if (body.profile_picture !== null && typeof body.profile_picture !== 'string') return null;
  if (body.bio !== null && typeof body.bio !== 'string') return null;
  const followers = readList(body.followers, readPerson, 500);
  const following = readList(body.following, readPerson, 500);
  if (!followers || !following) return null;
  let viewer: ProfileViewer | null = null;
  if (body.viewer !== null && body.viewer !== undefined) {
    const raw = body.viewer;
    if (!isObject(raw) || typeof raw.following !== 'boolean' || typeof raw.friend_state !== 'string'
      || !friendStates.includes(raw.friend_state)) return null;
    viewer = { following: raw.following, friendState: raw.friend_state as FriendState };
  }
  let organizations: ProfileOrganization[] = [];
  let recurring: ProfileRecurring[] = [];
  if (body.self) {
    const orgs = readList(body.followed_organizations, readOrganization, 200);
    const demos = readList(body.followed_recurring, readRecurring, 200);
    if (!orgs || !demos) return null;
    organizations = orgs; recurring = demos;
  }
  return { username: body.username, displayname: body.displayname || body.username, picture: safeImage(body.profile_picture),
    bio: typeof body.bio === 'string' && body.bio ? body.bio.slice(0, 5000) : null, adminBadge: body.admin_badge,
    postsCount: body.posts_count as number, followers, following, followersCount: body.followers_count as number,
    followingCount: body.following_count as number, self: body.self, organizations, recurring, viewer,
    viewerUnverified: body.viewer_unverified === true };
}

export type ProfileResponseKind = 'ok' | 'not-found' | 'unavailable';

/**
 * A 404 means "no such profile" only when Go answered it with JSON. A 404 page from
 * Flask (the route is not proxied to Go) or any 5xx is unavailable, never a missing user.
 */
export function classifyProfileResponse(status: number, contentType: string | null): ProfileResponseKind {
  if (status === 404 && /^application\/json/i.test(contentType ?? '')) return 'not-found';
  if (status >= 200 && status < 300) return 'ok';
  return 'unavailable';
}

/** Server-side read of the public part. No cookie is sent, so the answer is the same for every visitor. */
export async function loadProfile(readBase: string, username: string, fetcher: typeof fetch = fetch): Promise<ProfileLoad> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetcher(`${readBase}/profile/${encodeURIComponent(username)}`,
      { signal: controller.signal, credentials: 'omit', headers: { Accept: 'application/json' } });
    const kind = classifyProfileResponse(response.status, response.headers.get('content-type'));
    if (kind === 'not-found') return { status: 'not-found' };
    if (kind !== 'ok') return { status: 'unavailable' };
    const profile = readProfile(await response.json(), username);
    return profile ? { status: 'ready', profile } : { status: 'unavailable' };
  } catch {
    return { status: 'unavailable' };
  } finally { clearTimeout(timer); }
}

export const profileTitle = (displayname: string) => `Profiili: ${displayname}`;

/** The share message of the Flask page, with the profile's absolute address. */
export function profileShareMessage(profileUrl: string): string {
  return `Hei! Tule seuraamaan profiiliani Mielenosoitukset.fi:ssä: ${profileUrl}\nVoimme myös lisätä toisemme ystäviksi!`;
}

export type ProfileActionMode = 'self' | 'viewer' | 'anonymous' | 'none';

/**
 * Which actions to offer. Only a real Go answer says "owner" or "other signed-in visitor". A visitor the
 * server session calls signed in, but whom Go did not identify (bridge off, bridge error, read pending),
 * gets no buttons rather than login links.
 */
export function profileActionMode(fresh: ProfileRead | null, session: { authenticated: boolean; unverified?: boolean }): ProfileActionMode {
  if (fresh?.self) return 'self';
  if (fresh?.viewer) return 'viewer';
  if (fresh?.viewerUnverified) return 'none';
  return session.authenticated || session.unverified ? 'none' : 'anonymous';
}
