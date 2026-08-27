/**
 * The report rules the golden fixture cannot reach.
 *
 * `parity.test.mjs` pins the JavaScript to the Python over one fixture, which
 * is the right way to catch drift in a formula -- but it only covers the
 * branches that fixture happens to take. Its two months are both invoiced, so
 * everything the code does when there is no invoice to read goes unchecked by
 * it. These are those branches, driven off the same fixture with the invoices
 * taken away.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import * as reports from '../src/reports.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(here, 'fixture.json'), 'utf-8'));
const expected = JSON.parse(readFileSync(join(here, 'expected.json'), 'utf-8'));

const { as_of: asOf, customer, nodes, share } = expected;

const scope = reports.inScope(fixture['openportal-allocations']);
const rows = reports.monthlyRows(
  fixture['openportal-allocation-user-usage'],
  scope,
  customer,
);

/** The same fixture with nothing invoiced: every month takes the usage route. */
const uninvoiced = reports.monthlyTotals(rows, [], {
  nodes, share, scope, customer, asOf,
});

describe('a month with no invoice behind it', () => {
  it('says which of the two measurements its node hours are', () => {
    assert.ok(uninvoiced.length > 0);
    for (const row of uninvoiced) {
      assert.equal(row.node_hours_source, 'usage');
      assert.equal(row.node_hours, row.usage_node_hours);
    }
  });

  it('counts its active projects on the side its node hours came from', () => {
    // The ledger knows of no project at all here. Taking the count from it
    // would report no projects for a month that plainly ran something.
    for (const row of uninvoiced) {
      const ran = new Set(
        rows.filter((r) => r.month === row.month && r.node_usage > 0)
          .map((r) => r.project_code),
      );
      assert.equal(row.active_projects, ran.size);
      assert.ok(row.active_projects > 0);
    }
  });
});

/**
 * The same invoices with the largest usage line no longer looking like one.
 *
 * The largest, because the check has an absolute floor under it -- losing a
 * line worth less than that is deliberately not a finding.
 */
function withALostLine(invoices) {
  const copy = structuredClone(invoices);
  const lines = copy
    .filter((inv) => inv.customer_details?.name === customer)
    .flatMap((inv) => inv.items ?? [])
    .filter((item) => item.measured_unit === 'hours');
  if (!lines.length) throw new Error('the fixture has no usage line to lose');
  const biggest = lines.reduce((a, b) => (Number(b.quantity) > Number(a.quantity) ? b : a));
  biggest.measured_unit = 'GB'; // storage, as far as the filter can tell
  return copy;
}

describe('an invoice its own lines no longer add up to', () => {
  it('is flagged rather than reconciled off its header', () => {
    const checked = reports.reconcile(rows, withALostLine(fixture.invoices), {
      customer, scope, asOf,
    });
    const lost = checked.filter((row) => row.status === 'split incomplete');
    assert.ok(lost.length > 0);
    for (const row of lost) {
      assert.notEqual(row.items_difference, 0);
      // The split is what `monthlyTotals` reports, so this has to raise a flag.
      assert.ok(!reports.RECONCILED.has(row.status));
    }
  });

  it('reconciles as before when every line is intact', () => {
    const checked = reports.reconcile(rows, fixture.invoices, { customer, scope, asOf });
    for (const row of checked) {
      assert.notEqual(row.status, 'split incomplete');
      assert.equal(row.items_difference, 0);
    }
  });
});

describe('a month whose invoice lines no longer add up', () => {
  it('is not read off that invoice as if the missing lines were zeros', () => {
    const invoices = withALostLine(fixture.invoices);
    const before = new Map(
      reports.monthlyTotals(rows, fixture.invoices, { nodes, share, scope, customer, asOf })
        .map((row) => [row.month, row]),
    );
    const after = reports.monthlyTotals(rows, invoices, {
      nodes, share, scope, customer, asOf,
    });
    const lost = reports.reconcile(rows, invoices, { customer, scope, asOf })
      .filter((row) => row.status === 'split incomplete')
      .map((row) => row.month);
    assert.ok(lost.length > 0);
    for (const row of after) {
      if (!lost.includes(row.month)) {
        // Untouched months must keep reading off the ledger exactly as before.
        assert.equal(row.node_hours_source, before.get(row.month).node_hours_source);
        continue;
      }
      assert.equal(row.node_hours_source, 'usage');
      assert.equal(row.node_hours, row.usage_node_hours);
    }
  });
});
