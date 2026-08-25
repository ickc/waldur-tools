/**
 * The properties the figures must not lose, in the spirit of `test_viz.py`.
 *
 * These are the rules that are easy to undo in a refactor and invisible in a
 * diff: a second y-axis quietly appearing, an eighth hue being generated, a
 * colour that the theme switch cannot swap because it is not in the palette.
 * None of them throws; they just produce a figure that misleads.
 *
 * **This does not open a browser.** It builds the figure specifications and
 * checks them; whether plotly draws anything is a question only a browser can
 * answer, and the note at the end of DEVELOPER.md's testing section applies
 * here with full force.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import {
  figureEngagement, figureHeatmap, figureProjects, figureQueue, figureShare,
  figureStorageProjects, figureStorageUsers, figureTotalsByProject,
} from '../src/figures.js';
import { STALE_DAYS, reconcileTable, storageStaleness } from '../src/page.js';
import { CHROME, RAMP_FILL_LIGHT, RAMP_LIGHT, SERIES } from '../src/palette.js';
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
const totals = reports.monthlyTotals(rows, { nodes, share, asOf });
const perProject = reports.monthly(rows, { nodes, share });
const months = totals.map((row) => row.month);
const allocation = reports.allocationsReport(
  scope,
  fixture['openportal-accounting-summary'],
  { asOf, customer },
);
const awarded = reports.committed(allocation, months, asOf);

const every = () => [
  ['share', figureShare(totals, nodes, share, awarded)],
  ['projects', figureProjects(reports.rankedBands(perProject), totals)],
  ['heatmap', figureHeatmap(perProject, totals, allocation)],
  ['totals', figureTotalsByProject(reports.totalsByProject(perProject))],
  ['engagement', figureEngagement(totals, reports.projectsExisting(
    fixture.projects, months, customer,
  ))],
  ['queue', figureQueue(reports.queueMonthly(
    fixture['openportal-project-usage-reports'],
    scope.map((row) => row.project_code),
  ))],
  ['storage-projects', figureStorageProjects(storageByMonth)],
  ['storage-users', figureStorageUsers(storageByMonth)],
];

const storageByMonth = reports.storageMonthly(
  reports.storageSamples(
    fixture['openportal-project-storage-reports'],
    scope.map((row) => row.project_code),
  ),
);

/** Every colour the palette knows, in its light step. */
const PALETTE = new Set([
  ...SERIES.map(([pale]) => pale),
  ...Object.values(CHROME).map(([pale]) => pale),
]);

describe('every figure', () => {
  it('builds without throwing, and draws something', () => {
    for (const [name, figure] of every()) {
      assert.ok(figure, `${name} produced nothing`);
      assert.ok(figure.data.length > 0, `${name} has no traces`);
      assert.ok(figure.layout.title.text, `${name} has no title`);
    }
  });

  it('never declares a second y-axis', () => {
    // The rule the whole design rests on: a figure that could show two measures
    // offers buttons that rewrite the one axis, so nothing is ever plotted
    // against a scale it does not belong to.
    for (const [name, figure] of every()) {
      assert.equal(figure.layout.yaxis2, undefined, `${name} grew a second y-axis`);
      for (const trace of figure.data) {
        assert.notEqual(trace.yaxis, 'y2', `${name} put a trace on y2`);
      }
    }
  });

  it('draws only in colours the theme switch can swap', () => {
    // The switch works by hex lookup, so a colour outside the palette is a
    // colour that stays light-mode forever on a dark page.
    for (const [name, figure] of every()) {
      for (const trace of figure.data) {
        for (const colour of [trace.marker?.color, trace.line?.color]) {
          if (typeof colour === 'string') {
            assert.ok(PALETTE.has(colour), `${name} uses ${colour}, which is not in the palette`);
          }
        }
      }
    }
  });

  it('keeps the title clear of the view buttons', () => {
    // Found by loading the extension and looking at it, which is the only way
    // it could have been found: the specification was correct and the geometry
    // was wrong, so the title rendered as "…came from, month by mon|" with the
    // button row drawn over the rest of it.
    //
    // Three things want the strip above the plot — plotly's modebar, the
    // button row and the title. A centred title and a right-anchored button
    // row grow towards each other, and the narrower the window the sooner they
    // touch. So the title is pinned left in *container* coordinates, on its
    // own band above the buttons, and the top margin is deep enough for both.
    for (const [name, figure] of every()) {
      const { title, margin, updatemenus } = figure.layout;
      assert.equal(title.xanchor, 'left', `${name} centres its title into the button row`);
      assert.equal(title.xref, 'container', `${name} anchors its title to the plot, not the figure`);
      if ((updatemenus ?? []).length) {
        // The button row sits at y=1.03 in paper coordinates and is about
        // 30px tall; the title needs a band of its own above that.
        assert.ok(margin.t >= 96, `${name} has only ${margin.t}px above the plot for both`);
      }
    }
  });

  it('offers both scales wherever it offers a choice at all', () => {
    for (const [name, figure] of every()) {
      const menus = figure.layout.updatemenus ?? [];
      for (const menu of menus) {
        assert.ok(menu.buttons.length >= 2, `${name} has a menu with one button`);
      }
    }
  });
});

describe('the categorical palette', () => {
  /** Ten projects, which is past the point where hue can tell series apart. */
  function manyProjects() {
    return Array.from({ length: 10 }, (_, index) => ({
      month: '2026-02',
      project_code: `p${index}`,
      project_name: `Project ${index}`,
      customer_name: 'UKRI',
      node_hours: 100 - index,
      active_users: 1,
      entitlement_node_hours: 25804.8,
      pct_of_entitlement: 1,
      mean_nodes: 1,
    }));
  }

  it('never generates an eighth hue', () => {
    const banded = reports.rankedBands(manyProjects());
    const figure = figureProjects(banded, totals.filter((row) => row.month === '2026-02'));
    const named = figure.data.filter((trace) => trace.name !== 'Other projects');
    assert.equal(named.length, 7, 'more than seven coloured series');
    for (const trace of named) {
      assert.ok(SERIES.some(([pale]) => pale === trace.marker.color));
    }
  });

  it('folds the tail into one neutral band, and puts it last', () => {
    const banded = reports.rankedBands(manyProjects());
    const figure = figureProjects(banded, totals.filter((row) => row.month === '2026-02'));
    const last = figure.data[figure.data.length - 1];
    assert.equal(last.name, 'Other projects');
    assert.equal(last.marker.color, CHROME.other[0]);
    // Context, not a series: it must not be carrying a slot that would let the
    // theme switch treat it as one.
    assert.deepEqual(last.meta, { chrome: 'other' });
  });

  it('keeps the tail in the total rather than dropping it', () => {
    const projects = manyProjects();
    const banded = reports.rankedBands(projects);
    const drawn = figureProjects(banded, totals.filter((row) => row.month === '2026-02'))
      .data.reduce((sum, trace) => sum + trace.y.reduce((a, b) => a + b, 0), 0);
    const actual = projects.reduce((sum, row) => sum + row.node_hours, 0);
    assert.equal(drawn, actual);
  });
});

describe('the headline figure', () => {
  it('hatches the month in progress, and only that one', () => {
    const figure = figureShare(totals, nodes, share, awarded);
    const pattern = figure.data[0].marker.pattern.shape;
    assert.deepEqual(pattern, totals.map((row) => (row.is_partial ? '/' : '')));
    assert.ok(pattern.includes('/'), 'the fixture no longer has a partial month');
  });

  it('draws the awarded line only when something was actually awarded', () => {
    const withAwards = figureShare(totals, nodes, share, awarded);
    assert.ok(withAwards.data.some((trace) => trace.name === 'Awarded to projects'));

    const without = figureShare(totals, nodes, share, months.map(() => 0));
    assert.ok(!without.data.some((trace) => trace.name === 'Awarded to projects'));
    // Both views must still line up with the traces they rewrite, or plotly
    // silently redraws the wrong series.
    for (const button of without.layout.updatemenus[0].buttons) {
      assert.equal(button.args[0].y.length, without.data.length);
    }
  });

  it('keeps every view button in step with the trace count', () => {
    const figure = figureShare(totals, nodes, share, awarded);
    for (const button of figure.layout.updatemenus[0].buttons) {
      assert.equal(button.args[0].y.length, figure.data.length);
    }
  });
});

describe('the heatmap', () => {
  it('leaves a project with no usage in a month blank, not at zero', () => {
    // A blank cell is "held no allocation"; a zero would claim the project was
    // set up and idle, which is a different and more damning statement.
    const figure = figureHeatmap(perProject, totals, allocation);
    assert.ok(figure.data[0].z.flat().includes(null));
    assert.ok(figure.data[0].text.flat().includes('no allocation'));
  });

  it('offers the relative view only when some project has an award rate', () => {
    const withRates = figureHeatmap(perProject, totals, allocation);
    assert.equal(withRates.layout.updatemenus.length, 1);

    const without = figureHeatmap(perProject, totals, []);
    assert.deepEqual(without.layout.updatemenus, []);
  });
});

describe('the demand figure', () => {
  it('is absent rather than empty when the portal has no usage reports', () => {
    assert.equal(figureQueue([]), null);
  });
});

describe('the quota heatmaps', () => {
  it('colours a bounded fraction on a linear scale, not a log one', () => {
    // The departure from the node-hour heatmap, and the reason the figure is
    // readable: a fill percentage runs 0 to 100 and the whole decision lives
    // at the top of that range. A log ramp would put half-full and nearly-full
    // a few pixels apart.
    const figure = figureStorageProjects(storageByMonth);
    assert.equal(figure.data[0].zmin, 0);
    assert.equal(figure.data[0].zmax, 100);
    assert.deepEqual(figure.data[0].colorbar.ticktext, ['0', '25%', '50%', '75%', '100%']);
  });

  it('carries its own ramp, so a repaint cannot hand it the activity blues', () => {
    assert.equal(figureStorageProjects(storageByMonth).data[0].meta.ramp, 'fill');
    assert.equal(figureStorageUsers(storageByMonth).data[0].meta.ramp, 'fill');
  });

  it('leaves the quota ramp behind on the size views', () => {
    // A bounded fraction is linear and ends in red; an unbounded magnitude is
    // logarithmic and stays one hue. The size views are log10 bytes with no
    // ceiling, so the quota ramp would paint a large project red for being
    // large -- the opposite of what that colour means on every other view of
    // the same figure. Switched by name as well as by value, so that a theme
    // repaint, which reads the name, keeps it.
    const figure = figureStorageProjects(storageByMonth);
    const buttons = new Map(
      figure.layout.updatemenus[0].buttons.map((button) => [button.label, button.args[0]]),
    );
    for (const label of ['Peak', 'End', 'Median']) {
      assert.equal(buttons.get(label)['meta.ramp'], 'fill');
      assert.equal(buttons.get(label).colorscale[0].at(-1)[1], RAMP_FILL_LIGHT.at(-1));
    }
    for (const label of ['Peak size', 'End size', 'Median size']) {
      assert.equal(buttons.get(label)['meta.ramp'], 'activity');
      assert.equal(buttons.get(label).colorscale[0].at(-1)[1], RAMP_LIGHT.at(-1));
    }
  });

  it('marks a month that was not observed on every day', () => {
    // Both fixture months are short, and a column standing on one reading is
    // not comparable with one standing on thirty.
    const figure = figureStorageProjects(storageByMonth);
    assert.ok(figure.data[0].x.every((label) => label.endsWith('*')));
  });

  it('orders rows so the fullest quota is at the top', () => {
    // Plotly draws the first row at the bottom, so ascending peak puts the
    // person about to run out where the eye lands.
    const figure = figureStorageUsers(storageByMonth);
    const peaks = figure.data[0].y.map((key) => {
      const rows = storageByMonth.filter(
        (row) => `${row.username} · ${row.project_code}` === key,
      );
      return Math.max(...rows.map((row) => row.peak_fill_pct ?? -1));
    });
    assert.deepEqual(peaks, [...peaks].sort((a, b) => a - b));
  });

  it('spends its buttons on the filesystem for people and on size for projects', () => {
    // Flat rather than two groups, because plotly's groups do not compose.
    const projects = figureStorageProjects(storageByMonth);
    assert.deepEqual(
      projects.layout.updatemenus[0].buttons.map((button) => button.label),
      ['Peak', 'End', 'Median', 'Peak size', 'End size', 'Median size'],
    );
    const users = figureStorageUsers(storageByMonth);
    assert.deepEqual(
      users.layout.updatemenus[0].buttons.map((button) => button.label),
      ['Home peak', 'Home end', 'Home median', 'Scratch peak', 'Scratch end', 'Scratch median'],
    );
  });

  it('puts the size on every tooltip, since no button carries it for people', () => {
    const figure = figureStorageUsers(storageByMonth);
    const hovers = figure.data[0].text.flat().filter((text) => text !== 'no reading');
    assert.ok(hovers.length);
    assert.ok(hovers.every((text) => /\d GB|\d TB|\d MB|\d KB/.test(text)));
    assert.ok(hovers.every((text) => text.includes('days')));
  });

  it('leaves a quota with no reading blank, not at zero', () => {
    // One fixture user holds a home quota and no scratch one, so the blank
    // appears when the buttons switch filesystem rather than on the default
    // view. Drawing it as zero would claim an empty disk, not an absent one.
    const figure = figureStorageUsers(storageByMonth);
    const scratch = figure.layout.updatemenus[0].buttons.find(
      (button) => button.label === 'Scratch peak',
    );
    const [{ z, text }] = scratch.args;
    assert.ok(z[0].flat().includes(null));
    assert.ok(text[0].flat().includes('no reading'));
  });

  it('is absent rather than empty when the portal reports no quotas', () => {
    assert.equal(figureStorageProjects([]), null);
    assert.equal(figureStorageUsers([]), null);
  });
});

describe('sizes written back out', () => {
  it('reaches petabytes rather than stopping at four figures of terabytes', () => {
    // The unit the parser accepts is the unit the renderer has to reach. A
    // project quota measured in petabytes is not hypothetical on a machine
    // this size, and "1,024.00 TB" is precisely the noise this exists to avoid.
    assert.equal(reports.humaniseBytes(1024 ** 5), '1.00 PB');
    assert.equal(reports.humaniseBytes(2.5 * 1024 ** 5), '2.50 PB');
  });
});

describe('the projects a quota figure covers', () => {
  const administered = [
    { project_code: 'abc1', customer_name: 'UKRI' },
    { project_code: 'abc2', customer_name: 'UKRI' },
    { project_code: 'zzz9', customer_name: 'Other Uni' },
  ];

  it('keeps a project that has never run a job', () => {
    // The bug this replaced took the codes off the monthly usage, so a project
    // with no compute vanished from the disk figures -- and a project with no
    // compute is exactly the one whose disk fills up with nobody watching.
    // Nothing here has a usage row at all.
    assert.deepEqual(reports.scopedCodes(administered, 'UKRI'), ['abc1', 'abc2']);
  });

  it('drops the organisations the report is not about', () => {
    assert.ok(!reports.scopedCodes(administered, 'UKRI').includes('zzz9'));
  });

  it('keeps every project the token administers when no organisation is chosen', () => {
    assert.deepEqual(reports.scopedCodes(administered, null), ['abc1', 'abc2', 'zzz9']);
  });
});

describe('the staleness warning on the quota figures', () => {
  // This endpoint is documented as answering, unchanged and without an error,
  // after its collector has silently stopped. The heatmap's columns just end,
  // and the date is otherwise only inside a collapsed table -- so if the page
  // does not say it in words, nothing does.
  const readings = (day) => [{ date: day }, { date: '2020-01-01' }];
  const on = (iso) => new Date(`${iso}T12:00:00Z`);

  it('says nothing while the collector is keeping up', () => {
    assert.equal(storageStaleness(readings('2026-03-01'), on('2026-03-02')), '');
  });

  it('stays quiet right up to the threshold and speaks the day after', () => {
    // A collector between runs must not be accused of being dead.
    const day = new Date(Date.UTC(2026, 2, 1) + (STALE_DAYS - 1) * 86400000);
    assert.equal(storageStaleness(readings('2026-03-01'), day), '');
    const next = new Date(Date.UTC(2026, 2, 1) + STALE_DAYS * 86400000);
    assert.ok(storageStaleness(readings('2026-03-01'), next).length);
  });

  it('gives the date it stopped and how long ago that was', () => {
    const warning = storageStaleness(readings('2026-03-01'), on('2026-06-01'));
    assert.ok(warning.includes('1 March 2026'));
    assert.match(warning, /92 days/);
    assert.ok(warning.includes('<strong>'));
  });

  it('reads the newest reading, not the first row it is handed', () => {
    // `storageCurrent` is sorted by how full each quota is, so the freshest
    // date is in no particular position. Taking the first row would call a
    // current report months out of date on the strength of one dead quota.
    const scrambled = [{ date: '2020-01-01' }, { date: '2026-03-01' }, { date: '2019-06-30' }];
    assert.equal(storageStaleness(scrambled, on('2026-03-02')), '');
  });

  it('says nothing at all when there are no readings', () => {
    assert.equal(storageStaleness([], on('2026-06-01')), '');
  });
});

describe('the invoice cross-check table', () => {
  // Two routes to one number. What the table has to preserve is that the
  // reader can tell a known accounting quirk from an unstable pull, and
  // neither of those is legible from a count of failures alone.
  const rows = [
    { month: '2026-01', node_hours: 1000, incurred_costs: 1000, difference: 0,
      pct_difference: 0, status: 'ok', invoice_state: 'created', is_partial: false },
    { month: '2026-02', node_hours: 1200, incurred_costs: 900, difference: 300,
      pct_difference: 33.3, status: 'usage high', invoice_state: 'created',
      is_partial: false },
    { month: '2026-03', node_hours: 400, incurred_costs: null, difference: null,
      pct_difference: null, status: 'no invoice', invoice_state: null, is_partial: true },
  ];

  it('lists every month, not only the ones that disagree', () => {
    // A gap with no agreeing months beside it has no scale to be read against.
    const html = reconcileTable(rows);
    for (const label of ['Jan 2026', 'Feb 2026', 'Mar 2026']) {
      assert.ok(html.includes(label), `${label} is missing from the table`);
    }
  });

  it('opens itself when something disagrees, and stays shut when nothing does', () => {
    assert.match(reconcileTable(rows), /<details class="reconcile" open>/);
    const clean = rows.filter((row) => row.status !== 'usage high');
    assert.match(reconcileTable(clean), /<details class="reconcile">/);
  });

  it('names the failure in words, never in colour alone', () => {
    // Several series colours on this page already sit below 3:1 contrast; a
    // tinted row on its own would not be a signal for every reader.
    const html = reconcileTable(rows);
    assert.ok(html.includes('usage high'));
    assert.ok(html.includes('class="flagged"'));
  });

  it('shows both routes and the gap, signed, rather than a verdict', () => {
    const html = reconcileTable(rows);
    assert.ok(html.includes('Node hours used') && html.includes('Invoiced'));
    // Positive means usage nobody billed -- the shape a duplicated page takes.
    assert.match(html, /\+33\.3%/);
  });

  it('marks the month in progress, which is expected to disagree', () => {
    assert.match(reconcileTable(rows), /Mar 2026 <em>\(partial\)<\/em>/);
  });

  it('draws a missing side as a dash, never as a zero', () => {
    // A month absent from one side is not a zero on it, and rendering it as
    // one would invent a 100% disagreement out of nothing.
    assert.ok(reconcileTable(rows).includes('class="na"'));
  });

  it('renders nothing at all when there is nothing to check', () => {
    assert.equal(reconcileTable([]), '');
  });
});
