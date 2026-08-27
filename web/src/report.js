/**
 * The report, assembled live in the tab.
 *
 * **It starts on its own.** The reader was on their organisation's dashboard
 * when they pressed the button, and `background.js` read the token, the API URL
 * and the organisation UUID out of that tab and handed them over. Nothing is
 * asked for; the gate in `report.html` appears only when one of those readings
 * failed, and says which. See `portal.js` for what is read and how far each
 * reading can be trusted.
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
  figureStorageProjects, figureStorageUsers, figureTotalsByProject, format,
} from './figures.js';
import {
  PROSE, esc, intro, method, reconcileTable, section, storageStaleness, tableView, tile,
} from './page.js';
import { currentTheme, preferredTheme, setTheme } from './palette.js';
import { customerFromPermissions } from './portal.js';
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
    storageReports: [],
    /** `users/me/`: the token's own account, for the organisation it belongs to. */
    me: null,
    associations: null,
    /** Rows the associations pull handed back twice; see `loadExtras`. */
    associationRepeats: 0,
  },
  /** Keyed by month, because a month can be delivered twice on a cache retry. */
  usageByMonth: new Map(),
  options: { nodes: reports.TOTAL_NODES, share: reports.DEFAULT_SHARE, customer: null },
  /**
   * The organisation the portal tab was showing, as a UUID.
   *
   * Held rather than resolved immediately because the name it maps to arrives
   * with the `customers` endpoint, in the first wave of the pull. A UUID is the
   * right thing to carry: it is what the portal URL states, it is stable across
   * a rename, and it belongs to whichever institution the reader came from --
   * which is the whole reason no organisation is named in this source.
   */
  customerUuid: null,
  /** The organisation name the portal tab remembered, when it offered no UUID. */
  customerName: null,
  /**
   * The project the portal tab was showing, as a UUID.
   *
   * A `/projects/<uuid>/` page names no organisation, and `portal.js` returns
   * the project rather than pretending otherwise. It resolves to one through
   * the allocations, which carry `project_uuid` alongside `customer_name` --
   * and it has to be kept for that, because a reader looking at one project is
   * telling us whose report they want just as plainly as a dashboard does.
   */
  projectUuid: null,
  /** Whether the token came from the portal tab, and so can be re-read. */
  tokenFromPortal: false,
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
  const {
    allocations, summary, customers, projects, invoices, usageReports, storageReports,
  } = state.raw;
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

  const totals = reports.monthlyTotals(rows, invoices, {
    nodes, share, scope, customer, asOf: state.asOf,
  });
  const perProject = reports.monthly(rows, invoices, { nodes, share, scope, customer });
  const months = totals.map((row) => row.month);
  const complete = totals.filter((row) => !row.is_partial);
  const latest = complete.length ? complete[complete.length - 1] : null;
  // A project billed after its allocation ended has no code left to look up.
  const codes = [...new Set(perProject.map((row) => row.project_code))].filter(Boolean);

  const allocation = reports.allocationsReport(scope, summary, { asOf: state.asOf, customer });
  const awarded = reports.committed(allocation, months, state.asOf);
  const existing = reports.projectsExisting(projects, months, customer);
  const [creditHeld, unallocated] = reports.creditPosition(customers, customer);
  const monthlyShare = totals[totals.length - 1].entitlement_node_hours || 1;
  const unallocatedMonths = unallocated / monthlyShare;
  const queue = reports.queueMonthly(usageReports, codes);

  // Scoped off the allocations rather than off the usage, and narrowed to the
  // selected organisation. The storage endpoint reports every project on the
  // machine, so an unscoped figure would put other organisations' disks on our
  // page; taking the codes from `perProject` instead would drop any project
  // that has never run a job, and a project with no compute is exactly the one
  // whose disk fills up unnoticed.
  const storageSamples = reports.storageSamples(
    storageReports,
    reports.scopedCodes(scope, customer),
  );
  const storageByMonth = reports.storageMonthly(storageSamples);
  const storageNow = reports.storageCurrent(storageSamples);
  const projectQuota = figureStorageProjects(storageByMonth);
  const userQuota = figureStorageUsers(storageByMonth);

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
          : state.raw.associationRepeats
            ? 'of everyone with access ran something — the portal paged that list ' +
              'inconsistently, so the denominator is short by a row or two'
            : 'of everyone with access ran something',
      ),
    );
  }
  el('tiles').innerHTML = tiles.join('');

  // -- reconcile ------------------------------------------------------------
  renderReconcile(rows, invoices, customer, scope);

  // -- figures --------------------------------------------------------------
  ensureSection('share', 'Are we using our share?');
  ensureSection('projects', 'Where the usage comes from');
  ensureSection('heatmap', 'Which projects are alive');
  ensureSection('totals', 'How concentrated we are');
  ensureSection('engagement', 'Are more people using it?');
  if (projectQuota !== null) ensureSection('storage-projects', 'How full the project disks are');
  if (userQuota !== null) ensureSection('storage-users', "How full people's own disks are");
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

  // The one thing about these two figures that the columns cannot say: the
  // collector behind them can stop without the endpoint saying so, and then
  // the heatmap simply ends rather than looking wrong.
  const stale = storageStaleness(storageNow);
  if (projectQuota !== null) {
    note('storage-projects', stale);
    draw('storage-projects', projectQuota);
    table('storage-projects', storageNow.filter((row) => row.kind === 'project'), [
      ['project_code', 'Project'],
      ['filesystem', 'Filesystem'],
      ['usage_bytes', 'Used', 'size'],
      ['limit_bytes', 'Quota', 'size'],
      ['fill_pct', '% full', 'number'],
      ['date', 'Last read'],
    ]);
  }

  if (userQuota !== null) {
    note('storage-users', stale);
    draw('storage-users', userQuota);
    table('storage-users', storageNow.filter((row) => row.kind === 'user'), [
      ['username', 'User'],
      ['project_code', 'Project'],
      ['filesystem', 'Filesystem'],
      ['usage_bytes', 'Used', 'size'],
      ['limit_bytes', 'Quota', 'size'],
      ['fill_pct', '% full', 'number'],
      ['date', 'Last read'],
    ]);
  }

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

/** The warning slot under a section's prose. Trusted markup, empty when silent. */
function note(id, html) {
  el(`note-${id}`).innerHTML = html;
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
function renderReconcile(rows, invoices, customer, scope) {
  const badge = el('reconcile-badge');
  const detail = el('reconcile');
  if (!invoices.length) {
    badge.innerHTML = '<span class="badge">invoices not loaded</span>';
    detail.innerHTML = '';
    return;
  }
  const checked = reports.reconcile(rows, invoices, { customer, scope, asOf: state.asOf });
  const bad = checked.filter((row) => !reports.RECONCILED.has(row.status));
  const ended = checked.filter((row) => row.status === 'project ended');
  // The table goes up either way. When it reconciles it is the evidence for the
  // badge; when it does not, it is the only thing that says which months and by
  // how much -- and telling a known accounting quirk from an unstable pull is
  // exactly the judgement the badge cannot make for the reader.
  detail.innerHTML = reconcileTable(checked);
  badge.innerHTML = bad.length
    ? `<span class="badge warn" title="${esc(
      bad.map((row) => `${reports.monthLabel(row.month)}: ${row.status}`).join(', '),
    )}">${bad.length} month${bad.length === 1 ? '' : 's'} do not reconcile — see below</span>`
    : ended.length
      ? `<span class="badge ok" title="${esc(
        `Usage agrees with incurred_costs everywhere it can. ${ended.length} month${
          ended.length === 1 ? '' : 's'
        } are short only because a project has since ended and the portal drops a ` +
        'terminated project\u2019s usage rows; the node hours on this page come from the ' +
        'invoice and are unaffected.',
      )}">usage reconciles with ${checked.length} months of invoices — ${ended.length} ` +
        `explained by ended projects</span>`
      : `<span class="badge ok" title="Usage agrees with incurred_costs in every invoiced ` +
        `month">usage reconciles with ${checked.length} months of invoices</span>`;
}

/**
 * What the reader is told about a failure, and what they can press about it.
 *
 * A message ending in "retry" is only advice if there is something to retry
 * with. The refresh button lives in the controls bar, which is not where anyone
 * is looking after watching the page fail -- and after a failure in the first
 * wave it is not on screen at all. So the action goes in the error itself, and
 * `retry` is the thing to run again: the same work, not a reload, so the cached
 * months are still there and only what failed is fetched.
 */
function showError(heading, error, { retry = null, label = 'Try again' } = {}) {
  const hint = error.transient ? `<p class="hint">${esc(TRANSIENT_HINT)}</p>` : '';
  el('errors').innerHTML =
    `<div class="error"><h2>${esc(heading)}</h2><p>${esc(error.message)}</p>${hint}</div>`;
  if (!retry) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'primary';
  button.textContent = label;
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Trying again…';
    try {
      await retry();
      clearError();
    } catch (again) {
      // Straight back to the same box, button and all: a second failure must
      // not be the one that leaves the reader with nothing to press.
      showError(heading, again, { retry, label });
    }
  });
  el('errors').querySelector('.error').append(button);
}

/**
 * Said whenever a failure carries `transient`, because "the portal changed
 * under us" is not a thing a reader can be expected to infer from a row count,
 * and it is the difference between pressing the button and filing a bug.
 */
const TRANSIENT_HINT =
  'This is the portal being written to while it was read — a live database doing its job, '
  + 'not a fault in the report or in what is cached here. Every month was already fetched '
  + 'several times over before this gave up, so trying again usually clears it.';

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
  el('starting').hidden = true;
  el('gate').hidden = true;
  el('report').hidden = false;
  progress('Reading allocations, credits and invoices…', 0, 1);

  // Six small endpoints, all at once: about a hundred rows between them, and
  // everything the header and the credit tiles need. `users/me/` is the odd one
  // out -- a single record, not a list, and it is here for the organisation the
  // token belongs to, which is the fallback when the portal tab's URL named
  // none.
  const [allocations, summary, customers, projects, invoices, me] = await Promise.all([
    state.client.list('openportal-allocations'),
    state.client.list('openportal-accounting-summary'),
    state.client.list('customers'),
    state.client.list('projects'),
    state.client.list('invoices'),
    state.client.record('users/me'),
  ]);
  Object.assign(state.raw, { allocations, summary, customers, projects, invoices, me });

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
    // A month is re-pulled when it comes back inconsistent, which is nearly
    // always the live month growing under the read. Said out loud because it is
    // the only reason a pull stalls on one month, and silence there reads as a
    // hang; the console line is what a bug report needs.
    onRetry: ({ year, month, attempt, of, detail }) => {
      console.warn(`Retrying ${year}-${String(month).padStart(2, '0')} (${attempt}/${of}):`,
        detail);
      progress(`Re-reading ${reports.monthKey(year, month)} — the portal changed it mid-read `
        + `(attempt ${attempt} of ${of})`);
    },
  });

  state.loading = false;
  progress(null);
  scheduleRender();
  await showCacheNote();

  // The tail: neither figure below blocks anything above it, and the page is
  // already complete and readable without them.
  loadExtras().catch((error) => showError('Could not load the supporting figures', error, {
    retry: () => loadExtras(),
    label: 'Load them again',
  }));
}

/**
 * The endpoints nothing on the critical path needs.
 *
 * `openportal-project-usage-reports` carries a fat per-day blob and backs one
 * figure; `openportal-project-storage-reports` carries another and backs the
 * two quota figures; `openportal-associations` is thousands of rows across the
 * whole machine and backs one denominator on one tile. All are worth having and
 * none is worth waiting for.
 *
 * Associations is pulled with a duplicate check, because measured against the
 * live deployment it is *also* not totally ordered: the row count matches every
 * time while a row or two comes back twice and as many never arrive, and the
 * number varies with `page_size`. Unlike the usage table there is nothing to
 * slice it by and no ordering parameter this deployment honours, so the repeat
 * is noted rather than raised: it moves one denominator on one tile by a row or
 * two, and losing the whole tile over that would be the worse trade. The tile
 * says so itself when it happens.
 */
async function loadExtras() {
  state.raw.usageReports = await state.client.list('openportal-project-usage-reports');
  scheduleRender();
  state.raw.storageReports = await state.client.list('openportal-project-storage-reports');
  scheduleRender();
  state.raw.associations = await state.client.list('openportal-associations', {
    rowKeys: ['uuid'],
    onRepeats: ({ repeats }) => {
      state.raw.associationRepeats = repeats;
    },
  });
  scheduleRender();
}

/**
 * The organisations this token can see, largest first.
 *
 * The portal is multi-tenant and one token often administers projects belonging
 * to several separately funded organisations; counting them together overstates
 * any one of them. The picker is therefore never removed -- but it should open
 * on the right one, and working out which is `defaultCustomer` below.
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
    state.options.customer = defaultCustomer(found.map((row) => row.name));
  }
  select.value = state.options.customer ?? '';
}

/**
 * Whose report this is, in the order the evidence is worth believing.
 *
 * This is what replaced an institution's name written into the source. Nothing
 * below names one, so an RSE at any institution opens their own dashboard,
 * presses the button, and gets their own figures -- which is the point, and is
 * also why none of these routes can be tested from a single organisation's
 * account.
 *
 * 1. **The organisation UUID from the portal tab's URL**, resolved through the
 *    `customers` rows already in hand. It is where the reader was looking, so
 *    it is what they meant.
 * 2. **The organisation that owns the project the tab was showing**, resolved
 *    through the allocations. A `/projects/<uuid>/` page states an
 *    organisation just as plainly as a dashboard does, only indirectly.
 * 3. **The organisation the tab's remembered filter named.** Where the reader
 *    looked once, rather than where they are looking now.
 * 4. **The organisation their token belongs to**, from the `customer`-scoped
 *    entry in `users/me/`'s permissions. Works from any portal page at all,
 *    including one with no UUID in it.
 * 5. **Whichever organisation holds the most projects** in scope. A guess, and
 *    the picker is right there.
 *
 * A name that resolves to no organisation *in scope* is dropped rather than
 * used: scoping to an organisation whose projects this token cannot see would
 * draw an empty report and blame the reader for it.
 */
function defaultCustomer(namesInScope) {
  const byUuid = state.customerUuid
    ? state.raw.customers.find((row) => row.uuid === state.customerUuid)?.name
    : null;
  // Through the allocations rather than `projects`, because those are what the
  // scope is derived from in the first place: a project not among them is one
  // this token cannot report on anyway.
  const byProject = state.projectUuid
    ? reports.inScope(state.raw.allocations)
      .find((row) => row.project_uuid === state.projectUuid)?.customer_name
    : null;
  const candidates = [
    byUuid,
    byProject,
    state.customerName,
    customerFromPermissions(state.raw.me),
  ];
  for (const candidate of candidates) {
    if (candidate && namesInScope.includes(candidate)) return candidate;
  }
  return namesInScope[0] ?? null;
}

async function showCacheNote() {
  // Null until an API URL is known: the cache is keyed by it, so there is
  // nothing to count yet and nothing to say.
  if (!state.cache) return;
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
/**
 * Whether the extension already holds permission for this API host.
 *
 * Separate from `grantHost` because *asking* has to happen inside a click and
 * `boot()` is not one: a `permissions.request` on page load is refused by
 * Chrome. So the automatic path can only check, and hand a host it does not
 * hold to the gate, where the reader's own click carries the gesture.
 *
 * True on anything that throws, for the same reason `grantHost` returns true:
 * outside an extension there is no permission to hold, and the fetch itself
 * gives the honest error.
 */
async function hasHost(apiUrl) {
  try {
    return await chrome.permissions.contains({ origins: [`${new URL(apiUrl).origin}/*`] });
  } catch {
    return true;
  }
}

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

/**
 * Ask the background worker for whatever it read off the portal tab.
 *
 * Null when there was no portal tab, when the page would not be scripted, or
 * when this is not running as an extension at all -- and all three mean the
 * same thing downstream: show the form.
 */
async function handover() {
  try {
    return await chrome.runtime.sendMessage({ type: 'context' });
  } catch {
    return null;
  }
}

/**
 * A fresh token from the portal tab, for `WaldurClient` to retry a 401 with.
 *
 * Only wired up when the token came from the portal in the first place. A
 * pasted token has no source to go back to, and silently replacing it with one
 * belonging to a different account would be worse than the error.
 */
async function renewFromPortal() {
  try {
    return await chrome.runtime.sendMessage({ type: 'refresh' });
  } catch {
    return null;
  }
}

/** Build the report from a token and an API URL, whatever their provenance. */
async function build({ token, apiUrl, fromPortal }) {
  state.apiUrl = apiUrl;
  state.tokenFromPortal = fromPortal;
  state.client = new WaldurClient({
    apiUrl,
    token,
    renew: fromPortal ? renewFromPortal : null,
  });
  state.cache = new MonthCache(apiUrl);

  el('starting').hidden = true;
  el('gate').hidden = true;
  el('run').disabled = true;
  el('gate-error').innerHTML = '';
  try {
    await run();
  } catch (error) {
    state.loading = false;
    progress(null);
    // `state.headRendered`, not `#report.hidden`: `run()` unhides the report
    // before its first request, so the shell is always visible by the time
    // anything can throw. What decides this is whether the reader has something
    // to read -- a failure in the first wave leaves an empty page whose only
    // useful control, the token box, is on the gate.
    if (!state.headRendered) {
      // The gate's own button is the retry here, so all it needs is the reason
      // to press it rather than to go looking for what went wrong.
      showGate(error.transient ? `${error.message} ${TRANSIENT_HINT}` : error.message);
    } else {
      showError(
        error instanceof WaldurError ? 'The portal pull did not complete' : 'Something broke',
        error,
        // Not `refresh: true`: the complete months already cached are not what
        // failed, and re-fetching a year of them to get at one bad month would
        // turn a button press into another minute of waiting.
        { retry: () => run() },
      );
    }
  } finally {
    el('run').disabled = false;
  }
}

/** The pasted-token path: the fallback, reached from the gate's own button. */
async function start() {
  const token = el('token').value.trim();
  const apiUrl = el('api-url').value.trim();

  if (!token) return gateError('Paste a portal API token to continue.');
  // Blank when the portal tab offered a token but no URL to send it to, which
  // is the one case this box is not prefilled for.
  if (!apiUrl) return gateError('Give the API URL of the portal this token belongs to.');

  // The manifest grants the Isambard portal outright; any other deployment has
  // to be asked for, and asking has to happen inside the click that triggered
  // it -- so this comes before the first await in the function.
  if (!(await grantHost(apiUrl))) {
    return gateError(
      `Reading ${apiUrl} needs permission for that host, and it was not granted.`,
    );
  }

  if (el('remember').checked) await session.set(token);
  else await session.clear();

  await build({ token, apiUrl, fromPortal: false });
  return undefined;
}

/**
 * Fall back to the form, saying what failed rather than just appearing.
 *
 * A form the reader did not expect is a form they have to guess the reason for,
 * and every reason here is knowable: no portal tab open, a token the portal
 * would not accept, a page that could not be read. Say which.
 */
function showGate(why) {
  el('starting').hidden = true;
  // The inverse of what `run()` does on the way in. A half-built report left
  // behind the gate is a page with two headings and no reader who can tell
  // which one is current.
  el('report').hidden = true;
  el('gate').hidden = false;
  if (why) el('gate-why').textContent = why;
  if (state.apiUrl) el('api-url').value = state.apiUrl;
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

// Likewise free, and for the same reason: the machine's size and this
// organisation's share of it are denominators applied at render time, not
// filters applied at fetch time. Nothing here refetches anything.
el('nodes').addEventListener('change', () => {
  const nodes = Number(el('nodes').value);
  if (!Number.isFinite(nodes) || nodes <= 0) {
    el('nodes').value = String(state.options.nodes);
    return;
  }
  state.options.nodes = nodes;
  scheduleRender();
});

el('share').addEventListener('change', () => {
  const share = parseShare(el('share').value);
  if (share === null) {
    el('share').value = `${(state.options.share * 100).toFixed(0)}%`;
    return;
  }
  state.options.share = share;
  el('share').value = `${(share * 100).toFixed(share * 100 % 1 ? 1 : 0)}%`;
  scheduleRender();
});

el('refresh').addEventListener('click', async () => {
  clearError();
  try {
    await run({ refresh: true });
  } catch (error) {
    showError('Refresh failed', error, { retry: () => run({ refresh: true }) });
  }
});

el('forget').addEventListener('click', async () => {
  if (state.cache) await state.cache.clearAll();
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
const plotlyMissing = typeof Plotly === 'undefined';
if (plotlyMissing) {
  showGate('');
  el('gate-error').innerHTML =
    '<div class="error"><h2>plotly.js is missing</h2><p>The extension ships the ' +
    'bundle rather than fetching it from a CDN, and it has not been written yet. ' +
    'Run <code>pixi run web-vendor</code> in the repository, then reload this ' +
    'page.</p></div>';
  el('run').disabled = true;
}

/**
 * What happens when the tab opens: as little of it in front of the reader as
 * possible.
 *
 * The good path is silent. The background read the portal tab before this page
 * existed, so the token, the API URL and the organisation are already waiting;
 * the report starts and the reader sees figures. Everything below is the
 * arithmetic of *which* failure to explain when that does not happen.
 *
 * The remembered token is tried only when the portal tab offered none. It is
 * the older mechanism and the weaker one -- it can be an hour stale, where the
 * portal tab is by definition current -- so it is a fallback to a fallback,
 * and it exists for the reader who closed the portal but not the browser.
 */
async function boot() {
  const context = await handover();

  // **A token read off a portal tab goes only to an API URL inferred from that
  // same tab.** The box's default is this deployment's, and `portal.js` answers
  // null rather than guessing at a hostname it does not recognise -- so falling
  // back to the default here would take another deployment's credential and
  // send it to this one. The reader supplies the URL instead, and knows they
  // did.
  const apiUrl = context?.apiUrl ?? (context?.token ? '' : el('api-url').value.trim());

  state.customerUuid = context?.customerUuid ?? null;
  state.customerName = context?.customerName ?? null;
  state.projectUuid = context?.projectUuid ?? null;
  el('api-url').value = apiUrl;
  if (apiUrl) {
    state.cache = new MonthCache(apiUrl);
    await showCacheNote();
  }

  if (plotlyMissing) return;

  if (context?.token) {
    if (!apiUrl) {
      // The token is offered rather than the reader being sent back to the
      // account menu for it: it is theirs, off the tab they pressed the button
      // on, and only where to send it is in doubt.
      el('token').value = context.token;
      showGate(
        'The portal tab was read, but its API URL could not be worked out from it — ' +
          'the hostname follows no convention this knows. Give the API URL and press ' +
          'Build report.',
      );
      return;
    }
    if (!(await hasHost(apiUrl))) {
      // Another deployment, first visit. The manifest grants this one outright
      // and covers the rest with `optional_host_permissions`, which can only be
      // requested from a click -- so the gate is the route, and pressing its
      // button raises the prompt. Without this the reader would meet a blocked
      // cross-origin fetch and no explanation, on the very path the README
      // advertises for pointing this at another Waldur.
      el('token').value = context.token;
      showGate(
        `Reading ${apiUrl} needs permission for that host, which can only be asked ` +
          'for from a button. Press Build report to be asked.',
      );
      return;
    }
    await build({ token: context.token, apiUrl, fromPortal: true });
    return;
  }

  const stored = await session.get();
  if (stored) {
    el('token').value = stored;
    el('remember').checked = true;
    await build({ token: stored, apiUrl, fromPortal: false });
    return;
  }

  showGate(
    context
      ? 'The portal tab is open but holds no token — sign in to the portal, then press ' +
        'the toolbar button again. Or paste one here.'
      : 'Open the portal, sign in, and press the toolbar button from that tab: the ' +
        'extension then reads the token and your organisation without being asked. ' +
        'Failing that, paste a token here.',
  );
}

boot();
