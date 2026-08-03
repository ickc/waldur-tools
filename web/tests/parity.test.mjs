/**
 * The JavaScript half of the contract with `tests/test_web_parity.py`.
 *
 * Reads the same fixture the Python reports were run over, runs the browser
 * implementation on it, and asserts the two land on the same numbers. The
 * Python side is the definition; anything that disagrees here is a bug in
 * `web/src/reports.js`, not a difference of opinion.
 *
 * Run with `pixi run test-web`, or `node web/tests/parity.test.mjs`.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import * as reports from '../src/reports.js';

const here = dirname(fileURLToPath(import.meta.url));
const read = (name) => JSON.parse(readFileSync(join(here, name), 'utf-8'));

const fixture = read('fixture.json');
const expected = read('expected.json');

const { as_of: asOf, customer, nodes, share } = expected;

/** Floats that survived two languages and a JSON round trip are close, not equal. */
const TOLERANCE = 1e-9;

let failures = 0;
let checks = 0;

function near(actual, wanted, where) {
  if (typeof wanted === 'number' && typeof actual === 'number') {
    const scale = Math.max(1, Math.abs(wanted));
    return Math.abs(actual - wanted) <= TOLERANCE * scale
      ? true
      : fail(where, actual, wanted);
  }
  // `undefined` is how a missing key arrives; the Python writes an explicit
  // null, and conflating the two would let a dropped column pass.
  const left = actual === undefined ? null : actual;
  const right = wanted === undefined ? null : wanted;
  return left === right ? true : fail(where, left, right);
}

function fail(where, actual, wanted) {
  failures += 1;
  console.error(`  ✗ ${where}\n      python: ${JSON.stringify(wanted)}\n` +
    `      js:     ${JSON.stringify(actual)}`);
  return false;
}

/**
 * Compare two lists of records as multisets.
 *
 * Sorted by their own serialisation on both sides, so a group-by that happens
 * to emit rows in a different order is not a failure -- the ordering that *is*
 * part of the contract is asserted separately, at the bottom.
 */
function sameRows(name, actual, wanted, columns) {
  checks += 1;
  const shape = (rows) =>
    rows
      .map((row) => Object.fromEntries(columns.map((column) => [column, row[column] ?? null])))
      .map((row) => [JSON.stringify(row, columns.slice().sort()), row]);

  const left = shape(actual).sort((a, b) => a[0].localeCompare(b[0]));
  const right = shape(wanted).sort((a, b) => a[0].localeCompare(b[0]));

  if (left.length !== right.length) {
    fail(`${name}: row count`, left.length, right.length);
    return;
  }
  let ok = true;
  right.forEach(([, wantedRow], index) => {
    const [, actualRow] = left[index];
    for (const column of columns) {
      if (!near(actualRow[column], wantedRow[column], `${name}[${index}].${column}`)) ok = false;
    }
  });
  if (ok) console.log(`  ✓ ${name} (${right.length} rows)`);
}

function sameValue(name, actual, wanted) {
  checks += 1;
  if (near(actual, wanted, name)) console.log(`  ✓ ${name}`);
}

function sameList(name, actual, wanted) {
  checks += 1;
  if (actual.length !== wanted.length) {
    fail(`${name}: length`, actual.length, wanted.length);
    return;
  }
  let ok = true;
  wanted.forEach((value, index) => {
    if (!near(actual[index], value, `${name}[${index}]`)) ok = false;
  });
  if (ok) console.log(`  ✓ ${name} (${wanted.length} values)`);
}

// --------------------------------------------------------------------------

console.log(`parity against the Python reports (as of ${asOf}, customer ${customer})`);

const scope = reports.inScope(fixture['openportal-allocations']);
sameRows('inScope', scope, expected.in_scope, [
  'project_code', 'project_name', 'customer_name', 'project_uuid',
]);

const rows = reports.monthlyRows(
  fixture['openportal-allocation-user-usage'],
  scope,
  customer,
);

const totals = reports.monthlyTotals(rows, { nodes, share, asOf });
sameRows('monthlyTotals', totals, expected.monthly_totals, [
  'month', 'node_hours', 'active_projects', 'active_users', 'projects_with_usage_rows',
  'entitlement_node_hours', 'pct_of_entitlement', 'mean_nodes', 'unused_node_hours',
  'is_partial',
]);

const perProject = reports.monthly(rows, { nodes, share });
sameRows('monthly', perProject, expected.monthly, [
  'month', 'project_code', 'project_name', 'customer_name', 'node_hours', 'active_users',
  'entitlement_node_hours', 'pct_of_entitlement', 'mean_nodes',
]);

const allocation = reports.allocationsReport(
  scope,
  fixture['openportal-accounting-summary'],
  { asOf, customer },
);
sameRows('allocationsReport', allocation, expected.allocations, [
  'project_code', 'project_name', 'customer_name', 'start_date', 'end_date',
  'total_credits', 'award_months', 'mean_monthly_allocation',
]);

const months = totals.map((row) => row.month);
sameList('committed', reports.committed(allocation, months, asOf), expected.committed);

sameRows(
  'projectsExisting',
  reports.projectsExisting(fixture.projects, months, customer),
  expected.projects_existing,
  ['month', 'projects'],
);

sameList(
  'creditPosition',
  reports.creditPosition(fixture.customers, customer),
  expected.credit_position,
);

sameRows('invoiced', reports.invoiced(fixture.invoices, customer), expected.invoiced, [
  'month', 'incurred_costs', 'invoice_state',
]);

sameRows(
  'reconcile',
  reports.reconcile(rows, fixture.invoices, { customer, asOf }),
  expected.reconcile,
  ['month', 'node_hours', 'incurred_costs', 'difference', 'pct_difference', 'status',
    'invoice_state', 'is_partial'],
);

const codes = scope.map((row) => row.project_code);
sameRows(
  'queueMonthly',
  reports.queueMonthly(fixture['openportal-project-usage-reports'], codes),
  expected.queue_monthly,
  ['month', 'num_jobs', 'total_wait_seconds', 'busiest_day_users', 'mean_wait_hours'],
);

sameValue(
  'peopleWithAccess',
  reports.peopleWithAccess(fixture['openportal-associations'], codes),
  expected.people_with_access,
);

sameRows('rankedBands', reports.rankedBands(perProject), expected.ranked_bands, [
  'month', 'project_code', 'band',
]);

// -- the orderings that are part of the contract ---------------------------
//
// Compared as multisets above, so that a group-by emitting rows in a different
// order is not a failure. These three orderings are not incidental: a figure
// reads them straight off, and getting one backwards silently reverses an axis.

checks += 1;
assert.deepEqual(
  months,
  [...months].sort(),
  'monthlyTotals must come back in calendar order — the x axis is read off it',
);

checks += 1;
assert.ok(
  allocation
    .map((row) => row.mean_monthly_allocation)
    .filter((rate) => rate !== null)
    .every((rate, index, rates) => index === 0 || rates[index - 1] >= rate),
  'allocationsReport must be sorted by rate, largest first, with nulls last',
);

checks += 1;
assert.ok(
  reports
    .totalsByProject(perProject)
    .every((row, index, all) => index === 0 || all[index - 1].total <= row.total),
  'totalsByProject must be smallest first — the bar chart draws it bottom up',
);

console.log(`  ✓ orderings`);

// --------------------------------------------------------------------------

if (failures) {
  console.error(`\n${failures} mismatch(es) across ${checks} checks.`);
  console.error(
    'web/src/reports.js disagrees with waldur_tools.reports. The Python is the ' +
      'definition; fix the JavaScript.',
  );
  process.exit(1);
}
console.log(`\nall ${checks} checks agree with the Python implementation.`);
