/**
 * The handover: toolbar click → read the portal tab → report tab starts.
 *
 * This is the one seam with no other coverage, and the hardest to look at
 * directly — an extension's own pages are invisible to every browser-automation
 * tool, so "load it and see" is the only manual check and it is a slow one. So
 * `background.js` is driven here against a fake `chrome`, which is enough
 * because the file is almost entirely orchestration: which tab to read, what to
 * do when it cannot be read, and who is told what.
 *
 * **The race in the middle is the reason this file exists.** `tabs.create`
 * resolves with the new tab, and only then can the readings be filed under its
 * id — but the page in that tab has been loading the whole time and may ask
 * first. A dropped handover looks exactly like "no portal tab was open", it
 * lands the reader on a paste form for no reason, and it happens on some runs
 * and not others. Nothing about that is visible in a diff.
 *
 * Run with `node web/tests/background.test.mjs`.
 */

import assert from 'node:assert/strict';
import { before, describe, it } from 'node:test';

const PORTAL = 'https://portal.example.test/organizations/'
  + '0123456789abcdef0123456789abcdef/dashboard/';

/** Captured listeners, filled when `background.js` is imported. */
const on = { clicked: null, message: null, removed: null };

/** Everything the fake `chrome` is asked, and what it should answer. */
const world = {
  tabs: [],
  /** What `executeScript` hands back, or null for a tab that cannot be read. */
  readings: null,
  /** Held open so a test can decide when `tabs.create` resolves. */
  releaseCreate: null,
  created: [],
  scripted: [],
  nextTabId: 100,
};

function fakeChrome() {
  return {
    action: { onClicked: { addListener: (fn) => { on.clicked = fn; } } },
    runtime: {
      onMessage: { addListener: (fn) => { on.message = fn; } },
      getURL: (path) => `chrome-extension://fake/${path}`,
    },
    tabs: {
      onRemoved: { addListener: (fn) => { on.removed = fn; } },
      query: async (filter) => {
        if (filter.active) return world.tabs.filter((tab) => tab.active);
        return world.tabs.filter((tab) => tab.url?.startsWith('https://portal.'));
      },
      create: async ({ url }) => {
        const tab = { id: world.nextTabId, url, active: true };
        world.nextTabId += 1;
        world.created.push(tab);
        // A test that sets `releaseCreate` decides when this resolves, which is
        // how the report tab is made to ask before the readings are filed.
        if (world.releaseCreate) await new Promise((r) => { world.releaseCreate = r; });
        return tab;
      },
    },
    scripting: {
      executeScript: async ({ target }) => {
        world.scripted.push(target.tabId);
        if (world.readings === null) throw new Error('Cannot access contents of the page');
        return [{ result: world.readings }];
      },
    },
  };
}

/** Ask the background what this report tab's context is, as the page does. */
function ask(tabId, message = { type: 'context' }) {
  return new Promise((resolve) => {
    const async_ = on.message(message, { tab: { id: tabId } }, resolve);
    // A listener that answers synchronously has already resolved by here; one
    // that returns true will call `resolve` later. Anything else answers
    // nothing, which is the promise rejection the real API produces.
    if (async_ !== true) { /* already resolved, or deliberately unanswered */ }
  });
}

function portalTabOpen() {
  world.tabs = [{ id: 1, url: PORTAL, active: true }];
  world.readings = {
    href: PORTAL,
    origin: 'https://portal.example.test',
    token: 'a-token',
    filter: null,
    iconOrigin: 'https://portal-api.example.test',
    requestOrigins: [],
  };
}

before(async () => {
  globalThis.chrome = fakeChrome();
  await import('../src/background.js');
  assert.ok(on.clicked && on.message, 'background.js registered its listeners');
});

// --------------------------------------------------------------------------

describe('the ordinary path', () => {
  it('reads the portal tab and hands the report tab its context', async () => {
    portalTabOpen();
    world.created = [];
    await on.clicked();

    const tab = world.created.at(-1);
    const context = await ask(tab.id);
    assert.equal(context.token, 'a-token');
    assert.equal(context.apiUrl, 'https://portal-api.example.test');
    assert.equal(context.customerUuid, '0123456789abcdef0123456789abcdef');
  });

  it('hands the context over exactly once', async () => {
    // The readings carry a token. Holding one after it has been delivered buys
    // nothing, so a second ask — a reload, say — gets nothing and falls back.
    portalTabOpen();
    world.created = [];
    await on.clicked();
    const tab = world.created.at(-1);

    await ask(tab.id);
    assert.equal(await ask(tab.id), null);
  });

  it('reads the active tab when it is the portal, not some other portal tab', async () => {
    world.tabs = [
      { id: 7, url: 'https://portal.example.test/profile/', active: true },
      { id: 8, url: PORTAL, active: false },
    ];
    world.readings = { href: PORTAL, origin: 'https://portal.example.test', token: 'x' };
    world.scripted = [];
    world.created = [];
    await on.clicked();
    assert.deepEqual(world.scripted, [7]);
  });

  it('falls back to any open portal tab when the active one is not one', async () => {
    // Pressing the button from a different tab is the common case once someone
    // has the report open, and refusing it would be a needless dead end.
    world.tabs = [
      { id: 7, url: 'https://example.test/somewhere', active: true },
      { id: 8, url: PORTAL, active: false },
    ];
    world.readings = { href: PORTAL, origin: 'https://portal.example.test', token: 'x' };
    world.scripted = [];
    world.created = [];
    await on.clicked();
    assert.deepEqual(world.scripted, [8]);
  });
});

describe('the race between the new tab and its readings', () => {
  it('answers a report tab that asks before the readings are filed', async () => {
    // The bug this guards: `tabs.create` resolves, and only *then* can the
    // readings be filed under the tab's id — while the page has been loading
    // the whole time. Losing the race would show the paste form at random.
    portalTabOpen();
    world.created = [];
    world.releaseCreate = () => {}; // hold `tabs.create` open

    const clicked = on.clicked();
    await new Promise((r) => { setTimeout(r, 10); });

    // The page is up and asking, and nothing has been filed for it yet. The tab
    // exists — `create` pushed it before it blocked — but the click handler has
    // not been handed it back yet, which is exactly the window in question.
    const asked = ask(world.created.at(-1).id);
    await new Promise((r) => { setTimeout(r, 10); });

    world.releaseCreate(); // the tab now exists; the readings are filed
    world.releaseCreate = null;
    await clicked;

    const context = await asked;
    assert.ok(context, 'the parked request was answered rather than refused');
    assert.equal(context.token, 'a-token');
  });

  it('gives up on a report tab nobody is bringing readings for', async () => {
    // Opened by typing its URL, or reloaded after the worker was torn down.
    // Both are legitimate and both must reach the paste form rather than hang.
    const started = Date.now();
    assert.equal(await ask(9999), null);
    assert.ok(Date.now() - started >= 1000, 'it waited before concluding that');
  });
});

describe('when the portal cannot be read', () => {
  it('still opens the report, and tells it there is no context', async () => {
    // A tab that will not be scripted must not stop the report opening: the
    // paste form lives there, and it is the whole fallback.
    world.tabs = [{ id: 1, url: PORTAL, active: true }];
    world.readings = null; // executeScript throws
    world.created = [];

    await on.clicked();
    assert.equal(world.created.length, 1);
    assert.equal(await ask(world.created[0].id), null);
  });

  it('opens the report when no portal tab is open at all', async () => {
    world.tabs = [];
    world.created = [];
    await on.clicked();
    assert.equal(world.created.length, 1);
    assert.equal(await ask(world.created[0].id), null);
  });
});

describe('renewing an expired token', () => {
  it('re-reads the portal tab the report came from', async () => {
    // Tokens live an hour. The front end in the portal tab has been refreshing
    // this one all along, so the recovery costs the reader nothing — provided
    // the report goes back to the *same* tab it was opened from.
    portalTabOpen();
    world.created = [];
    await on.clicked();
    const tab = world.created.at(-1);
    await ask(tab.id);

    world.readings = { ...world.readings, token: 'a-fresher-token' };
    world.scripted = [];
    assert.equal(await ask(tab.id, { type: 'refresh' }), 'a-fresher-token');
    assert.deepEqual(world.scripted, [1], 'it went back to the portal tab');
  });

  it('answers null when the portal tab has since been closed', async () => {
    world.tabs = [];
    world.readings = null;
    assert.equal(await ask(4242, { type: 'refresh' }), null);
  });
});
