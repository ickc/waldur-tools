/**
 * Reading the Waldur API from the browser, with the guards from `client.py`.
 *
 * This runs on an extension page, which is the only reason it can run at all:
 * the deployment's CORS allowlist holds exactly one origin, the portal's own
 * front end, so a page served from anywhere else never sees the response. An
 * extension with `host_permissions` for the API is not subject to that. See
 * web/README.md for the measurement and for the route back to a plain web page.
 *
 * **The token goes to exactly one origin.** It is a bearer credential for one
 * deployment, and two things could send it to another: a `Link: rel=next`
 * naming a different host, which this walk follows as an absolute URL, and a
 * remembered token being paired with the wrong API URL. Both are guarded --
 * here by `sameOrigin`, and in `report.js` by remembering the origin alongside
 * the token and refusing to reuse one against a different deployment.
 *
 * **The paging guards are not optional.** `page`/`page_size` is `LIMIT`/`OFFSET`
 * underneath, and that only enumerates a queryset once if the ordering is
 * total. `openportal-allocation-user-usage` is ordered by `(year, month)` and
 * nothing else, so paging it end to end hands back some rows two or three times
 * and never returns others -- which once inflated a month's node hours well past
 * the true figure. Every guard `client.iter_list_by_month` grew in response is
 * reproduced here, and they work in the browser only because the deployment
 * lists `x-result-count` and `Link` in `access-control-expose-headers`.
 */

/** Rows per page. The Python client's default, and the portal's practical cap. */
export const DEFAULT_PAGE_SIZE = 200;

/**
 * The first month `pullByMonth` looks in. Isambard 3 has no usage rows before
 * 2025 and the loop has to start somewhere; a year of slack costs twelve cheap
 * count requests and covers a backfill.
 */
export const EARLIEST_MONTH = [2024, 1];

/** How many months are fetched at once. */
export const CONCURRENCY = 6;

export class WaldurError extends Error {
  /**
   * `transient` marks the failures whose only cure is to run again: the portal
   * being written to while it is read. Everything the paging guards catch has
   * already been re-pulled `MONTH_ATTEMPTS` times by the time it is thrown, so
   * the flag means "and it still did not settle" rather than "this is
   * untried". The report offers the button either way -- there is nothing else
   * on that page to press -- but only says it is worth pressing for these.
   */
  constructor(message, { transient = false } = {}) {
    super(message);
    this.transient = transient;
  }
}

/** Every `[year, month]` from `start` up to the month containing `today`. */
export function monthsUntil(today, start = EARLIEST_MONTH) {
  const out = [];
  let [year, month] = start;
  const lastYear = today.getFullYear();
  const lastMonth = today.getMonth() + 1;
  while (year < lastYear || (year === lastYear && month <= lastMonth)) {
    out.push([year, month]);
    if (month === 12) {
      year += 1;
      month = 1;
    } else {
      month += 1;
    }
  }
  return out;
}

/** The `next` URL out of a `Link` header, if there is one. */
export function parseLinkHeader(header) {
  const links = {};
  for (const part of (header ?? '').split(',')) {
    const match = part.match(/<([^>]+)>\s*;\s*rel\s*=\s*"?([^";]+)"?/);
    if (match) links[match[2].trim()] = match[1].trim();
  }
  return links;
}

/**
 * `scheme://host[:port]`, or null when the string is not a URL at all.
 *
 * What decides whether the `Authorization` header may be attached is who is
 * going to receive it, which is the scheme, the host and the port -- never the
 * path or the query. `URL.origin` is exactly that, and this only exists to turn
 * a parse failure into a null instead of a throw.
 */
export function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

export class WaldurClient {
  /**
   * @param {object} options
   * @param {string} options.apiUrl  Base URL, no trailing slash.
   * @param {string} options.token   The portal token. Held in memory only.
   * @param {?function(): Promise<?string>} options.renew  Asked for a fresh
   *   token when the portal rejects this one, once per request. Portal tokens
   *   live an hour, so a report left open over lunch *will* meet a 401 -- and
   *   when the token was read out of a portal tab rather than pasted, the front
   *   end in that tab has been quietly refreshing it the whole time. Recovering
   *   from that without involving the reader is the difference between a report
   *   that stays open and one that has to be rebuilt. Null means there is
   *   nowhere to ask, and a 401 is final.
   */
  constructor({ apiUrl, token, renew = null }) {
    this.apiUrl = apiUrl.replace(/\/+$/, '');
    this.origin = originOf(this.apiUrl);
    if (this.origin === null) {
      throw new WaldurError(`${apiUrl} is not a URL this can send a token to.`);
    }
    this.token = token;
    this.renew = renew;
  }

  url(endpoint) {
    if (endpoint.startsWith('http')) return endpoint;
    return `${this.apiUrl}/api/${endpoint.replace(/^\/|\/$/g, '')}/`;
  }

  /**
   * One GET, with the token, held to the origin this client was built for.
   *
   * **Every URL is checked, not only the ones `url()` built.** A paginated walk
   * follows an absolute URL out of the portal's `Link` header, and that is a URL
   * the *server* chose: nothing in the protocol stops it naming another host,
   * and `list` would attach the `Authorization` header to it. So the origin is
   * checked here, where the header is added, rather than at each caller -- one
   * place, and no way past it.
   *
   * A cross-origin `fetch` from an extension page would also need
   * `host_permissions` for that host, so most of these would fail anyway. Most
   * is not all: `optional_host_permissions` covers every https host, so that
   * the extension can be pointed at another Waldur, and a reader who has
   * granted it for their own deployment has granted it broadly. The permission
   * model is therefore not the guard; this is.
   */
  async get(url, params, { renewed = false } = {}) {
    if (originOf(url) !== this.origin) {
      throw new WaldurError(
        `Refusing to send the portal token to ${originOf(url) ?? url}: this report is ` +
          `reading ${this.origin}, and the token belongs to that deployment alone. ` +
          'The URL was named by the server, in a Link header, rather than configured here.',
      );
    }
    const target = new URL(url);
    for (const [key, value] of Object.entries(params ?? {})) {
      target.searchParams.set(key, String(value));
    }
    let response;
    try {
      response = await fetch(target, {
        headers: { Authorization: `Token ${this.token}`, Accept: 'application/json' },
        // The token is the only credential; never attach ambient cookies.
        credentials: 'omit',
      });
    } catch (error) {
      throw new WaldurError(`GET ${target.pathname} failed: ${error.message}`);
    }
    if (response.status === 401 || response.status === 403) {
      // Once, and only once: a renewal that is itself rejected means the reader
      // has been signed out of the portal, and retrying that forever would turn
      // one clear error into a loop.
      if (this.renew && !renewed) {
        const fresh = await this.renew();
        if (fresh && fresh !== this.token) {
          this.token = fresh;
          return this.get(url, params, { renewed: true });
        }
      }
      // Worth saying outright: portal tokens expire within the hour, and an
      // expired one is by far the likeliest reason to be reading this.
      throw new WaldurError(
        'The portal rejected the token (HTTP ' +
          response.status +
          '). Portal tokens expire within the hour. Open the portal in another ' +
          'tab, sign in, and press the toolbar button again — the extension ' +
          'picks up the new token on its own.',
      );
    }
    if (!response.ok) {
      const body = (await response.text()).slice(0, 300);
      throw new WaldurError(`GET ${target.pathname} returned ${response.status}: ${body}`);
    }
    return response;
  }

  /**
   * One record from a detail endpoint, such as `users/me`.
   *
   * Deliberately separate from `list`: none of the paging guards apply to a
   * single object, and `list` throwing "not a list endpoint" at it is the right
   * behaviour to keep. Nothing on the critical path depends on the answer, so a
   * failure is null rather than an exception -- `users/me/` backs one fallback
   * for one default, and losing the whole report over it would be absurd.
   */
  async record(endpoint) {
    try {
      const response = await this.get(this.url(endpoint));
      const payload = await response.json();
      return payload && !Array.isArray(payload) ? payload : null;
    } catch {
      return null;
    }
  }

  /** Rows an endpoint reports, via `X-Result-Count`. Filters are passed through. */
  async count(endpoint, filters = {}) {
    const response = await this.get(this.url(endpoint), { page_size: 1, ...filters });
    const header = response.headers.get('x-result-count');
    return header === null ? null : Number(header);
  }

  /**
   * Every record from a list endpoint, following `Link` headers.
   *
   * Safe only for endpoints with a total ordering. Anything that needs slicing
   * must go through `pullByMonth` instead.
   *
   * The pull is checked two ways, because the two catch different faults and
   * neither is redundant:
   *
   * * the rows against `X-Result-Count`, which catches a short pull;
   * * and, when `rowKeys` is given, repeats of that key -- which is the only
   *   thing that catches an *unstable* pull, because a row handed back twice
   *   and a row never handed back at all cancel out in the count. Measured
   *   against the live deployment, `openportal-associations` does exactly
   *   that: the count matches on every attempt while one or two rows come
   *   back twice and as many never arrive, and how many varies with
   *   `page_size` -- which is what makes it a paging artefact rather than
   *   duplicate data. There is no server-side fix to reach for: this
   *   deployment ignores `o=` and `ordering=` as silently as it ignores any
   *   other unrecognised parameter, so the ordering cannot be made total from
   *   here, and the endpoint has no time axis to slice on either.
   *
   * A repeat is therefore reported rather than assumed away. `onRepeats` is
   * for the caller who would rather have slightly-wrong rows than no page --
   * it is handed the counts and the rows are returned. Without it, a repeat
   * throws, which is the right default for anything feeding a headline.
   *
   * `onCount` is the same arrangement for the count check, and exists because
   * this method cannot tell a short pull from a slice that grew while it was
   * being read. It knows neither which filter it is carrying nor what the
   * count says now, and both decide whether a mismatch is damage. A caller
   * that can answer those -- `pullMonth` is the one -- takes the discrepancy
   * and rules on it. Without it, a mismatch throws, as before.
   */
  async list(endpoint, {
    pageSize = DEFAULT_PAGE_SIZE, rowKeys = null, onRepeats = null, onCount = null, ...filters
  } = {}) {
    let url = this.url(endpoint);
    let params = { page_size: pageSize, ...filters };
    const rows = [];
    const seen = new Set();
    let expected = null;

    while (url) {
      const response = await this.get(url, params);
      const payload = await response.json();
      if (!Array.isArray(payload)) {
        throw new WaldurError(`${endpoint} is not a list endpoint`);
      }
      if (expected === null) {
        const header = response.headers.get('x-result-count');
        if (header !== null) expected = Number(header);
      }
      rows.push(...payload);
      seen.add(response.url);
      // Later URLs come out of the Link header already carrying their query.
      url = parseLinkHeader(response.headers.get('Link')).next ?? null;
      params = undefined;
      if (url && seen.has(url)) {
        // A page that links to itself would otherwise spin forever.
        throw new WaldurError(`${endpoint} pagination looped back to ${url}`);
      }
    }

    if (expected !== null && rows.length !== expected) {
      const detail =
        `${endpoint}: fetched ${rows.length} rows but the server reported ${expected}. ` +
        'Pagination is unstable; retry.';
      if (!onCount) throw new WaldurError(detail);
      onCount({ endpoint, expected, fetched: rows.length, detail });
    }
    if (rowKeys) {
      const repeats = countRepeats(rows, rowKeys);
      if (repeats) {
        const detail =
          `${endpoint}: ${repeats} of ${rows.length} rows repeat a key already seen, ` +
          'so as many rows are missing. The portal paged the list inconsistently.';
        if (!onRepeats) throw new WaldurError(detail);
        onRepeats({ endpoint, expected, fetched: rows.length, repeats, detail });
      }
    }
    return rows;
  }
}

/**
 * Every guard in this file is arithmetic against `X-Result-Count`, so a pull
 * that cannot read that header has no guards at all -- and the shape of the
 * failure is the worst one available: `count()` answers null, `if (!expected)`
 * reads null as an empty month, and the report draws itself out of zero rows
 * without an error anywhere. The browser is where this can really happen. The
 * header is readable cross-origin only because the deployment names it in
 * `access-control-expose-headers`, and that is one server-side config change
 * away from going missing. So it is checked for, and said out loud.
 */
function missingCountHeader(endpoint) {
  return new WaldurError(
    `${endpoint} returned no readable X-Result-Count header, so none of the paging ` +
      'guards can run and no total built from it can be trusted. That header is ' +
      'readable from here only while the deployment lists it in ' +
      'access-control-expose-headers.',
  );
}

/**
 * Check that an endpoint honours the `year`/`month` filter at all.
 *
 * Waldur's DRF filters silently ignore query parameters they do not recognise,
 * so an unsupported filter is indistinguishable from one that matched
 * everything -- and an endpoint without `year`/`month` would be fetched once
 * per month and yield the whole table over and over. 1900 predates every Waldur
 * deployment: a non-zero answer means the filter was dropped.
 */
export async function assertMonthFilterWorks(client, endpoint) {
  const probe = await client.count(endpoint, { year: 1900, month: 1 });
  if (probe === null) throw missingCountHeader(endpoint);
  if (probe) {
    throw new WaldurError(
      `${endpoint} ignores the year/month filter, so it cannot be pulled a month ` +
        'at a time and no total built from it can be trusted.',
    );
  }
}

/**
 * Rows repeated within a pull, by the columns that identify one.
 *
 * Duplicates and omissions cancel out in a row *count*, so they slip past the
 * `X-Result-Count` check that catches everything else. A repeat means the pull
 * enumerated the table unreliably, and where a row came back twice another came
 * back not at all -- the duplicates are the symptom, the missing rows are the
 * damage. Callers raise rather than de-duplicating.
 */
export function countRepeats(rows, keys) {
  const seen = new Set();
  let repeats = 0;
  for (const row of rows) {
    const key = keys.map((field) => String(row[field])).join('\u0000');
    if (seen.has(key)) repeats += 1;
    else seen.add(key);
  }
  return repeats;
}

/** The columns that identify one usage row, from `cache.ROW_KEYS`. */
export const USAGE_ROW_KEYS = ['allocation', 'user', 'year', 'month'];

/** How many times a month is pulled before its inconsistency is reported. */
export const MONTH_ATTEMPTS = 3;

/** Waited after a failed attempt, multiplied by the attempt number. */
export const RETRY_BACKOFF_MS = 300;

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Pull one calendar month of an endpoint, verified against the server's count.
 *
 * Filtering to a month shrinks the queryset to something the server enumerates
 * consistently. The checks are what turn a bad page into an error here rather
 * than an under-reported month in a figure.
 *
 * **The month in progress is being written to while it is read**, which is a
 * different fault from unstable paging and needs a different answer. Usage rows
 * land as jobs are accounted, and `X-Result-Count` is a count taken with the
 * first page while every later page's `OFFSET` is resolved against the table as
 * it is by then. One insert mid-crawl therefore lengthens the tail, and the
 * pull ends holding more rows than the count promised. That is not damage, and
 * failing the whole report over it -- which is what this used to do -- costs a
 * reader their page for the portal doing its job.
 *
 * So the two checks are ranked rather than run in sequence:
 *
 * * **A repeated key fails, always.** It is the fault that costs rows: where
 *   one came back twice another never came at all, and the two cancel in any
 *   count. Nothing about a live month excuses it.
 * * **A short pull fails.** Fewer rows than a count taken *before* the read
 *   cannot be explained by rows arriving during it.
 * * **An over-long pull is confirmed, not assumed.** With no repeated key,
 *   every row in hand is a distinct real row, so the pull holds at least what
 *   the opening count described. Re-reading the count settles the rest: if it
 *   now equals the rows in hand, the month is exactly this many rows and
 *   exactly these are held. Anything else is unresolved and fails.
 *
 * Everything above is a race, and a race is worth losing twice before it is
 * worth reporting -- one month is a handful of requests against a cache that
 * already holds the others. `attempts` re-pulls it; `onRetry` says so. An
 * unreadable count header is not a race and throws straight out.
 */
export async function pullMonth(client, endpoint, year, month, {
  pageSize, attempts = MONTH_ATTEMPTS, backoffMs = RETRY_BACKOFF_MS, onRetry = () => {},
} = {}) {
  const slice = `${endpoint} ${year}-${String(month).padStart(2, '0')}`;
  let failure = null;
  for (let attempt = 1; attempt <= Math.max(attempts, 1); attempt += 1) {
    const outcome = await pullMonthOnce(client, endpoint, year, month, { pageSize, slice });
    if (outcome.ok) return { rows: outcome.rows, expected: outcome.expected };
    failure = outcome.error;
    if (attempt < attempts) {
      onRetry({ endpoint, year, month, attempt, of: attempts, detail: failure.message });
      await sleep(backoffMs * attempt);
    }
  }
  throw failure;
}

/** One attempt at a month: the rows, or the reason they cannot be trusted. */
async function pullMonthOnce(client, endpoint, year, month, { pageSize, slice }) {
  const expected = await client.count(endpoint, { year, month });
  // Null is not zero: an unreadable header would otherwise be indistinguishable
  // from a month with no usage, and every month would "succeed" empty.
  if (expected === null) throw missingCountHeader(endpoint);
  if (!expected) return { ok: true, rows: [], expected: 0 };

  let mismatch = null;
  const rows = await client.list(endpoint, {
    pageSize, year, month, onCount: (detail) => { mismatch = detail; },
  });

  const repeats = countRepeats(rows, USAGE_ROW_KEYS);
  if (repeats) {
    return {
      ok: false,
      error: new WaldurError(
        `${slice}: ${repeats} of ${rows.length} rows repeat a key already seen, so as many ` +
          'are missing. The portal paged the month inconsistently.',
        { transient: true },
      ),
    };
  }
  if (!mismatch) return { ok: true, rows, expected };
  if (rows.length < expected) {
    return {
      ok: false,
      error: new WaldurError(
        `${slice}: fetched ${rows.length} rows but the server reported ${expected}. ` +
          'Pagination is unstable.',
        { transient: true },
      ),
    };
  }

  const now = await client.count(endpoint, { year, month });
  if (now === null) throw missingCountHeader(endpoint);
  if (now === rows.length) return { ok: true, rows, expected: now };
  return {
    ok: false,
    error: new WaldurError(
      `${slice}: fetched ${rows.length} distinct rows against a count of ${expected} that ` +
        `has since moved to ${now}. The month is being written to faster than it reads.`,
      { transient: true },
    ),
  };
}

/**
 * Every record, a month at a time, newest first, several months in flight.
 *
 * Newest first because that is the order the report becomes useful in: the
 * headline figure wants the recent months, and the reader sees a chart within a
 * second or two rather than a spinner over the whole history. Pages *within* a
 * month stay sequential -- they are a Link-header chain -- but months are
 * independent of one another, so the round trips overlap.
 *
 * `onMonth` is called with each month's rows as they land, which is what drives
 * the progressive render. It may be called twice for the same month (see the
 * cache note below), so a caller must key its accumulator by month rather than
 * appending.
 *
 * `cache` is consulted for complete months and written back for them; the month
 * in progress is always fetched, because it is still being written to. A stale
 * cached month is caught by the whole-table check at the end -- the portal does
 * backfill occasionally -- and the answer to that is to drop the cache and pull
 * again, not to fail in front of the reader.
 *
 * `onRetry` is passed through to `pullMonth` and fires when a month is pulled
 * again after coming back inconsistent. Worth surfacing: it is the one thing
 * that explains a pull taking noticeably longer than the last one.
 */
export async function pullByMonth(client, endpoint, options = {}) {
  const { cache = null } = options;
  const attempt = await pullMonths(client, endpoint, options);
  if (attempt.ok) return attempt.rows;

  if (cache && attempt.cached) {
    await cache.clear(endpoint);
    const fresh = await pullMonths(client, endpoint, { ...options, cache: null });
    if (fresh.ok) return fresh.rows;
    throw fresh.error;
  }
  throw attempt.error;
}

async function pullMonths(client, endpoint, {
  today = new Date(),
  pageSize = DEFAULT_PAGE_SIZE,
  concurrency = CONCURRENCY,
  cache = null,
  onMonth = () => {},
  onProgress = () => {},
  onRetry = () => {},
  attempts = MONTH_ATTEMPTS,
} = {}) {
  const total = await client.count(endpoint);
  if (total === null) throw missingCountHeader(endpoint);
  await assertMonthFilterWorks(client, endpoint);

  const current = [today.getFullYear(), today.getMonth() + 1];
  const months = monthsUntil(today).reverse();
  const results = new Map();
  let done = 0;
  let seen = 0;
  let cached = 0;

  const queue = [...months];
  const worker = async () => {
    for (;;) {
      const next = queue.shift();
      if (next === undefined) return;
      const [year, month] = next;
      const complete = !(year === current[0] && month === current[1]);

      let rows = null;
      if (complete && cache) rows = await cache.get(endpoint, year, month);
      if (rows === null) {
        const pulled = await pullMonth(client, endpoint, year, month, {
          pageSize, attempts, onRetry,
        });
        rows = pulled.rows;
        // Only complete months are stored. The month in progress is still
        // being written to, and a cached copy of it would go stale in minutes.
        if (complete && cache && rows.length) await cache.put(endpoint, year, month, rows);
      } else {
        cached += 1;
      }

      results.set(`${year}-${month}`, rows);
      seen += rows.length;
      done += 1;
      onMonth(rows, { year, month, done, of: months.length });
      onProgress({ done, of: months.length, rows: seen });
    }
  };

  await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, worker));

  const rows = months.flatMap(([year, month]) => results.get(`${year}-${month}`) ?? []);

  // The months must add up to the table as a whole, or the window above is too
  // narrow and a year of usage is missing from every figure without saying so.
  // Ruled on the way one month is: fewer rows than the table claimed is damage,
  // while more is the table having grown between this count and the last page
  // -- and every month here has already been verified row by row, so a fresh
  // count that agrees is the whole of what is left to check.
  if (seen < total) {
    // Not a race, and not marked as one. Every month here was verified row by
    // row against its own count, so the rows are not missing from the months --
    // there are months missing from the walk, and a second attempt covers the
    // same window and fails the same way. Offering the button is not the same
    // as saying it is worth pressing.
    return {
      ok: false,
      cached,
      rows,
      error: new WaldurError(
        `${endpoint}: ${seen} rows across months but ${total} in the table as a whole. ` +
          'Either the window in monthsUntil is too narrow, or rows were deleted while ' +
          'it ran.',
      ),
    };
  }
  if (seen > total) {
    const now = await client.count(endpoint);
    if (now === null) throw missingCountHeader(endpoint);
    if (now !== seen) {
      return {
        ok: false,
        cached,
        rows,
        error: new WaldurError(
          `${endpoint}: ${seen} rows across months but ${total} in the table as a whole, ` +
            `and ${now} in it now. Rows changed under the pull.`,
          { transient: true },
        ),
      };
    }
  }
  return { ok: true, cached, rows };
}
