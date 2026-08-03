/**
 * The paging guards, which are the reason any total on the page can be trusted.
 *
 * `openportal-allocation-user-usage` cannot be paged end to end: it is ordered
 * by `(year, month)` and nothing else, so `LIMIT`/`OFFSET` hands back some rows
 * two and three times and never returns others. That once put a month's node
 * hours well past the true figure, and it read as a finding until someone
 * checked it against the invoices.
 *
 * Every guard `waldur_tools.client` grew in response is reproduced in `api.js`,
 * and every one of them fails silently if it is wrong — a dropped check looks
 * exactly like a clean pull. So each is tested here against a stub portal that
 * misbehaves in the specific way the real one did.
 *
 * Run with `node web/tests/api.test.mjs`.
 */

import assert from 'node:assert/strict';
import { after, describe, it } from 'node:test';

import {
  WaldurClient, WaldurError, assertMonthFilterWorks, countRepeats, monthsUntil,
  parseLinkHeader, pullByMonth, pullMonth,
} from '../src/api.js';

const API = 'https://portal.example.test';
const ENDPOINT = 'openportal-allocation-user-usage';

const realFetch = globalThis.fetch;
after(() => {
  globalThis.fetch = realFetch;
});

/** A response object shaped like the parts of `Response` that `api.js` reads. */
function reply(body, { headers = {}, url = '', status = 200 } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    url,
    headers: new Headers(headers),
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

/**
 * A stub portal holding `rows`, paging them the way Waldur does.
 *
 * `corrupt` is how a misbehaving deployment is simulated: it gets the page the
 * server was about to return and may drop, duplicate or reorder it.
 */
function portal(rows, { corrupt = (page) => page, countOverride = null } = {}) {
  const calls = [];
  globalThis.fetch = async (target) => {
    const url = new URL(target);
    const params = url.searchParams;
    calls.push({ path: url.pathname, params: Object.fromEntries(params) });

    let matching = rows;
    if (params.has('year')) {
      matching = matching.filter(
        (row) => String(row.year) === params.get('year')
          && String(row.month) === params.get('month'),
      );
    }
    const total = countOverride ?? matching.length;
    const size = Number(params.get('page_size') ?? 200);
    const page = Number(params.get('page') ?? 1);
    const slice = corrupt(matching.slice((page - 1) * size, page * size), page);

    const headers = { 'x-result-count': String(total) };
    if (page * size < matching.length) {
      const next = new URL(url);
      next.searchParams.set('page', String(page + 1));
      headers.Link = `<${next}>; rel="next"`;
    }
    return reply(slice, { headers, url: String(url) });
  };
  return calls;
}

function usageRows(count, { year = 2026, month = 2 } = {}) {
  return Array.from({ length: count }, (_, index) => ({
    allocation: `${API}/api/openportal-allocations/a${index}/`,
    user: `${API}/api/users/u${index}/`,
    username: `user${index}.abc1.brics`,
    node_usage: '1.0',
    year,
    month,
  }));
}

const client = () => new WaldurClient({ apiUrl: API, token: 'secret' });

// --------------------------------------------------------------------------

describe('helpers', () => {
  it('enumerates months up to and including the current one', () => {
    const months = monthsUntil(new Date(2025, 2, 15), [2024, 11]);
    assert.deepEqual(months, [[2024, 11], [2024, 12], [2025, 1], [2025, 2], [2025, 3]]);
  });

  it('reads the next link out of a Link header', () => {
    const header = '<https://x/?page=2>; rel="next", <https://x/?page=1>; rel="prev"';
    assert.equal(parseLinkHeader(header).next, 'https://x/?page=2');
    assert.equal(parseLinkHeader('').next, undefined);
  });

  it('counts repeats on the compound key, not on whole rows', () => {
    // Two rows differing only in a field outside the key still repeat it: the
    // key is what the endpoint promises is unique, so that is what is checked.
    const rows = [
      { allocation: 'a', user: 'u', year: 2026, month: 2, node_usage: '1' },
      { allocation: 'a', user: 'u', year: 2026, month: 2, node_usage: '2' },
      { allocation: 'b', user: 'u', year: 2026, month: 2, node_usage: '1' },
    ];
    assert.equal(countRepeats(rows, ['allocation', 'user', 'year', 'month']), 1);
  });
});

describe('reading', () => {
  it('follows Link headers to the end', async () => {
    const calls = portal(usageRows(450));
    const rows = await client().list(ENDPOINT, { pageSize: 200 });
    assert.equal(rows.length, 450);
    assert.equal(calls.length, 3);
    // The page size travels on the first request; the rest of the chain comes
    // back from the server with its query already attached.
    assert.equal(calls[0].params.page_size, '200');
    assert.equal(calls[2].params.page, '3');
  });

  it('refuses a Link header that points back at a page already read', async () => {
    globalThis.fetch = async (target) =>
      reply([{ id: 1 }], {
        url: String(target),
        headers: { 'Link': `<${API}/api/loop/>; rel="next"` },
      });
    await assert.rejects(
      () => client().list('loop'),
      (error) => error instanceof WaldurError && /looped back/.test(error.message),
    );
  });

  it('checks a plain list against the server’s own count too', async () => {
    portal(usageRows(450), { corrupt: (page) => page.slice(0, -3) });
    await assert.rejects(
      () => client().list(ENDPOINT, { pageSize: 200 }),
      (error) => error instanceof WaldurError && /Pagination is unstable/.test(error.message),
    );
  });

  it('catches a repeated row in a plain list, which the count cannot', async () => {
    // Measured on the live deployment, `openportal-associations` does exactly
    // this: the count matches every time while a row comes back twice and
    // another never arrives, and how many varies with `page_size`. Nothing
    // server-side fixes it -- `o=` and `ordering=` are ignored as silently as
    // any other unrecognised parameter -- so it has to be caught here.
    portal(usageRows(10), { corrupt: (page) => [...page.slice(0, -1), page[0]] });
    await assert.rejects(
      () => client().list(ENDPOINT, { rowKeys: ['username'] }),
      (error) => /rows repeat a key already seen/.test(error.message),
    );
  });

  it('reports the repeat instead of raising when the caller asks it to', async () => {
    // For the one denominator on one tile that is better slightly short than
    // absent. The rows still come back; the caller is told how many are wrong.
    portal(usageRows(10), { corrupt: (page) => [...page.slice(0, -1), page[0]] });
    const seen = [];
    const rows = await client().list(ENDPOINT, {
      rowKeys: ['username'],
      onRepeats: (report) => seen.push(report),
    });
    assert.equal(rows.length, 10);
    assert.equal(seen.length, 1);
    assert.equal(seen[0].repeats, 1);
  });

  it('says what a rejected token means, since that is nearly always why', async () => {
    globalThis.fetch = async () => reply({ detail: 'nope' }, { status: 401 });
    await assert.rejects(
      () => client().count(ENDPOINT),
      (error) => /expire within hours/.test(error.message),
    );
  });
});

describe('the month filter', () => {
  it('passes when the endpoint really filters', async () => {
    portal(usageRows(10));
    await assertMonthFilterWorks(client(), ENDPOINT);
  });

  it('fails when the filter is silently ignored', async () => {
    // Waldur's DRF filters drop parameters they do not recognise, so an
    // endpoint without year/month answers every month with the whole table --
    // and would be pulled once per month and summed nineteen times over.
    globalThis.fetch = async () =>
      reply([], { headers: { 'x-result-count': '28830' } });
    await assert.rejects(
      () => assertMonthFilterWorks(client(), ENDPOINT),
      (error) => /ignores the year\/month filter/.test(error.message),
    );
  });
});

describe('pulling one month', () => {
  it('checks the rows fetched against the server’s own count', async () => {
    // A page that comes back short is the signature of unstable paging.
    portal(usageRows(300), { corrupt: (page) => page.slice(0, -5) });
    await assert.rejects(
      () => pullMonth(client(), ENDPOINT, 2026, 2, { pageSize: 200 }),
      (error) => /Pagination is unstable/.test(error.message),
    );
  });

  it('rejects a month that repeats a row, which a count cannot catch', async () => {
    // The damaging case: a duplicate and an omission cancel out in the count,
    // so this is the only guard that sees it.
    portal(usageRows(10), {
      corrupt: (page) => [...page.slice(0, -1), page[0]],
    });
    await assert.rejects(
      () => pullMonth(client(), ENDPOINT, 2026, 2, { pageSize: 200 }),
      (error) => /rows repeat a key already seen/.test(error.message),
    );
  });

  it('refuses to read a missing X-Result-Count as an empty month', async () => {
    // The header is readable from a browser only while the deployment lists it
    // in `access-control-expose-headers`. Without this check `count()` answers
    // null, `if (!expected)` reads null as zero, and every month "succeeds"
    // empty -- a whole report drawn from no rows and no error anywhere.
    globalThis.fetch = async (target) => reply([], { url: String(target) });
    await assert.rejects(
      () => pullMonth(client(), ENDPOINT, 2026, 2),
      (error) => error instanceof WaldurError && /X-Result-Count/.test(error.message),
    );
    await assert.rejects(
      () => pullByMonth(client(), ENDPOINT, { today: new Date(2026, 1, 15) }),
      (error) => error instanceof WaldurError && /X-Result-Count/.test(error.message),
    );
  });

  it('skips a month the server reports as empty without fetching it', async () => {
    const calls = portal([]);
    const { rows } = await pullMonth(client(), ENDPOINT, 2026, 2);
    assert.deepEqual(rows, []);
    // One count request, and no page request behind it.
    assert.equal(calls.length, 1);
  });
});

describe('pulling every month', () => {
  const today = new Date(2026, 1, 15); // February 2026

  it('returns every row, newest month first, and reports progress', async () => {
    const rows = [
      ...usageRows(3, { year: 2026, month: 1 }),
      ...usageRows(2, { year: 2026, month: 2 }),
    ];
    portal(rows);
    const seen = [];
    const pulled = await pullByMonth(client(), ENDPOINT, {
      today,
      concurrency: 2,
      onMonth: (monthRows, { year, month }) => seen.push([year, month, monthRows.length]),
    });
    assert.equal(pulled.length, 5);
    assert.deepEqual(
      seen.filter(([, , count]) => count > 0).sort(),
      [[2026, 1, 3], [2026, 2, 2]],
    );
  });

  it('fails when the months do not add up to the table as a whole', async () => {
    // The window in `monthsUntil` being too narrow looks exactly like this, and
    // silently drops a year of usage out of every figure.
    portal(usageRows(4, { year: 2023, month: 5 }), { countOverride: null });
    await assert.rejects(
      () => pullByMonth(client(), ENDPOINT, { today }),
      (error) => /rows across months but/.test(error.message),
    );
  });

  it('drops a stale cache and pulls again rather than failing at the reader', async () => {
    // The portal does backfill an old month occasionally. The whole-table check
    // notices the totals stopped agreeing; the answer is to refetch, not to put
    // an error where the chart should be.
    const rows = usageRows(4, { year: 2026, month: 1 });
    portal(rows);

    let cleared = 0;
    const cache = {
      // Two rows short: what a month cached before a backfill looks like.
      get: async (endpoint, year, month) =>
        (year === 2026 && month === 1 ? rows.slice(0, 2) : null),
      put: async () => {},
      clear: async () => {
        cleared += 1;
        cache.get = async () => null;
      },
    };

    const pulled = await pullByMonth(client(), ENDPOINT, { today, cache });
    assert.equal(cleared, 1);
    assert.equal(pulled.length, 4);
  });

  it('never caches the month in progress', async () => {
    portal(usageRows(2, { year: 2026, month: 2 }));
    const written = [];
    const cache = {
      get: async () => null,
      put: async (endpoint, year, month) => written.push([year, month]),
      clear: async () => {},
    };
    await pullByMonth(client(), ENDPOINT, { today, cache });
    // February 2026 is the month `today` falls in: still being written to, so
    // a cached copy of it would go stale within the hour.
    assert.deepEqual(written, []);
  });
});
