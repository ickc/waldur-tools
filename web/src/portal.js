/**
 * Reading the token, the API URL and the organisation off the portal itself.
 *
 * The report used to open on a form: paste a token, confirm an API URL, and
 * accept a default organisation that was one institution's name written into
 * the source. All three are already sitting in the tab the reader came from,
 * and none of them is a decision they should be asked to make.
 *
 * * **The token** is in the front end's own `localStorage`, under
 *   `waldur/auth/token`. It is the same string the account menu's *Copy* offers
 *   -- `users/me/` returns it as `token` -- so reading it is the same act, minus
 *   two clicks and a paste.
 * * **The API URL** is derivable from the page several ways over, and this file
 *   tries them in order of how directly the page states it.
 * * **The organisation** is in the URL of an organisation page, as a UUID.
 *
 * Every function here is pure: given the readings, work out the answer. The one
 * that is not -- `readPortalContext` -- is serialised into the portal tab by
 * `background.js` and so may reference nothing outside itself. Both properties
 * are what let `tests/portal.test.mjs` cover this without a browser.
 *
 * **None of it is an API contract.** `waldur/auth/token` is a HomePort internal
 * and a Waldur upgrade may rename it. So a reading is never trusted on its own:
 * `report.js` puts it to `users/me/` before building anything, and a token that
 * does not answer falls back to the paste form rather than failing obscurely.
 */

/** Where Waldur's front end keeps the session token. Not a documented key. */
export const TOKEN_KEY = 'waldur/auth/token';

/** Where it keeps the organisation and project the reader last filtered on. */
export const FILTER_KEY = 'waldur/filter/resources';

/** Waldur UUIDs are 32 hex characters, unhyphenated, in the path. */
const UUID = '[0-9a-f]{32}';

/**
 * Everything worth knowing, read inside the portal tab.
 *
 * **This function is stringified and injected**, so it must be self-contained:
 * no imports, no closure over anything in this module, and nothing that a
 * structured clone cannot carry back. Keep it dull.
 *
 * It reads and returns; it decides nothing. The judgement about which of the
 * API-URL signals to believe belongs in `apiUrl` below, where it can be tested.
 */
export function readPortalContext() {
  const readings = {
    href: location.href,
    origin: location.origin,
    token: null,
    filter: null,
    iconOrigin: null,
    requestOrigins: [],
  };

  try {
    readings.token = localStorage.getItem('waldur/auth/token');
    readings.filter = localStorage.getItem('waldur/filter/resources');
  } catch {
    // Storage can be denied outright. A missing token is handled everywhere
    // downstream; a thrown one would take the whole handover with it.
  }

  try {
    const icon = document.querySelector('link[rel*="icon"]');
    if (icon && icon.href) readings.iconOrigin = new URL(icon.href).origin;
  } catch {
    // As above: a signal that cannot be read is simply not offered.
  }

  try {
    // The single most direct evidence available: the origin the single-page app
    // has actually been talking to. It is a ring buffer and can be full, which
    // is why it is one signal among several rather than the only one.
    const origins = new Set();
    for (const entry of performance.getEntriesByType('resource')) {
      if (entry.name.indexOf('/api/') === -1) continue;
      origins.add(new URL(entry.name).origin);
    }
    readings.requestOrigins = [...origins];
  } catch {
    // Ditto.
  }

  return readings;
}

/** Whether a URL looks like a Waldur front end we can read a token out of. */
export function isPortalUrl(url) {
  try {
    const { protocol, hostname } = new URL(url);
    if (protocol !== 'https:' && protocol !== 'http:') return false;
    return hostname.startsWith('portal.') || hostname.startsWith('portal-');
  } catch {
    return false;
  }
}

/**
 * The API base the front end talks to, from the strongest signal available.
 *
 * Four routes to the same answer, tried in the order of how directly the page
 * states it. They are not redundant: each fails in a case the next survives.
 *
 * 1. **An origin the app has actually fetched `/api/` from.** Observed
 *    behaviour, not inference -- believe it above anything else. Empty on a
 *    page that has made no API call yet, and on a long-lived tab whose resource
 *    buffer has filled and dropped the early entries.
 * 2. **The favicon's origin.** Waldur's `index.html` points its icon at the
 *    back end, so this is the deployment's own statement of where the API is,
 *    and it survives an empty resource buffer.
 * 3. **The remembered resource filter**, which holds an absolute
 *    `.../api/customers/<uuid>/` URL. Only present once the reader has filtered
 *    something, but it outlives a reload, which the other two do not.
 * 4. **`portal.` rewritten to `portal-api.`** -- the convention this deployment
 *    follows, and a guess. It is last because it is the only one that is not
 *    evidence.
 *
 * Null when nothing offers an answer, which the caller turns into a form field
 * rather than a wrong host.
 */
export function apiUrl(readings) {
  const { requestOrigins = [], iconOrigin = null, filter = null, origin = null } = readings ?? {};

  if (requestOrigins.length === 1) return requestOrigins[0];
  if (requestOrigins.length > 1) {
    // More than one origin serving `/api/` is unusual but not ambiguous: the
    // one that is not the front end is the back end.
    const other = requestOrigins.find((candidate) => candidate !== origin);
    if (other) return other;
    return requestOrigins[0];
  }

  if (iconOrigin && iconOrigin !== origin) return iconOrigin;

  const url = filterApiUrl(filter);
  if (url) return url;

  if (origin) {
    try {
      const target = new URL(origin);
      if (target.hostname.startsWith('portal.')) {
        target.hostname = `portal-api.${target.hostname.slice('portal.'.length)}`;
        return target.origin;
      }
    } catch {
      // Fall through to null.
    }
  }
  return null;
}

/** The API origin inside the remembered resource filter, if it holds one. */
function filterApiUrl(filter) {
  const parsed = parseFilter(filter);
  const href = parsed?.organization?.url;
  if (typeof href !== 'string') return null;
  const cut = href.indexOf('/api/');
  if (cut === -1) return null;
  try {
    return new URL(href.slice(0, cut)).origin;
  } catch {
    return null;
  }
}

function parseFilter(filter) {
  if (!filter) return null;
  try {
    return JSON.parse(filter);
  } catch {
    return null;
  }
}

/**
 * The organisation the reader is looking at, as a UUID.
 *
 * The dashboard URL carries it -- `/organizations/<uuid>/dashboard/` -- and
 * that is the whole point of this: an RSE at any institution opens their own
 * organisation's page, presses the button, and gets their own report. Nothing
 * about which institution that is need be known here or written down anywhere.
 *
 * Project pages carry a *project* UUID, which is a different thing and is
 * returned separately: `report.js` can resolve it through `projects`, which it
 * already fetches, without confusing the two.
 */
export function scopeFromUrl(href) {
  let path;
  try {
    path = new URL(href).pathname;
  } catch {
    return {};
  }
  const organisation = path.match(new RegExp(`/organizations?/(${UUID})(?:/|$)`));
  if (organisation) return { customerUuid: organisation[1] };
  const project = path.match(new RegExp(`/projects?/(${UUID})(?:/|$)`));
  if (project) return { projectUuid: project[1] };
  return {};
}

/** The organisation name the reader last filtered on, if the page remembers one. */
export function scopeFromFilter(filter) {
  const parsed = parseFilter(filter);
  const name = parsed?.organization?.name;
  const uuid = parsed?.organization?.uuid;
  return {
    customerName: typeof name === 'string' && name ? name : null,
    customerUuid: typeof uuid === 'string' && uuid ? uuid : null,
  };
}

/**
 * The organisation this token belongs to, from `users/me/`.
 *
 * The last resort, and the only one that works from any page at all: a Waldur
 * user carries a permission whose `scope_type` is `customer` on the
 * organisation they belong to. Someone holding several gets the first, which
 * the picker on the report then lets them change.
 */
export function customerFromPermissions(me) {
  for (const permission of me?.permissions ?? []) {
    if (permission?.scope_type === 'customer' && permission?.scope_name) {
      return permission.scope_name;
    }
  }
  return null;
}

/**
 * Turn the raw readings into what the report needs, without judging the token.
 *
 * Whether the token *works* is not answerable here -- it takes a request, and
 * `report.js` makes it. This only says what was found.
 */
export function portalContext(readings) {
  if (!readings) return { token: null, apiUrl: null, customerUuid: null, customerName: null };
  const fromUrl = scopeFromUrl(readings.href ?? '');
  const fromFilter = scopeFromFilter(readings.filter);
  return {
    token: readings.token || null,
    apiUrl: apiUrl(readings),
    // The URL wins: it is where the reader is looking now, where the remembered
    // filter is where they looked once.
    customerUuid: fromUrl.customerUuid ?? fromFilter.customerUuid ?? null,
    projectUuid: fromUrl.projectUuid ?? null,
    customerName: fromUrl.customerUuid ? null : fromFilter.customerName,
  };
}
