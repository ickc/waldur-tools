/**
 * Working out the token, the API URL and the organisation from the portal tab.
 *
 * These functions replaced three things the reader used to be asked for, and
 * one of them — the organisation — used to be an institution's name written
 * into the source. That makes them worth testing for a reason the paging guards
 * do not share: **the interesting cases cannot be reached from any one
 * account.** An RSE at another institution opening their own dashboard is
 * exactly the path nobody developing this can exercise, so the only place it is
 * checked is here, against readings written out by hand.
 *
 * Every identifier below is fabricated. `portal.example.test` is not a Waldur
 * deployment and the UUIDs are keyboard noise of the right shape.
 *
 * Run with `node web/tests/portal.test.mjs`.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  apiUrl, customerFromPermissions, isPortalUrl, portalContext, scopeFromFilter, scopeFromUrl,
} from '../src/portal.js';

const FRONT = 'https://portal.example.test';
const BACK = 'https://portal-api.example.test';

/** Two fabricated organisation UUIDs, in Waldur's unhyphenated 32-hex shape. */
const ORG_A = '0123456789abcdef0123456789abcdef';
const ORG_B = 'fedcba9876543210fedcba9876543210';
const PROJECT = 'aaaa1111bbbb2222cccc3333dddd4444';

/** What `readPortalContext` hands back, with the fields a test does not use elided. */
function readings(overrides = {}) {
  return {
    href: `${FRONT}/organizations/${ORG_A}/dashboard/`,
    origin: FRONT,
    token: 'not-a-real-token',
    filter: null,
    iconOrigin: null,
    requestOrigins: [],
    ...overrides,
  };
}

// --------------------------------------------------------------------------

describe('recognising a portal tab', () => {
  it('accepts a Waldur front end and rejects everything else', () => {
    assert.equal(isPortalUrl(`${FRONT}/organizations/${ORG_A}/dashboard/`), true);
    assert.equal(isPortalUrl('https://portal-api.example.test/api/'), true);
    assert.equal(isPortalUrl('https://example.test/'), false);
    // A chrome:// page cannot be scripted, and asking is the failure this
    // avoids rather than the one it reports.
    assert.equal(isPortalUrl('chrome://extensions/'), false);
    assert.equal(isPortalUrl(undefined), false);
  });
});

describe('the API URL', () => {
  it('believes an origin the page has actually fetched /api/ from', () => {
    // The strongest signal available: observed, not inferred. It wins even when
    // the favicon and the hostname convention would say something else.
    assert.equal(
      apiUrl(readings({
        requestOrigins: ['https://api.elsewhere.test'],
        iconOrigin: BACK,
      })),
      'https://api.elsewhere.test',
    );
  });

  it('picks the back end when the front end also serves /api/', () => {
    assert.equal(apiUrl(readings({ requestOrigins: [FRONT, BACK] })), BACK);
  });

  it('falls back to the favicon, which Waldur points at the back end', () => {
    assert.equal(apiUrl(readings({ iconOrigin: BACK })), BACK);
  });

  it('ignores a favicon served by the front end itself', () => {
    // Self-hosted icon: no information, so fall through rather than return the
    // front end as an API base and fail every request against it.
    assert.equal(
      apiUrl(readings({ iconOrigin: FRONT })),
      BACK, // via the portal. -> portal-api. convention
    );
  });

  it('reads the remembered resource filter when nothing else offers one', () => {
    const filter = JSON.stringify({
      organization: { url: `${BACK}/api/customers/${ORG_A}/`, uuid: ORG_A, name: 'Fictional U' },
    });
    assert.equal(apiUrl(readings({ origin: 'https://hpc.example.test', filter })), BACK);
  });

  it('answers null rather than guessing at an unconventional hostname', () => {
    // `hpc.` is not `portal.`, so there is no convention to apply and no
    // evidence to fall back on. The caller shows a form; a wrong host would
    // instead produce a stream of failed requests against somebody else's site.
    assert.equal(apiUrl(readings({ origin: 'https://hpc.example.test', href: '' })), null);
  });

  it('survives a filter that is not JSON', () => {
    assert.equal(apiUrl(readings({ filter: 'not json at all' })), BACK);
  });
});

describe('the organisation', () => {
  it('takes the UUID out of an organisation URL', () => {
    assert.deepEqual(
      scopeFromUrl(`${FRONT}/organizations/${ORG_A}/dashboard/`),
      { customerUuid: ORG_A },
    );
  });

  it('does not mistake a project UUID for an organisation one', () => {
    // Both are 32 hex characters in the same position; only the segment before
    // them says which. Conflating the two would scope the report to an
    // organisation that does not exist and draw nothing.
    assert.deepEqual(scopeFromUrl(`${FRONT}/projects/${PROJECT}/`), { projectUuid: PROJECT });
  });

  it('finds nothing in a page that names neither', () => {
    assert.deepEqual(scopeFromUrl(`${FRONT}/profile/`), {});
  });

  it('prefers the URL over the remembered filter', () => {
    // The filter is where the reader looked once; the URL is where they are
    // looking now. Someone who has just switched organisation would otherwise
    // get the previous one's report.
    const filter = JSON.stringify({ organization: { uuid: ORG_B, name: 'Previous U' } });
    const context = portalContext(readings({ filter }));
    assert.equal(context.customerUuid, ORG_A);
    // And the stale *name* is dropped with it, rather than left to win a
    // fallback the UUID was supposed to have settled.
    assert.equal(context.customerName, null);
  });

  it('uses the remembered filter when the URL names no organisation', () => {
    const filter = JSON.stringify({ organization: { uuid: ORG_B, name: 'Fictional U' } });
    const context = portalContext(readings({ href: `${FRONT}/profile/`, filter }));
    assert.equal(context.customerUuid, ORG_B);
    assert.equal(context.customerName, 'Fictional U');
  });

  it('reads the organisation a token belongs to out of users/me', () => {
    // The last resort, and the only one that works from any page at all.
    const me = {
      permissions: [
        { scope_type: 'project', scope_name: 'Some Project', role: 'Researcher' },
        { scope_type: 'customer', scope_name: 'Fictional U', role: 'CUSTOMER.READER' },
      ],
    };
    assert.equal(customerFromPermissions(me), 'Fictional U');
  });

  it('answers null when the account holds no customer-scoped permission', () => {
    const me = { permissions: [{ scope_type: 'project', scope_name: 'Some Project' }] };
    assert.equal(customerFromPermissions(me), null);
    assert.equal(customerFromPermissions(null), null);
  });
});

describe('the whole handover', () => {
  it('carries the token through unchanged and normalises a missing one to null', () => {
    assert.equal(portalContext(readings({ token: 'abc' })).token, 'abc');
    assert.equal(portalContext(readings({ token: '' })).token, null);
    assert.equal(portalContext(readings({ token: null })).token, null);
  });

  it('answers a shape the caller can destructure even with no readings at all', () => {
    // `background.js` hands null through when the tab could not be scripted,
    // and the page must reach its "paste a token" path rather than throw.
    const context = portalContext(null);
    assert.deepEqual(context, {
      token: null, apiUrl: null, customerUuid: null, customerName: null,
    });
  });

  it('yields a token and a scope from a plain dashboard visit', () => {
    const context = portalContext(readings({ requestOrigins: [BACK] }));
    assert.deepEqual(context, {
      token: 'not-a-real-token',
      apiUrl: BACK,
      customerUuid: ORG_A,
      projectUuid: null,
      customerName: null,
    });
  });
});
