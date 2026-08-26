/**
 * The analyses behind the report, ported from `waldur_tools.reports`.
 *
 * Pure functions over arrays of API records: no DOM, no fetch, no plotly, so
 * node can import this file and check it against the Python implementation.
 * That check is the point -- see `web/tests/parity.test.mjs`. Every formula
 * here has a twin in `src/waldur_tools/reports.py`, the caveats attached to
 * each one are documented there and in DEVELOPER.md, and the two are allowed to
 * differ only in language.
 *
 * **Months are `YYYY-MM` strings throughout.** They sort, compare and key a Map
 * correctly as strings, which a Date does none of, and they survive a round
 * trip through IndexedDB and JSON unchanged.
 */

/** Compute nodes in Isambard 3 phase 1. */
export const TOTAL_NODES = 384;

/** The GW4 partner share of that machine held by this organisation. */
export const DEFAULT_SHARE = 0.1;

/**
 * There is deliberately no default organisation.
 *
 * The Python tool has one, because it is run from a checkout by the person who
 * configured it. The browser version is opened from a portal tab, and which
 * organisation that tab is showing is the answer -- an RSE at any institution
 * should get their own figures without anyone editing this file. So `customer`
 * is a required argument in spirit: passing `null` widens the scope to every
 * project the token administers, which is a *choice* the picker offers, not a
 * fallback for having failed to work out whose report this is.
 */

/** How far `reconcile` lets usage drift from the invoice, as a fraction. */
export const RECONCILE_TOLERANCE = 0.01;

/** The absolute floor under that tolerance, in node hours. */
export const RECONCILE_FLOOR = 2.0;

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

const MONTH_NAMES_LONG = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

// --------------------------------------------------------------------------
// Scalars
// --------------------------------------------------------------------------

/**
 * A decimal string, or a number, or a null, as a number or null.
 *
 * The twin of `frames.numeric`: Waldur serialises money and usage as decimal
 * *strings*, and arithmetic on those silently concatenates.
 */
export function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** The twin of `frames.integral`. */
export function int(value) {
  const parsed = num(value);
  return parsed === null ? null : Math.trunc(parsed);
}

/**
 * The SLURM project code inside a Waldur `groupname` or `username`.
 *
 * Isambard's SLURM names are structured: `brics.<code>` for a group,
 * `<unix user>.<code>[.<cluster>]` for a user. The code is the stable key that
 * ties a person to a project, and unlike the allocation URL it survives a
 * project holding several allocations.
 *
 * Null when there is no second segment, matching polars' `null_on_oob`.
 */
export function projectCode(name) {
  if (name === null || name === undefined) return null;
  const parts = String(name).split('.');
  return parts.length > 1 ? parts[1] : null;
}

/**
 * Everything before the first dot.
 *
 * Two different things wear this shape: a `username` is `<unix user>.<code>`,
 * and a usage report's `project_identifier` is `<code>.brics`. Both want the
 * head of the name, so they share one helper rather than two identical ones.
 */
export function firstSegment(name) {
  if (name === null || name === undefined) return null;
  return String(name).split('.')[0];
}

/** The unix user in front of the project code. */
export const unixUsername = firstSegment;

/** `YYYY-MM` from a year and a month number. */
export function monthKey(year, month) {
  return `${year}-${String(month).padStart(2, '0')}`;
}

/** `Mmm YYYY`, the axis label. */
export function monthLabel(key) {
  const [year, month] = key.split('-');
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

/** `Month YYYY`, for prose and tile headings. */
export function monthLabelLong(key) {
  const [year, month] = key.split('-');
  return `${MONTH_NAMES_LONG[Number(month) - 1]} ${year}`;
}

/** Days in the calendar month, which is what the entitlement is worth. */
export function daysInMonth(key) {
  const [year, month] = key.split('-').map(Number);
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * A `Date` as `YYYY-MM-DD` in the reader's own timezone.
 *
 * Local rather than UTC on purpose: the only thing this date decides is which
 * month is still in progress, and on the first of the month a UTC conversion
 * would answer that question with the wrong month for half the world.
 * Everything downstream takes a string, so no `Date` ever reaches a comparison.
 */
export function isoDate(when = new Date()) {
  const year = when.getFullYear();
  const month = String(when.getMonth() + 1).padStart(2, '0');
  const day = String(when.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/** The `YYYY-MM` a `YYYY-MM-DD...` timestamp falls in. */
export function monthOf(stamp) {
  if (!stamp) return null;
  const text = String(stamp);
  return text.length >= 7 ? text.slice(0, 7) : null;
}

/**
 * Node hours our share of the machine is worth in one calendar month.
 *
 * `nodes * share` nodes held for every hour of it. An accounting entitlement
 * rather than a cap: nothing on Isambard 3 enforces the share, so a month above
 * 100% is possible and unremarkable. See `reports._entitlement` for why.
 */
export function entitlement(key, nodes, share) {
  return nodes * share * 24 * daysInMonth(key);
}

// --------------------------------------------------------------------------
// Small aggregation helpers
//
// Written out rather than pulled from a dataframe library: there are five
// distinct shapes below and all of them are a group-by with a sum or a distinct
// count, which is not worth a dependency the extension would have to ship.
// --------------------------------------------------------------------------

function groupBy(rows, key) {
  const groups = new Map();
  for (const row of rows) {
    const value = key(row);
    let bucket = groups.get(value);
    if (bucket === undefined) {
      bucket = [];
      groups.set(value, bucket);
    }
    bucket.push(row);
  }
  return groups;
}

function sumOf(rows, field) {
  let total = 0;
  for (const row of rows) total += row[field] ?? 0;
  return total;
}

function distinct(rows, field, predicate = () => true) {
  const seen = new Set();
  for (const row of rows) {
    if (predicate(row)) seen.add(row[field]);
  }
  return seen.size;
}

// --------------------------------------------------------------------------
// Scope
// --------------------------------------------------------------------------

/**
 * The projects this token administers, one entry per SLURM project code.
 *
 * The portal is multi-tenant and inconsistent about it: allocations come back
 * filtered to your own organisation while associations and usage return the
 * whole machine, with the rows you may not read blanked rather than omitted.
 * There is no documented "mine" filter, so the visible allocations *are* the
 * answer and the scope is derived from them.
 *
 * Deduplicated on the code, because every project holds an allocation per
 * service -- Isambard 3 and Isambard 3 MACS both appear, sharing a `groupname`.
 */
export function inScope(allocations) {
  const projects = new Map();
  for (const row of allocations) {
    const code = projectCode(row.groupname);
    if (code === null || projects.has(code)) continue;
    projects.set(code, {
      project_code: code,
      project_name: row.project_name,
      customer_name: row.customer_name,
      project_uuid: row.project_uuid,
    });
  }
  return [...projects.values()];
}

/**
 * The project codes one organisation holds, out of everything the token sees.
 *
 * Taken from the allocations rather than from the usage, which is the whole
 * point: a project that has never run a job has no usage row, and a project
 * with no compute is exactly the one whose disk fills up with nobody watching.
 * `null` means every project the token administers, across organisations.
 *
 * The Python side does this inside `reports.storage_samples(customer=...)`;
 * here the caller passes codes in, so the filter lives at the call site.
 */
export function scopedCodes(scope, customer = null) {
  return scope
    .filter((project) => customer === null || project.customer_name === customer)
    .map((project) => project.project_code);
}

/** The customers visible in the allocations, with how many projects each has. */
export function customersInScope(allocations) {
  const counts = new Map();
  for (const project of inScope(allocations)) {
    const name = project.customer_name;
    if (!name) continue;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, projects]) => ({ name, projects }))
    .sort((a, b) => b.projects - a.projects || a.name.localeCompare(b.name));
}

// --------------------------------------------------------------------------
// The usage series
//
// `openportal-allocation-user-usage` is the only endpoint with a time axis --
// one row per user, allocation and calendar month -- so every figure is built
// from it. Its `node_usage` *is* safe to sum, unlike the identically named
// field on `openportal-allocations`, which holds only the current month.
// --------------------------------------------------------------------------

/**
 * Per user, project and month node usage for the projects we count as ours.
 *
 * The shared base of `monthlyTotals` and `monthly`. Both need these rows and
 * aggregate them differently: distinct users per month cannot be recovered by
 * summing a per-project user count, so neither report derives from the other.
 *
 * `customer` of `null` widens to every project the token administers, which is
 * as wide as this can honestly go -- usage does arrive for other organisations,
 * but their codes resolve to no name, no customer and no limit, so there is
 * nothing to attribute it to.
 */
export function monthlyRows(usage, scope, customer = null) {
  const projects = new Map();
  for (const project of scope) {
    if (customer !== null && project.customer_name !== customer) continue;
    projects.set(project.project_code, project);
  }

  const rows = [];
  for (const record of usage) {
    const code = projectCode(record.username);
    const project = code === null ? undefined : projects.get(code);
    // An inner join: a usage row for a project we cannot name is a row we
    // cannot attribute, and counting it would inflate our own share.
    if (project === undefined) continue;
    const year = int(record.year);
    const month = int(record.month);
    if (year === null || month === null) continue;
    rows.push({
      month: monthKey(year, month),
      project_code: code,
      project_name: project.project_name,
      customer_name: project.customer_name,
      unix_username: unixUsername(record.username),
      node_usage: num(record.node_usage) ?? 0,
    });
  }
  return rows;
}

/**
 * One row per month: how much of our share of the machine we actually used.
 *
 * The headline series. `pct_of_entitlement` is the number the whole report is
 * built around -- 100% means we ran, on average across the month, exactly the
 * `nodes * share` nodes our share is worth.
 *
 * `active_projects` and `active_users` count only non-zero usage, so they read
 * as "who actually ran something" rather than "who could have". `is_partial`
 * marks the month `asOf` falls in, which is incomplete by construction and must
 * be kept out of any average.
 */
export function monthlyTotals(rows, { nodes = TOTAL_NODES, share = DEFAULT_SHARE, asOf } = {}) {
  const partial = monthOf(asOf);
  const out = [];
  for (const [month, bucket] of groupBy(rows, (row) => row.month)) {
    const nodeHours = sumOf(bucket, 'node_usage');
    const worth = entitlement(month, nodes, share);
    out.push({
      month,
      node_hours: nodeHours,
      active_projects: distinct(bucket, 'project_code', (row) => row.node_usage > 0),
      active_users: distinct(bucket, 'unix_username', (row) => row.node_usage > 0),
      projects_with_usage_rows: distinct(bucket, 'project_code'),
      entitlement_node_hours: worth,
      pct_of_entitlement: (100 * nodeHours) / worth,
      mean_nodes: nodeHours / (worth / (nodes * share)),
      unused_node_hours: worth - nodeHours,
      is_partial: month === partial,
    });
  }
  return out.sort((a, b) => a.month.localeCompare(b.month));
}

/**
 * Node hours per project per calendar month.
 *
 * `entitlement_node_hours` is the whole organisation's monthly share, so
 * `pct_of_entitlement` measures one project against all of it and answers "how
 * much of our slice did this project alone account for?". It is not a
 * per-project quota; nothing in the portal allocates the share out to projects.
 */
export function monthly(rows, { nodes = TOTAL_NODES, share = DEFAULT_SHARE } = {}) {
  const out = [];
  for (const [, bucket] of groupBy(rows, (row) => `${row.month}\u0000${row.project_code}`)) {
    const first = bucket[0];
    const nodeHours = sumOf(bucket, 'node_usage');
    const worth = entitlement(first.month, nodes, share);
    out.push({
      month: first.month,
      project_code: first.project_code,
      project_name: first.project_name,
      customer_name: first.customer_name,
      node_hours: nodeHours,
      active_users: distinct(bucket, 'unix_username', (row) => row.node_usage > 0),
      entitlement_node_hours: worth,
      pct_of_entitlement: (100 * nodeHours) / worth,
      mean_nodes: nodeHours / (worth / (nodes * share)),
    });
  }
  return out.sort(
    (a, b) => a.month.localeCompare(b.month) || b.node_hours - a.node_hours,
  );
}

// --------------------------------------------------------------------------
// Awards
// --------------------------------------------------------------------------

/**
 * What each project was awarded, over what span, and its monthly average.
 *
 * `mean_monthly_allocation` is `total_credits / award_months`, the denominator
 * behind the heatmap's relative view: a project's usage means nothing beside
 * the whole organisation's share, because no project was given the whole
 * organisation's share. It means something beside its own award.
 *
 * **Four things it is not**, all four spelled out in `reports.allocations`:
 * it is not a monthly cap; it back-dates top-ups, so a topped-up project's
 * early months read quieter than they were; it ignores when the project
 * actually started running; and it is undefined rather than zero when a project
 * holds no credits.
 *
 * The join is on `project_uuid` and not the name, because names are not unique:
 * an estate can carry two accounting rows sharing a name under different UUIDs
 * where only one holds the credits.
 */
export function allocationsReport(scope, summary, { asOf, customer = null } = {}) {
  const awarded = new Map();
  for (const row of summary) {
    if (row.project_uuid) awarded.set(row.project_uuid, row);
  }
  const horizon = String(asOf).slice(0, 10);

  const out = [];
  for (const project of scope) {
    if (customer !== null && project.customer_name !== customer) continue;
    const award = awarded.get(project.project_uuid);
    const start = award?.start_date ? String(award.start_date).slice(0, 10) : null;
    // Null rather than unbounded for the open-ended internal projects: they are
    // measured to the snapshot date, so the span is "so far".
    const end = award?.end_date ? String(award.end_date).slice(0, 10) : null;
    const finish = end ?? horizon;
    const credits = award ? num(award.total_credits) : null;

    let months = null;
    if (start) {
      const [startYear, startMonth] = start.split('-').map(Number);
      const [endYear, endMonth] = finish.split('-').map(Number);
      months = Math.max(
        (endYear - startYear) * 12 + (endMonth - startMonth) + 1,
        1,
      );
    }
    out.push({
      project_code: project.project_code,
      project_name: project.project_name,
      customer_name: project.customer_name,
      start_date: start,
      end_date: end,
      total_credits: credits,
      award_months: months,
      // Zero credits gives no rate at all, not a rate of zero -- the internal
      // and workshop projects hold none and should drop out of the relative
      // view rather than read as infinitely over budget.
      mean_monthly_allocation: credits && months ? credits / months : null,
    });
  }
  return out.sort(
    (a, b) => (b.mean_monthly_allocation ?? -Infinity) - (a.mean_monthly_allocation ?? -Infinity),
  );
}

/**
 * The awarded rate in force in each month: node hours a month, promised.
 *
 * A project counts towards a month only while its award covers it, which is the
 * whole reason this is not one number. Summing every project's
 * `mean_monthly_allocation` regardless of dates treats awards that never
 * overlapped as concurrent, and inflates the total the moment one window
 * closes. The two agree until the first award expires, and that agreement is a
 * coincidence rather than a licence to take the shortcut.
 */
export function committed(allocation, months, asOf) {
  const horizon = monthOf(asOf);
  return months.map((month) => {
    let total = 0;
    for (const award of allocation) {
      const rate = award.mean_monthly_allocation;
      if (rate === null || !award.start_date) continue;
      const from = monthOf(award.start_date);
      const to = monthOf(award.end_date) ?? horizon;
      if (from <= month && month <= to) total += rate;
    }
    return total;
  });
}

// --------------------------------------------------------------------------
// Supporting series
// --------------------------------------------------------------------------

/**
 * How many of our projects had been set up by each month, from `created`.
 *
 * The denominator behind "how many of the projects that existed ran
 * something": without it the active-project count reads as a plateau rather
 * than as a fraction.
 */
export function projectsExisting(projects, months, customer = null) {
  const created = projects
    .filter((row) => customer === null || row.customer_name === customer)
    .map((row) => (row.created ? String(row.created).slice(0, 10) : null))
    .filter((day) => day !== null);
  return months.map((month) => ({
    month,
    // Counted against the first of the month, as the Python does: a project
    // created on the 5th does not count towards the month it was created in.
    projects: created.filter((day) => day <= `${month}-01`).length,
  }));
}

/**
 * `[credit held, credit never allocated to a project]`, in node hours.
 *
 * Straight from `customers`. The only organisation-level quantities the portal
 * carries, and they change the whole reading of the report: an organisation can
 * hold a large unallocated balance while its utilisation looks poor, because
 * unallocated credit reaches no project and a project is the only thing that
 * can spend it. Both are measured net of spend.
 */
export function creditPosition(customers, customer = null) {
  let held = 0;
  let spare = 0;
  for (const row of customers) {
    if (customer !== null && row.name !== customer) continue;
    held += num(row.customer_credit) ?? 0;
    spare += num(row.customer_unallocated_credit) ?? 0;
  }
  return [held, spare];
}

/** The name inside `customer_details`, which arrives as an object or as JSON. */
function customerOfInvoice(record) {
  const details = record.customer_details;
  if (!details) return null;
  if (typeof details === 'string') {
    try {
      return JSON.parse(details).name ?? null;
    } catch {
      return null;
    }
  }
  return details.name ?? null;
}

/**
 * The node hours the portal billed, one row per calendar month.
 *
 * `incurred_costs` is a running total in credits, and on this deployment a
 * credit *is* a node hour: every usage line carries `unit_price` exactly
 * 1.0000000000 and `measured_unit` hours. So it is a second, independent
 * measurement of the same node hours the usage endpoint sums to -- which is
 * what makes `reconcile` possible at all.
 *
 * Not `price` or `total`: those are net of the credit lines the portal writes
 * to zero a grant-funded invoice out, so an invoice can bill thousands of node
 * hours and show a total near zero.
 */
export function invoiced(invoices, customer = null) {
  const kept = invoices.filter(
    (record) => customer === null || customerOfInvoice(record) === customer,
  );
  const out = [];
  for (const [month, bucket] of groupBy(kept, (record) => {
    const year = int(record.year);
    const month_ = int(record.month);
    return year === null || month_ === null ? null : monthKey(year, month_);
  })) {
    if (month === null) continue;
    out.push({
      month,
      incurred_costs: bucket.reduce((total, row) => total + (num(row.incurred_costs) ?? 0), 0),
      invoice_state: [...new Set(bucket.map((row) => row.state))].sort().join(', '),
    });
  }
  return out.sort((a, b) => a.month.localeCompare(b.month));
}

/**
 * Summed usage against the invoice, month by month: does the pull add up?
 *
 * Two routes to one number, from different endpoints aggregated by different
 * sides of the portal, so agreement is evidence and disagreement is a defect.
 * **This is the check that catches an unstable pull** before an inflated
 * headline can be read as a finding -- which is why the browser report shows it
 * rather than leaving it to a separate command nobody runs.
 *
 * `difference` is positive when we counted usage nobody billed, which is the
 * shape a duplicated page takes.
 */
export function reconcile(rows, invoices, {
  customer = null,
  asOf,
  tolerance = RECONCILE_TOLERANCE,
} = {}) {
  const usage = new Map();
  for (const [month, bucket] of groupBy(rows, (row) => row.month)) {
    usage.set(month, sumOf(bucket, 'node_usage'));
  }
  const billed = new Map(invoiced(invoices, customer).map((row) => [row.month, row]));
  const partial = monthOf(asOf);

  const months = [...new Set([...usage.keys(), ...billed.keys()])].sort();
  return months.map((month) => {
    // A month absent from one side is not a zero on it: the join keeps the null
    // and `status` decides what a missing side means, while the comparison
    // itself reads it as zero so a month nobody used and nobody billed
    // reconciles instead of raising a flag.
    const nodeHours = usage.has(month) ? usage.get(month) : null;
    const invoice = billed.get(month);
    const costs = invoice ? invoice.incurred_costs : null;
    const gap = (nodeHours ?? 0) - (costs ?? 0);
    const allowed = Math.max(RECONCILE_FLOOR, tolerance * Math.abs(costs ?? 0));
    let status;
    if (Math.abs(gap) <= allowed) status = 'ok';
    else if (costs === null) status = 'no invoice';
    else if (nodeHours === null) status = 'no usage';
    else status = gap > 0 ? 'usage high' : 'usage low';
    return {
      month,
      node_hours: nodeHours,
      incurred_costs: costs,
      difference: nodeHours === null || costs === null ? null : nodeHours - costs,
      pct_difference:
        nodeHours === null || !costs ? null : (100 * (nodeHours - costs)) / costs,
      status,
      invoice_state: invoice ? invoice.invoice_state : null,
      is_partial: month === partial,
    };
  });
}

/**
 * The daily queue report, rolled up to one row per month for our projects.
 *
 * `openportal-project-usage-reports` returns one row per project, resource and
 * month with a free-form `report` blob nesting a dictionary per day. This is
 * the only analysis that reshapes rather than selects.
 *
 * `mean_wait_hours` is total wait over total jobs for the month, not the mean
 * of the daily means -- a day with three jobs should not weigh as much as a day
 * with three thousand.
 */
export function queueMonthly(usageReports, codes) {
  const wanted = new Set(codes);
  const perMonth = new Map();

  for (const record of usageReports) {
    const code = firstSegment(record.project_identifier);
    if (!wanted.has(code)) continue;
    // An object live from the API, a JSON string out of a parquet snapshot.
    let payload = record.report;
    if (typeof payload === 'string') {
      try {
        payload = JSON.parse(payload);
      } catch {
        continue;
      }
    }
    for (const [day, daily] of Object.entries(payload?.reports ?? {})) {
      const month = monthOf(day);
      if (month === null) continue;
      let bucket = perMonth.get(month);
      if (bucket === undefined) {
        bucket = { month, num_jobs: 0, total_wait_seconds: 0, busiest_day_users: 0 };
        perMonth.set(month, bucket);
      }
      bucket.num_jobs += daily.num_jobs ?? 0;
      bucket.total_wait_seconds += daily.total_wait_seconds ?? 0;
      bucket.busiest_day_users = Math.max(
        bucket.busiest_day_users,
        Object.keys(daily.user_job_counts ?? {}).length,
      );
    }
  }

  return [...perMonth.values()]
    .map((row) => ({
      ...row,
      mean_wait_hours: row.num_jobs ? row.total_wait_seconds / row.num_jobs / 3600 : null,
    }))
    .sort((a, b) => a.month.localeCompare(b.month));
}

/**
 * How many distinct people hold an association on one of our projects.
 *
 * The denominator for "people who ran something": access granted but never
 * exercised is the cheapest utilisation to recover, and it is invisible in the
 * usage endpoint, which only knows about people who ran.
 *
 * A person's access is per project, but the raw endpoint carries one row per
 * user *per service*, so this counts distinct people rather than rows.
 */
export function peopleWithAccess(associations, codes) {
  const wanted = new Set(codes);
  const people = new Set();
  for (const row of associations) {
    const code = projectCode(row.groupname);
    if (code === null || !wanted.has(code)) continue;
    const person = unixUsername(row.username);
    if (person) people.add(person);
  }
  return people.size;
}

// --------------------------------------------------------------------------
// Presentation shaping
//
// Not analyses -- these decide what a figure can legibly draw, and they live
// here so the parity test covers them too.
// --------------------------------------------------------------------------

/**
 * Collapse all but the `keep` largest projects into one "Other projects" band.
 *
 * Past seven series, hue can no longer tell them apart, and generating more
 * hues is the one thing a categorical palette must never do. The tail is not
 * lost: it is the neutral band, a row in the table view, and its own bar in the
 * totals figure.
 */
export function rankedBands(perProject, keep = 7) {
  const totals = new Map();
  for (const row of perProject) {
    totals.set(row.project_name, (totals.get(row.project_name) ?? 0) + row.node_hours);
  }
  const ordered = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  const top = new Set(ordered.slice(0, keep).map(([name]) => name));
  return perProject.map((row) => ({
    ...row,
    band: top.has(row.project_name) ? row.project_name : 'Other projects',
  }));
}

/** Lifetime node hours per project, smallest first. */
export function totalsByProject(perProject) {
  const totals = new Map();
  for (const row of perProject) {
    totals.set(row.project_name, (totals.get(row.project_name) ?? 0) + row.node_hours);
  }
  return [...totals.entries()]
    .map(([project_name, total]) => ({ project_name, total }))
    .sort((a, b) => a.total - b.total);
}

/** Months with usage and the lifetime total, for the stacked figure's table. */
export function projectSummary(perProject) {
  const rolled = new Map();
  for (const row of perProject) {
    let bucket = rolled.get(row.project_name);
    if (bucket === undefined) {
      bucket = { project_name: row.project_name, node_hours: 0, months_with_usage: 0 };
      rolled.set(row.project_name, bucket);
    }
    bucket.node_hours += row.node_hours;
    if (row.node_hours > 0) bucket.months_with_usage += 1;
  }
  return [...rolled.values()].sort((a, b) => b.node_hours - a.node_hours);
}

// -- storage quotas ---------------------------------------------------------

/**
 * Binary multipliers for the sizes the storage collector writes.
 *
 * Its "GB" is a GiB: it reports `"100.00 GB"` for the same home quota
 * `lfs quota -h` calls `100G`, and a 1000-based reading of that figure would be
 * 93.13 GiB. Only the absolute views depend on the choice -- a fill percentage
 * divides two figures carrying the same unit, so it is base-independent.
 */
const SIZE_UNITS = {
  B: 1,
  KB: 1024 ** 1,
  MB: 1024 ** 2,
  GB: 1024 ** 3,
  TB: 1024 ** 4,
  PB: 1024 ** 5,
};

/**
 * The same units, smallest first, for writing a size back out. Taken from the
 * table above rather than repeated, so parsing and rendering cannot drift.
 */
const BYTE_UNITS = Object.keys(SIZE_UNITS);

const SIZE = /^\s*([0-9]*\.?[0-9]+)\s*([KMGTP]?B)\s*$/i;

/** The filesystems charged to a project rather than to a person. */
export const PROJECT_FILESYSTEMS = ['projects'];

/**
 * `"1.50 TB"` as a number of bytes, or `null` if it is not a size.
 *
 * Null rather than throwing: this parses a free-form field inside a free-form
 * blob, and one unrecognised string should blank one cell rather than take down
 * the whole report.
 */
export function sizeBytes(text) {
  if (typeof text !== 'string') return null;
  const match = SIZE.exec(text);
  if (match === null) return null;
  return Number(match[1]) * SIZE_UNITS[match[2].toUpperCase()];
}

/** A byte count written the way the collector wrote it: `"46.79 GB"`. */
export function humaniseBytes(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  let size = value;
  // Every unit but the last: the loop divides its way down to petabytes and
  // then stops, since there is nothing above them to scale into.
  for (const unit of BYTE_UNITS.slice(0, -1)) {
    if (Math.abs(size) < 1024) {
      return unit === 'B' ? `${sizeText(size, 0)} B` : `${sizeText(size, 2)} ${unit}`;
    }
    size /= 1024;
  }
  return `${sizeText(size, 2)} ${BYTE_UNITS[BYTE_UNITS.length - 1]}`;
}

function sizeText(value, digits) {
  return value.toLocaleString('en-GB', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * Every quota reading in the pull, one row per scope, filesystem and sample.
 *
 * `openportal-project-storage-reports` returns one row per project, resource
 * and month, with a `report` blob holding quota readings. Like `queueMonthly`
 * this reshapes rather than selects, and it is what both storage figures are
 * built from.
 *
 * **Two shapes share the blob.** A finished month carries `daily_reports`, a
 * dictionary keyed by date; the month still in progress carries no such
 * dictionary at all, only the top-level snapshot. Both are read here, which
 * matters twice over: the top-level snapshot of a finished month is taken
 * *after* its last daily entry, so reading it recovers a final day the daily
 * dictionary alone would lose, and it is the only reading the open month has.
 *
 * **A day can be sampled more than once.** The same filesystem is reported
 * under each `resource` the project holds, by collectors running minutes apart,
 * so two readings of one day legitimately disagree. They are kept as separate
 * samples rather than deduplicated: storage is a property of the filesystem and
 * not of the cluster, so these are repeat measurements of one quantity, and
 * letting the monthly aggregation see all of them is what makes `peak` honest.
 */
export function storageSamples(storageReports, codes = null) {
  const wanted = codes === null ? null : new Set(codes);
  const rows = [];

  for (const record of storageReports ?? []) {
    // An object live from the API, a JSON string out of a parquet snapshot.
    let payload = record.report;
    if (typeof payload === 'string') {
      try {
        payload = JSON.parse(payload);
      } catch {
        continue;
      }
    }
    if (payload === null || typeof payload !== 'object') continue;

    const stamp = record.year && record.month ? monthKey(record.year, record.month) : null;
    const readings = [...Object.entries(payload.daily_reports ?? {}), [null, payload]];

    for (const [day, reading] of readings) {
      if (reading === null || typeof reading !== 'object') continue;
      const observed = reading.generated_at ?? '';
      const when = day ?? observed.slice(0, 10);
      // The row's own year and month are what the API says it describes; a
      // reading dated outside them would put a day in the wrong column.
      if (!when || (stamp !== null && when.slice(0, 7) !== stamp)) continue;

      const identifier = reading.project ?? record.project_identifier ?? '';
      const code = firstSegment(identifier);
      if (code === null || (wanted !== null && !wanted.has(code))) continue;

      const push = (kind, username, filesystem, quota) => {
        const usage = sizeBytes(quota === null ? null : quota?.usage);
        const limit = sizeBytes(quota === null ? null : quota?.limit);
        rows.push({
          observed_at: observed,
          date: when,
          month: when.slice(0, 7),
          kind,
          project_code: code,
          username,
          filesystem,
          usage_bytes: usage,
          limit_bytes: limit,
          fill_pct: limit && usage !== null ? (100 * usage) / limit : null,
        });
      };

      for (const [filesystem, quota] of Object.entries(reading.project_quotas ?? {})) {
        push('project', null, filesystem, quota);
      }
      for (const [who, quotas] of Object.entries(reading.user_quotas ?? {})) {
        for (const [filesystem, quota] of Object.entries(quotas ?? {})) {
          push('user', firstSegment(who), filesystem, quota);
        }
      }
    }
  }

  return rows.sort((a, b) => a.observed_at.localeCompare(b.observed_at));
}

/** The key identifying one quota across months. */
function storageKey(row) {
  return `${row.kind} ${row.project_code} ${row.username ?? ''} ${row.filesystem}`;
}

/**
 * How full every quota was when it was last read, fullest first.
 *
 * The current-state view: one row per project or person per filesystem,
 * carrying the most recent reading of it. `date` is part of the answer rather
 * than decoration -- these readings are only as current as the collector behind
 * them, which is not the same thing as how fresh the pull is.
 */
export function storageCurrent(samples) {
  const latest = new Map();
  // Samples arrive sorted by `observed_at`, so the last write per key wins.
  for (const row of samples) latest.set(storageKey(row), row);
  return [...latest.values()]
    .map((row) => ({
      kind: row.kind,
      project_code: row.project_code,
      username: row.username,
      filesystem: row.filesystem,
      usage_bytes: row.usage_bytes,
      limit_bytes: row.limit_bytes,
      fill_pct: row.fill_pct,
      date: row.date,
    }))
    .sort(
      (a, b) =>
        (b.fill_pct ?? -1) - (a.fill_pct ?? -1) ||
        a.kind.localeCompare(b.kind) ||
        a.project_code.localeCompare(b.project_code) ||
        (a.username ?? '').localeCompare(b.username ?? ''),
    );
}

/**
 * Each quota reduced to one row per month, for the storage heatmaps.
 *
 * Disk usage is a *level*, not a flow: unlike node hours there is nothing to
 * sum, and a month has to be summarised by choosing a statistic rather than by
 * adding one up. `peak` is the fullest it got -- the reading that decides
 * whether writes failed, and the default; `end` is the level carried into the
 * next month; `median` is the typical level, robust to one day's spike.
 *
 * A mean is deliberately absent. Averaging a slowly drifting level is close to
 * meaningless -- it is the mean of a random walk -- and it hides the peak,
 * which is the part that actually breaks jobs.
 *
 * `limit_bytes` is the quota **every** reading in the month agreed on, and null
 * when they did not -- a quota raised mid-month, or one reading whose limit was
 * not a size. Stricter than the last limit read, and it is what keeps the three
 * statistics honest. Each is chosen independently: the peak fill and the peak
 * size can be different readings, and the medians are interpolated between two.
 * While one limit holds all month that costs nothing, because `fill_pct` is
 * then a fixed multiple of `usage_bytes` and both the maximum and the median
 * carry straight through it. The moment the limit moves, that stops being true,
 * and nothing downstream may write the three as one reading; a null here is how
 * they are told not to.
 *
 * `is_partial` means *fewer daily readings than the month has days*, which is a
 * different claim from the `is_partial` of `monthlyTotals`: there it marks the
 * month the pull happened in. Storage readings lag their own collector, so
 * freshness here cannot be derived from the pull date.
 */
export function storageMonthly(samples) {
  const buckets = new Map();
  for (const row of samples) {
    const key = `${row.month} ${storageKey(row)}`;
    let bucket = buckets.get(key);
    if (bucket === undefined) {
      bucket = {
        month: row.month,
        kind: row.kind,
        project_code: row.project_code,
        username: row.username,
        filesystem: row.filesystem,
        fills: [],
        usages: [],
        limits: new Set(),
        days: new Set(),
        last: null,
        samples: 0,
      };
      buckets.set(key, bucket);
    }
    // Peak and median are over the readings that parsed; `end` is the last
    // sample whatever it held. Dropping an unreadable final reading from the
    // end view would report a stale level beside the newest limit, and polars'
    // `last()` on the other side of the parity tests keeps the null.
    if (row.fill_pct !== null) bucket.fills.push(row.fill_pct);
    if (row.usage_bytes !== null) bucket.usages.push(row.usage_bytes);
    bucket.limits.add(row.limit_bytes);
    bucket.days.add(row.date);
    bucket.last = row;
    bucket.samples += 1;
  }

  return [...buckets.values()]
    .map((bucket) => ({
      month: bucket.month,
      kind: bucket.kind,
      project_code: bucket.project_code,
      username: bucket.username,
      filesystem: bucket.filesystem,
      peak_fill_pct: bucket.fills.length ? Math.max(...bucket.fills) : null,
      end_fill_pct: bucket.last.fill_pct,
      median_fill_pct: median(bucket.fills),
      peak_bytes: bucket.usages.length ? Math.max(...bucket.usages) : null,
      end_bytes: bucket.last.usage_bytes,
      median_bytes: median(bucket.usages),
      // The limit the month held, not the last one read: null unless every
      // reading agreed on one. See `storageMonthly`'s doc comment -- it is what
      // makes the statistics above safe to write as a fraction of one quota.
      limit_bytes: bucket.limits.size === 1 ? [...bucket.limits][0] : null,
      days_observed: bucket.days.size,
      samples: bucket.samples,
      is_partial: bucket.days.size < daysInMonth(bucket.month),
    }))
    .sort(
      (a, b) =>
        a.month.localeCompare(b.month) ||
        a.kind.localeCompare(b.kind) ||
        a.project_code.localeCompare(b.project_code) ||
        (a.username ?? '').localeCompare(b.username ?? '') ||
        a.filesystem.localeCompare(b.filesystem),
    );
}

/** The middle value, matching polars: the mean of the two middles when even. */
function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
