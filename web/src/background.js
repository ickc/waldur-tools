/**
 * The handover: read the portal tab, then open the report already knowing.
 *
 * The reader is signed in to the portal and looking at their organisation's
 * dashboard. Everything the report used to ask them for is in that tab -- the
 * token in `localStorage`, the API URL in the page, the organisation in the URL
 * -- so the button reads it and hands it over, and the report starts.
 *
 * A tab, not a popup. This report is a page -- six figures, a table under each
 * and a method section -- and a popup that closes when the reader clicks away
 * would throw the pull away with it. A tab also gives the page an extension
 * origin, which is what lets it read the API at all: the deployment's CORS
 * allowlist holds only the portal's own front end, and `host_permissions` is
 * the way around that.
 *
 * **Nothing is stored here.** The readings are held in a `Map` in this worker,
 * keyed by the report tab they were taken for, and deleted when that tab first
 * asks for them. A service worker is torn down when idle, which takes the Map
 * with it -- that is a property worth having, not one to work around.
 */

import { readPortalContext, isPortalUrl, portalContext } from './portal.js';

/** Readings waiting to be collected, keyed by the report tab they are for. */
const pending = new Map();

/**
 * Report tabs that asked before the readings were filed, keyed the same way.
 *
 * There is a race here and it is not theoretical: `chrome.tabs.create` resolves
 * with the tab, and only *then* can the readings be filed under its id -- but
 * the page in that tab is loading the whole time and may well ask first. Losing
 * that race would drop the reader onto the paste form for no reason, and
 * intermittently, which is the worst way to find a bug.
 *
 * So a request that finds nothing is parked rather than refused, and `deliver`
 * answers it when the readings arrive.
 */
const waiting = new Map();

/**
 * How long a report tab waits for readings before concluding there are none.
 *
 * Not every report tab has any coming: one opened by typing its URL, or
 * reloaded after the worker was torn down, is answered by this timeout and
 * shows the paste form -- correctly. It only has to outlast a tab creation, so
 * it is short enough not to be noticed when it is doing nothing.
 */
const HANDOVER_TIMEOUT_MS = 2000;

/** File readings for a report tab, answering it if it is already asking. */
function deliver(tabId, context) {
  const parked = waiting.get(tabId);
  if (parked) {
    clearTimeout(parked.timer);
    waiting.delete(tabId);
    parked.respond(context);
    return;
  }
  pending.set(tabId, context);
}

/** Where the token was read from, so it can be read again when it expires. */
const source = new Map();

/**
 * Read the three things off a portal tab.
 *
 * `activeTab` covers whichever tab was active when the button was clicked, and
 * the manifest names the Isambard front end outright so a portal tab that is
 * merely *open* can be read too. Failure is silent by design: every caller's
 * fallback is the paste form, which is a worse experience and not an error.
 */
async function readFrom(tabId) {
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: readPortalContext,
    });
    return result?.result ?? null;
  } catch {
    // No permission for that host, a chrome:// page, a tab that closed under
    // us. All of them mean the same thing: ask the reader instead.
    return null;
  }
}

/** The portal tab to read: the active one if it is one, else any open one. */
async function findPortalTab() {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active && isPortalUrl(active.url ?? '')) return active;
  const open = await chrome.tabs.query({ url: 'https://portal.isambard.ac.uk/*' });
  return open[0] ?? null;
}

chrome.action.onClicked.addListener(async () => {
  const portal = await findPortalTab();
  const readings = portal ? await readFrom(portal.id) : null;
  const context = readings ? portalContext(readings) : null;

  const tab = await chrome.tabs.create({ url: chrome.runtime.getURL('src/report.html') });
  if (portal) source.set(tab.id, portal.id);
  // Filed even when null, so a tab whose portal could not be read is told so at
  // once rather than sitting through the timeout above.
  deliver(tab.id, context);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  pending.delete(tabId);
  source.delete(tabId);
  const parked = waiting.get(tabId);
  if (parked) {
    clearTimeout(parked.timer);
    waiting.delete(tabId);
  }
});

/**
 * Two messages, both from the report page.
 *
 * `context` is asked once, on load, and the readings are dropped as they are
 * handed over: they are a token, and holding one after it has been delivered
 * buys nothing.
 *
 * `refresh` is for later. Portal tokens live an hour, so a report left open
 * over lunch will meet a 401 -- and the answer to that is not to make the
 * reader fetch a new one, it is to read the portal tab again, where the front
 * end has been quietly refreshing it all along.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id;
  if (tabId === undefined) return false;

  if (message?.type === 'context') {
    if (pending.has(tabId)) {
      // Dropped as it is handed over: these readings include a token, and
      // holding one after it has been delivered buys nothing.
      sendResponse(pending.get(tabId));
      pending.delete(tabId);
      return false;
    }
    // Nothing filed yet. Either the readings are moments away -- the tab was
    // created before they could be -- or this tab was never opened by the
    // button and none are coming. Park it and let the timeout tell them apart.
    const timer = setTimeout(() => {
      waiting.delete(tabId);
      sendResponse(null);
    }, HANDOVER_TIMEOUT_MS);
    waiting.set(tabId, { timer, respond: sendResponse });
    return true; // The response is asynchronous.
  }

  if (message?.type === 'refresh') {
    (async () => {
      const known = source.get(tabId);
      const portal = known !== undefined ? { id: known } : await findPortalTab();
      if (!portal) return sendResponse(null);
      const readings = await readFrom(portal.id);
      return sendResponse(readings?.token ?? null);
    })();
    return true; // The response is asynchronous.
  }

  return false;
});
