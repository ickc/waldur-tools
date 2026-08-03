/**
 * The report, assembled live in the tab.
 *
 * The order things happen in is the whole design. A snapshot-backed report can
 * compute everything and then draw; this one has a reader watching, so it draws
 * as early as it honestly can:
 *
 * 1. **Five cheap endpoints in parallel** -- allocations, the accounting
 *    summary, customers, projects and invoices, about a hundred rows between
 *    them. That is enough for the credit tiles and the organisation list, which
 *    appear within a second.
 * 2. **The usage table, newest month first, six months in flight.** Every month
 *    that lands re-renders the figures, so the headline chart is on screen long
 *    before the history behind it has finished arriving.
 * 3. **The rest in the background** -- the daily usage reports behind the queue
 *    figure, and the associations behind the "people with access" denominator.
 *    Neither blocks anything above it.
 *
 * Because the usage endpoint is not scoped to an organisation, changing the
 * organisation, the node count or the share re-renders from rows already in
 * hand and costs no request at all.
 */

import { MonthCache } from './store.js';
import {
  DEFAULT_PAGE_SIZE, WaldurClient, WaldurError, pullByMonth,
} from './api.js';
import {
  figureEngagement, figureHeatmap, figureProjects, figureQueue, figureShare,
  figureTotalsByProject, format,
} from './figures.js';
import { PROSE, esc, intro, method, section, tableView, tile } from './page.js';
import { currentTheme, preferredTheme, setTheme } from './palette.js';
import * as reports from './reports.js';

const USAGE = 'openportal-allocation-user-usage';

const PLOT_CONFIG = { displaylogo: false, responsive: true };

/** Everything fetched, plus what the reader has chosen to do with it. */
const state = {
  client: null,
  cache: null,
  apiUrl: '',
  asOf: reports.isoDate(),
  raw: {
    allocations: [],
    summary: [],
    customers: [],
    projects: [],
    invoices: [],
    usageReports: [],
    associations: null,
  },
  /** Keyed by month, because a month can be delivered twice on a cache retry. */
  usageByMonth: new Map(),
  options: { nodes: 384, share: 0.1, customer: null },
  loading: false,
  headRendered: false,
  sections: new Set(),
};

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

let pending = null;

/**
 * Ask for a render, at most one every 300ms.
 *
 * Nineteen months arriving over a few seconds would otherwise redraw six
 * figures nineteen times, and plotly is not free. Coalescing keeps the page
 * responsive while still visibly filling in.
 */
function scheduleRender() {
  if (pending !== null) return;
  pending = setTimeout(() => {
    pending = null;
    try {
      render();
    } catch (error) {
      showError('Could not draw the report', error);
    }
  }, 300);
}

function usageRows() {
  const rows = [];
  for (const month of state.usageByMonth.values()) rows.push(...month);
  return rows;
}

function render() {
  const { nodes, share, customer } = state.options;
  const { allocations, summary, customers, projects, invoices, usageReports } = state.raw;
  const scope = reports.inScope(allocations);
  const rows = reports.monthlyRows(usageRows(), scope, customer);
  if (!rows.length) {
    if (!state.loading) {
      showError(
        'No usage rows',
        new Error(
          `Nothing to plot for ${customer ?? 'the projects this token administers'}. ` +
            'Either no project of that organisation has run anything, or the token ' +
            'administers none of its projects — try another organisation above.',
        ),
      );
    }
    return;
  }

  const totals = reports.monthlyTotals(rows, { nodes, share, asOf: state.asOf });
  const perProject = reports.monthly(rows, { nodes, share });
  const months = totals.map((row) => row.month);
  const complete = totals.filter((row) => !row.is_partial);
  const latest = complete.length ? complete[complete.length - 1] : null;
  const codes = [...new Set(perProject.map((row) => row.project_code))];

  const allocation = reports.allocationsReport(scope, summary, { asOf: state.asOf, customer });
  const awarded = reports.committed(allocation, months, state.asOf);
  const existing = reports.projectsExisting(projects, months, customer);
  const [creditHeld, unallocated] = reports.creditPosition(customers, customer);
  const monthlyShare = totals[totals.length - 1].entitlement_node_hours || 1;
  const unallocatedMonths = unallocated / monthlyShare;
  const queue = reports.queueMonthly(usageReports, codes);

  const awardedPct = latest
    ? (100 * awarded[months.indexOf(latest.month)]) / latest.entitlement_node_hours
    : 0;
  const best = complete.length
    ? complete.reduce((a, b) => (b.pct_of_entitlement > a.pct_of_entitlement ? b : a))
    : null;

  // -- header and method, once the numbers behind them are known ------------
  const span = `${reports.monthLabelLong(months[0])} to ${
    reports.monthLabelLong(months[months.length - 1])
  }`;
  el('head').innerHTML = intro({
    nodes, share, customer, span, bestMonth: best,
    creditHeld, unallocated, unallocatedMonths, awardedPct,
  });
  el('method').innerHTML = method({
    nodes, share, customer, creditHeld, unallocated, unallocatedMonths,
  });

  // -- tiles ---------------------------------------------------------------
  const tiles = [];
  if (latest) {
    tiles.push(
      tile(
        `${reports.monthLabelLong(latest.month)} — share used`,
        `${format(latest.pct_of_entitlement, 0)}%`,
        `${format(latest.mean_nodes, 1)} of the ${format(nodes * share, 1)} nodes we hold`,
        true,
      ),
    );
  }
  if (complete.length) {
    // Hours over hours, not the mean of the monthly percentages: a quiet month
    // and a busy one are different sizes, and averaging their ratios would
    // weigh them the same.
    const used = complete.reduce((total, row) => total + row.node_hours, 0);
    const entitled = complete.reduce((total, row) => total + row.entitlement_node_hours, 0);
    tiles.push(
      tile(
        'Since we started',
        `${format((100 * used) / entitled, 0)}%`,
        `over ${complete.length} complete months`,
      ),
    );
  }
  if (latest && awardedPct) {
    // Read off the latest complete month, not summed over every project that
    // has ever held an award: two projects whose windows never overlapped were
    // never committed at the same time.
    const rate = awarded[months.indexOf(latest.month)];
    tiles.push(
      tile(
        'Awarded to projects',
        `${format(awardedPct, 0)}%`,
        `of our share is promised to anyone — ${format(rate, 0)} node hours a month`,
      ),
    );
  }
  if (unallocated) {
    tiles.push(
      tile(
        'Credit never allocated',
        format(unallocated, 0),
        `node hours held but assigned to no project — ${format(unallocatedMonths, 1)} ` +
          'months of our share',
      ),
    );
  }
  if (latest) {
    const peak = existing.length ? Math.max(...existing.map((row) => row.projects)) : 0;
    tiles.push(
      tile(
        'Projects running',
        peak ? `${latest.active_projects} / ${peak}` : `${latest.active_projects}`,
        'ran something in the last complete month',
      ),
    );
    tiles.push(
      tile(
        'People running',
        state.raw.associations === null
          ? `${latest.active_users}`
          : `${latest.active_users} / ${reports.peopleWithAccess(state.raw.associations, codes)}`,
        state.raw.associations === null
          ? 'ran something in the last complete month'
          : 'of everyone with access ran something',
      ),
    );
  }
  el('tiles').innerHTML = tiles.join('');

  // -- reconcile ------------------------------------------------------------
  renderReconcile(rows, invoices, customer);

  // -- figures --------------------------------------------------------------
  ensureSection('share', 'Are we using our share?');
  ensureSection('projects', 'Where the usage comes from');
  ensureSection('heatmap', 'Which projects are alive');
  ensureSection('totals', 'How concentrated we are');
  ensureSection('engagement', 'Are more people using it?');
  if (queue.length) ensureSection('queue', 'Demand, and what it cost to wait');

  draw('share', figureShare(totals, nodes, share, awarded));
  table('share', totals, [
    ['month', 'Month', 'month'],
    ['node_hours', 'Node hours used', 'number'],
    ['entitlement_node_hours', 'Share worth', 'number'],
    ['pct_of_entitlement', '% of share', 'number'],
    ['active_projects', 'Active projects'],
    ['active_users', 'Active users'],
  ]);

  draw('projects', figureProjects(reports.rankedBands(perProject), totals));
  table('projects', reports.projectSummary(perProject), [
    ['project_name', 'Project'],
    ['node_hours', 'Node hours, all months', 'number'],
    ['months_with_usage', 'Months with usage'],
  ]);

  draw('heatmap', figureHeatmap(perProject, totals, allocation));
  table('heatmap', allocation, [
    ['project_name', 'Project'],
    ['total_credits', 'Node hours awarded', 'number'],
    ['award_months', 'Months'],
    ['mean_monthly_allocation', 'Mean monthly allocation', 'number'],
    ['start_date', 'From'],
    ['end_date', 'To'],
  ]);

  draw('totals', figureTotalsByProject(reports.totalsByProject(perProject)));
  draw('engagement', figureEngagement(totals, existing));

  if (queue.length) {
    draw('queue', figureQueue(queue));
    table('queue', queue, [
      ['month', 'Month', 'month'],
      ['num_jobs', 'Jobs'],
      ['mean_wait_hours', 'Mean wait (h)', 'number'],
    ]);
  }

  setTheme(state.headRendered ? currentTheme() : preferredTheme());
  state.headRendered = true;
}

function ensureSection(id, heading) {
  if (state.sections.has(id)) return;
  el('figures').insertAdjacentHTML('beforeend', section(id, heading, PROSE[id]));
  state.sections.add(id);
}

function draw(id, figure) {
  if (figure === null) return;
  Plotly.react(el(`plot-${id}`), figure.data, figure.layout, PLOT_CONFIG);
}

function table(id, rows, columns) {
  el(`table-${id}`).innerHTML = tableView(rows, columns);
}

/**
 * The invoice cross-check, as a badge rather than a separate command.
 *
 * `waldur-tools` asks you to run `report reconcile` before quoting anything out
 * of the visual report, and nobody reading a web page is going to. The check is
 * one cheap endpoint and the same arithmetic, so it belongs on the page: it is
 * the only independent measurement of these node hours, and the failure it
 * catches -- an unstable pull -- looks exactly like a finding until you check.
 */
function renderReconcile(rows, invoices, customer) {
  const badge = el('reconcile-badge');
  if (!invoices.length) {
    badge.innerHTML = '<span class="badge">invoices not loaded</span>';
    return;
  }
  const checked = reports.reconcile(rows, invoices, { customer, asOf: state.asOf });
  const bad = checked.filter((row) => row.status !== 'ok' && row.status !== 'no invoice');
  if (!bad.length) {
    badge.innerHTML =
      `<span class="badge ok" title="Usage agrees with incurred_costs in every ` +
      `invoiced month">usage reconciles with ${checked.length} months of invoices</span>`;
    return;
  }
  const detail = bad
    .map((row) => `${reports.monthLabel(row.month)}: ${row.status}`)
    .join(', ');
  badge.innerHTML =
    `<span class="badge warn" title="${esc(detail)}">${bad.length} month` +
    `${bad.length === 1 ? '' : 's'} do not reconcile — suspect the pull</span>`;
}

function showError(heading, error) {
  el('errors').innerHTML =
    `<div class="error"><h2>${esc(heading)}</h2><p>${esc(error.message)}</p></div>`;
}

function clearError() {
  el('errors').innerHTML = '';
}

function progress(text, done, of) {
  const box = el('progress');
  if (text === null) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  el('progress-text').textContent = text;
  el('progress-bar').style.width = of ? `${(100 * done) / of}%` : '0';
}

// --------------------------------------------------------------------------
// The pipeline
// --------------------------------------------------------------------------

async function run({ refresh = false } = {}) {
  clearError();
  state.loading = true;
  state.asOf = reports.isoDate();
  if (refresh) state.usageByMonth.clear();

  // Swapped before the first request rather than after it, so the progress line
  // below is somewhere the reader can see while it is the only thing happening.
  el('gate').hidden = true;
  el('report').hidden = false;
  progress('Reading allocations, credits and invoices…', 0, 1);

  // Five small endpoints, all at once: about a hundred rows between them, and
  // everything the header and the credit tiles need.
  const [allocations, summary, customers, projects, invoices] = await Promise.all([
    state.client.list('openportal-allocations'),
    state.client.list('openportal-accounting-summary'),
    state.client.list('customers'),
    state.client.list('projects'),
    state.client.list('invoices'),
  ]);
  Object.assign(state.raw, { allocations, summary, customers, projects, invoices });

  populateCustomers(allocations);
  el('controls').hidden = false;

  // The usage table: the only expensive pull, and the one every figure needs.
  progress('Reading monthly usage…', 0, 1);
  await pullByMonth(state.client, USAGE, {
    pageSize: DEFAULT_PAGE_SIZE,
    cache: state.cache,
    onMonth: (rows, { year, month }) => {
      // Keyed, not appended: a cache mismatch makes `pullByMonth` start over,
      // and appending would then count those months twice.
      state.usageByMonth.set(reports.monthKey(year, month), rows);
      scheduleRender();
    },
    onProgress: ({ done, of, rows }) => {
      progress(`Reading monthly usage — ${done} of ${of} months, ${format(rows, 0)} rows`,
        done, of);
    },
  });

  state.loading = false;
  progress(null);
  scheduleRender();
  await showCacheNote();

  // The tail: neither figure below blocks anything above it, and the page is
  // already complete and readable without them.
  loadExtras().catch((error) => showError('Could not load the supporting figures', error));
}

/**
 * The two endpoints nothing on the critical path needs.
 *
 * `openportal-project-usage-reports` carries a fat per-day blob and backs one
 * figure; `openportal-associations` is thousands of rows across the whole
 * machine and backs one denominator on one tile. Both are worth having and
 * neither is worth waiting for.
 */
async function loadExtras() {
  state.raw.usageReports = await state.client.list('openportal-project-usage-reports');
  scheduleRender();
  state.raw.associations = await state.client.list('openportal-associations');
  scheduleRender();
}

/**
 * The organisations this token can see, largest first.
 *
 * The portal is multi-tenant and one token often administers projects belonging
 * to several separately funded organisations; counting them together overstates
 * any one of them. The default matches the command-line tool, and falls back to
 * whichever organisation holds the most projects when that one is not visible.
 */
function populateCustomers(allocations) {
  const select = el('customer');
  const found = reports.customersInScope(allocations);
  const options = [
    ...found.map(({ name, projects }) => ({
      value: name,
      label: `${name} (${projects} project${projects === 1 ? '' : 's'})`,
    })),
    { value: '', label: 'Every project this token administers' },
  ];
  select.innerHTML = options
    .map((option) => `<option value="${esc(option.value)}">${esc(option.label)}</option>`)
    .join('');

  if (state.options.customer === null) {
    const names = found.map((row) => row.name);
    state.options.customer = names.includes(reports.DEFAULT_CUSTOMER)
      ? reports.DEFAULT_CUSTOMER
      : (names[0] ?? null);
  }
  select.value = state.options.customer ?? '';
}

async function showCacheNote() {
  const summary = await state.cache.summary();
  el('cache-note').textContent = summary.months
    ? `${summary.months} complete months (${format(summary.rows, 0)} rows) are cached ` +
      'in this browser, and will not be fetched again.'
    : '';
}

// --------------------------------------------------------------------------
// Wiring
// --------------------------------------------------------------------------

/** "10%", "10", "0.1" all mean the same thing to someone typing it in a box. */
function parseShare(text) {
  const trimmed = String(text).trim();
  const value = Number(trimmed.replace('%', ''));
  if (!Number.isFinite(value) || value <= 0) return null;
  if (trimmed.includes('%')) return value / 100;
  return value > 1 ? value / 100 : value;
}

/**
 * Where the token lives.
 *
 * In a variable, and -- only if the reader asks -- in `chrome.storage.session`,
 * which is held in memory and dropped when the browser closes. Never
 * `localStorage`: a token that outlives the browser is a token still sitting on
 * disk long after the portal expired it hours later.
 */
const session = {
  async get() {
    try {
      const stored = await chrome.storage.session.get('token');
      return stored.token ?? null;
    } catch {
      return null;
    }
  },
  async set(token) {
    try {
      await chrome.storage.session.set({ token });
    } catch {
      // Not being able to remember it is a re-paste, not a failure.
    }
  },
  async clear() {
    try {
      await chrome.storage.session.remove('token');
    } catch {
      // As above.
    }
  },
};

/**
 * Make sure the extension may read the given API host.
 *
 * The Isambard deployment is in `host_permissions` and needs no asking. Another
 * Waldur is covered by `optional_host_permissions` and is requested here, which
 * keeps the extension useful against a second deployment without it claiming
 * access to every site at install time.
 */
async function grantHost(apiUrl) {
  let origin;
  try {
    origin = `${new URL(apiUrl).origin}/*`;
  } catch {
    return false;
  }
  try {
    if (await chrome.permissions.contains({ origins: [origin] })) return true;
    return await chrome.permissions.request({ origins: [origin] });
  } catch {
    // No permissions API means this is not running as an extension at all --
    // let the fetch itself produce the honest error.
    return true;
  }
}

async function start() {
  const token = el('token').value.trim();
  const apiUrl = el('api-url').value.trim();
  const nodes = Number(el('nodes').value);
  const share = parseShare(el('share').value);

  if (!token) return gateError('Paste a portal API token to continue.');
  if (!Number.isFinite(nodes) || nodes <= 0) return gateError('Node count must be a number.');
  if (share === null) return gateError('Share must be a percentage, like 10%.');

  // The manifest grants the Isambard portal outright; any other deployment has
  // to be asked for, and asking has to happen inside the click that triggered
  // it -- so this comes before the first await in the function.
  if (!(await grantHost(apiUrl))) {
    return gateError(
      `Reading ${apiUrl} needs permission for that host, and it was not granted.`,
    );
  }

  state.options.nodes = nodes;
  state.options.share = share;
  state.apiUrl = apiUrl;
  state.client = new WaldurClient({ apiUrl, token });
  state.cache = new MonthCache(apiUrl);

  if (el('remember').checked) await session.set(token);
  else await session.clear();

  el('run').disabled = true;
  el('gate-error').innerHTML = '';
  try {
    await run();
  } catch (error) {
    state.loading = false;
    progress(null);
    if (el('report').hidden) gateError(error.message);
    else {
      showError(
        error instanceof WaldurError ? 'The portal pull did not complete' : 'Something broke',
        error,
      );
    }
  } finally {
    el('run').disabled = false;
  }
  return undefined;
}

function gateError(message) {
  el('gate-error').innerHTML = `<div class="error"><p>${esc(message)}</p></div>`;
  return undefined;
}

el('run').addEventListener('click', start);
el('token').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') start();
});

el('customer').addEventListener('change', (event) => {
  // Free: the usage endpoint is not scoped to an organisation, so every row is
  // already in hand and only the filter over them changes.
  state.options.customer = event.target.value || null;
  clearError();
  scheduleRender();
});

el('refresh').addEventListener('click', async () => {
  clearError();
  try {
    await run({ refresh: true });
  } catch (error) {
    showError('Refresh failed', error);
  }
});

el('forget').addEventListener('click', async () => {
  await state.cache.clearAll();
  await session.clear();
  await showCacheNote();
  el('forget').textContent = 'Cached data cleared';
  setTimeout(() => {
    el('forget').textContent = 'Clear cached data';
  }, 2000);
});

// The theme button is inside markup the report rebuilds, so the listener is on
// the document rather than on a node that will not survive the next render.
document.addEventListener('click', (event) => {
  if (event.target.classList.contains('theme')) {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  }
});

document.documentElement.setAttribute('data-theme', preferredTheme());

// The bundle is written by `pixi run web-vendor` rather than committed, so a
// freshly cloned checkout can reach this page without it. Say so plainly
// instead of failing at the first `Plotly.react`.
if (typeof Plotly === 'undefined') {
  el('gate-error').innerHTML =
    '<div class="error"><h2>plotly.js is missing</h2><p>The extension ships the ' +
    'bundle rather than fetching it from a CDN, and it has not been written yet. ' +
    'Run <code>pixi run web-vendor</code> in the repository, then reload this ' +
    'page.</p></div>';
  el('run').disabled = true;
}

(async () => {
  const stored = await session.get();
  if (stored) {
    el('token').value = stored;
    el('remember').checked = true;
  }
  const cache = new MonthCache(el('api-url').value.trim());
  state.cache = cache;
  await showCacheNote();
})();
