/**
 * The prose, tiles and table views around the figures.
 *
 * Ported from the `_table` / `_tile` / `_section` helpers and the page template
 * at the bottom of `waldur_tools.viz`. The wording is deliberately the same:
 * these paragraphs are the report's argument, not decoration, and a reader who
 * has seen the emailed HTML should recognise the page.
 */

import { format } from './figures.js';
import { humaniseBytes, monthLabel, monthLabelLong } from './reports.js';

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/**
 * A frame as an HTML table -- the WCAG-clean twin of the figure above it.
 *
 * Not an optional extra: several series colours sit below 3:1 contrast on the
 * light surface, and the documented relief for that is a readable table.
 *
 * `columns` is `[key, heading, kind]`, where kind is `month`, `number` or the
 * default text.
 */
export function tableView(rows, columns) {
  if (!rows.length) return '';
  const head = columns.map(([, heading]) => `<th>${esc(heading)}</th>`).join('');
  const body = rows
    .map((row) => {
      const cells = columns
        .map(([key, , kind]) => {
          const value = row[key];
          if (value === null || value === undefined) return '<td></td>';
          if (kind === 'month') return `<td>${esc(monthLabel(value))}</td>`;
          if (kind === 'number') return `<td>${format(Number(value), 1)}</td>`;
          // Bytes are humanised here rather than upstream, so the row keeps a
          // number: a quota in bytes is fourteen digits of noise at one
          // decimal place, and a string would sort alphabetically.
          if (kind === 'size') return `<td>${esc(humaniseBytes(Number(value)))}</td>`;
          return `<td>${esc(value)}</td>`;
        })
        .join('');
      return `<tr>${cells}</tr>`;
    })
    .join('');
  return (
    '<details><summary>Table view</summary><div class="tablewrap"><table>' +
    `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>` +
    '</table></div></details>'
  );
}

/**
 * The invoice cross-check, month by month, under the badge.
 *
 * The badge alone says *whether* the pull reconciles, which is enough to stop
 * someone quoting a bad headline and not enough to do anything about it. Two
 * months that disagree are either an unstable pull or a known accounting
 * quirk, and those look identical from a summary -- so the arithmetic is put
 * on the page: both routes to the same node hours, their difference, and the
 * state of the invoice behind it.
 *
 * Every month is listed, not just the failures. A reader checking a
 * disagreement against what they already know needs the agreeing months beside
 * it, or there is no scale to read the gap against.
 *
 * Open by default when something disagrees, closed when nothing does.
 */
export function reconcileTable(rows) {
  if (!rows.length) return '';
  const bad = rows.filter((row) => row.status !== 'ok' && row.status !== 'no invoice');
  const cell = (value, digits = 1) =>
    value === null || value === undefined ? '<td class="na">—</td>' : `<td>${format(value, digits)}</td>`;

  const body = rows
    .map((row) => {
      const flagged = row.status !== 'ok' && row.status !== 'no invoice';
      const sign = row.pct_difference !== null && row.pct_difference > 0 ? '+' : '';
      return (
        `<tr class="${flagged ? 'flagged' : ''}">` +
        `<td>${esc(monthLabel(row.month))}${row.is_partial ? ' <em>(partial)</em>' : ''}</td>` +
        cell(row.node_hours) +
        cell(row.incurred_costs) +
        cell(row.difference) +
        (row.pct_difference === null
          ? '<td class="na">—</td>'
          : `<td>${sign}${format(row.pct_difference, 1)}%</td>`) +
        `<td>${esc(row.invoice_state ?? '—')}</td>` +
        `<td class="${flagged ? 'bad' : 'good'}">${esc(row.status)}</td>` +
        '</tr>'
      );
    })
    .join('');

  return (
    `<details class="reconcile"${bad.length ? ' open' : ''}>` +
    `<summary>Invoice cross-check, month by month${
      bad.length ? ` — ${bad.length} month${bad.length === 1 ? '' : 's'} disagree` : ''
    }</summary>` +
    '<p class="sub">Node hours summed from <code>openportal-allocation-user-usage</code> ' +
    'beside <code>incurred_costs</code> on the invoice for the same month — two routes to ' +
    'the same figure, aggregated by different sides of the portal. A positive difference ' +
    'is usage nobody billed, which is the shape a duplicated page takes; a negative one ' +
    'is billing with no usage behind it, which is not something paging can cause. ' +
    'The month in progress is expected to disagree and is marked partial.</p>' +
    '<div class="tablewrap"><table><thead><tr>' +
    '<th>Month</th><th>Node hours used</th><th>Invoiced</th><th>Difference</th>' +
    '<th>%</th><th>Invoice state</th><th>Status</th>' +
    `</tr></thead><tbody>${body}</tbody></table></div></details>`
  );
}

export function tile(label, value, note, hero = false) {
  return (
    `<div class="tile${hero ? ' hero' : ''}">` +
    `<div class="label">${esc(label)}</div>` +
    `<div class="value">${esc(value)}</div>` +
    `<div class="note">${esc(note)}</div></div>`
  );
}

/**
 * One figure's shell: heading, prose, an empty plot div and an empty table slot.
 *
 * Built once and then filled, rather than rebuilt on every render. Replacing
 * the markup would destroy the plotly div and take the reader's chosen view
 * button or slider position with it, which is most of what the figures offer.
 * `prose` is trusted markup from `PROSE`.
 */
export function section(id, heading, prose) {
  return (
    `<section class="fig" id="fig-${esc(id)}"><h2>${esc(heading)}</h2><p>${prose}</p>` +
    `<div class="plot" id="plot-${esc(id)}"></div>` +
    `<div id="table-${esc(id)}"></div></section>`
  );
}

/** The prose for each figure, keyed by the id its section carries. */
export const PROSE = {
  share:
    "Each column is a month's node hours. The grey line is what our share of the machine " +
    'is worth; the dotted line is what has actually been <em>awarded</em> to projects, at ' +
    'the rate their award periods imply. The distance between the two lines is share ' +
    'nobody has been given, and no amount of running harder reaches it. Usage above the ' +
    'dotted line is projects spending their awards faster than the award period assumed. ' +
    'The hatched column is the month in progress and is incomplete by construction.',
  projects:
    'The seven largest projects by hue, the rest folded into a neutral band; the full list ' +
    'is in the table. The <em>% of the month</em> view answers a different question from ' +
    'the other two: how concentrated a month was, regardless of how big it was.',
  heatmap:
    'One cell per project per month, on a log colour scale so a test job and a production ' +
    'campaign are both visible. A row that stays at the background colour is a project ' +
    'that was set up and never used &mdash; capacity we hold and do not convert, and the ' +
    'most actionable thing on this page. The <em>% of own allocation</em> view measures ' +
    "each project against its <strong>mean monthly allocation</strong>: the credits it " +
    'holds, divided by the number of months between its start and end dates. It passes ' +
    '100% freely, because an award is a lump for a period and not a monthly ration.',
  totals:
    'If a couple of bars carry most of the total, our utilisation is one or two research ' +
    'groups deep. The axis is logarithmic; a project that never ran is drawn at the far ' +
    'left and labelled zero.',
  engagement:
    'Three counts on one axis. The gap between projects set up and projects that ran ' +
    'something is the onboarding gap; the people line is whether usage rests on more than ' +
    'a handful of individuals.',
  'storage-projects':
    'Node hours are a flow; disk is a level, so a month has to be summarised by picking a ' +
    'statistic rather than by adding one up. <em>Peak</em> is the fullest it got &mdash; ' +
    'the reading that decides whether writes failed, and the default. <em>End</em> is the ' +
    'level carried into the next month, and <em>median</em> is the typical level, unmoved ' +
    "by a single day's spike. Colour is the percentage of the quota rather than the size, " +
    'because every project holds the same limit and only the fraction says who is in ' +
    'trouble; the <em>size</em> views and every tooltip give the bytes. A month marked ' +
    '<code>*</code> was not observed on every day.',
  'storage-users':
    'The same reading, per person. Home is a hundredth the size of scratch, so the two are ' +
    'never comparable as sizes and the colour stays a percentage of whichever quota the ' +
    'buttons select. Home is where this bites: it is small, it is where people put things ' +
    'they meant to keep, and a full one stops a job as surely as a full scratch does. Rows ' +
    'are ordered by the fullest that quota ever got, so the people worth an email are at ' +
    'the top.',
  queue:
    'Utilisation that is low <em>and</em> quick to schedule is a demand problem; low ' +
    'utilisation with long waits is a job-shape or scheduling problem. This is as far as ' +
    'the portal goes: its usage reports stop at daily totals, so what a job actually ' +
    "asked the scheduler for is not here. <code>waldur-tools viz</code> on a machine with " +
    'a <code>sacct</code> capture adds three figures that answer it.',
};

/** The opening paragraphs, which state what every percentage is measured against. */
export function intro({ nodes, share, customer, span, bestMonth, creditHeld, unallocated,
  unallocatedMonths, awardedPct }) {
  const held = nodes * share;
  const sharePct = `${(share * 100).toFixed(0)}%`;
  const best = bestMonth
    ? `The best month so far was ${esc(monthLabelLong(bestMonth.month))}, at ` +
      `${format(bestMonth.pct_of_entitlement, 0)}%.`
    : '';
  return `
<header>
  <div>
    <h1>Are we using our share of Isambard&nbsp;3?</h1>
    <p class="sub">${esc(customer ?? 'All visible projects')} · ${esc(span)} ·
      built live from the portal API</p>
  </div>
  <button class="theme" type="button">Dark mode</button>
</header>

<p>Isambard&nbsp;3 has ${nodes} compute nodes and our share of it is
${sharePct} &mdash; <strong>${format(held, 1)} nodes, held for every hour of every
month</strong>. That is ${format(held * 24, 0)} node hours a day, and roughly
${format(held * 24 * 30, 0)} a month. Every percentage on this page is usage measured
against that, so 100% would mean we ran, on average, exactly the nodes we hold.</p>

<p>Nothing on the machine cuts us off. ${best}
Nor is the constraint a shortage of credit: we hold ${format(creditHeld, 0)} node hours of it
and <strong>${format(unallocated, 0)} has never been allocated to a project</strong> &mdash;
${format(unallocatedMonths, 1)} months of our entire share, sitting unassigned. What has
reached projects amounts to about ${format(awardedPct, 0)}% of the share, the dotted line on
the first chart, and that is the ceiling everything below it works under.
<a href="#method">How to read these numbers</a> sets out what is and is not enforced.</p>
`;
}

/**
 * The caveats that bound every number above them.
 *
 * Carried over from the generated report almost word for word, with the two
 * differences this version introduces called out: it reads the API live rather
 * than a snapshot, and it has no SLURM capture to draw job shape from.
 */
export function method({ nodes, share, customer, creditHeld, unallocated, unallocatedMonths }) {
  const held = nodes * share;
  const sharePct = `${(share * 100).toFixed(0)}%`;
  return `
<section class="method" id="method">
<h2>How to read these numbers</h2>
<ul>
<li><strong>The source is one endpoint.</strong> Every figure comes from
<code>openportal-allocation-user-usage</code>, the only endpoint with a time axis: one row
per user, allocation and calendar month. Nothing is smoothed, imputed or back-filled, and a
month with no rows is a month with no usage. It is pulled a calendar month at a time and
each month checked against the server's own row count, because paging it end to end returns
some rows twice and drops others.</li>
<li><strong>This page reads the portal live.</strong> The command-line report builds from a
timestamped snapshot you can keep and diff; this one holds nothing but the months it has
already fetched, and the figures reflect the portal as of the moment you loaded it.</li>
<li><strong>Node hours are assumed.</strong> The portal calls the field
<code>node_usage</code> and does not state a unit. This reads it as node hours, which is
what makes ${format(held, 1)} nodes &times; 24 h &times; days the right comparison. If it
turns out to be node <em>days</em>, every percentage here is far too small &mdash; but the
shape of every curve is unchanged.</li>
<li><strong>Scope is ${esc(customer ?? 'every project this token administers')}.</strong>
The portal also shows separately funded UKRI and other organisations' projects that share
the same token; counting those in would inflate our own share.</li>
<li><strong>The last month is partial.</strong> It is hatched in the first figure, and
excluded from every headline average.</li>
<li><strong>Nothing enforces the ${sharePct}, and nothing needs to.</strong>
Isambard&nbsp;3 runs SLURM with every priority weight &mdash; fair share included &mdash;
set to zero, so jobs are scheduled first come, first served with backfill; no account is
held back for running over its share, and none is favoured for running under it. The only
enforced ceiling is <em>per project</em> &mdash; a <code>GrpTRESMins</code> on each
project's SLURM account, which the portal sets from the credits that project has left. So
what bounds the figures on this page is not the scheduler and not a shortage of credit: the
organisation holds ${format(creditHeld, 0)} node hours of credit, of which
<strong>${format(unallocated, 0)} has never been allocated to any project</strong> &mdash;
${format(unallocatedMonths, 1)} months at 100% of our share, sitting unassigned. The binding
constraint is how much of it reaches a project, and then whether that project has work to
run.</li>
<li><strong>Two percentages, two denominators.</strong> Everything at page level is measured
against the organisation's share above. The <em>% of own allocation</em> view on the project
heatmap is measured against each project's own award, and passes 100% routinely &mdash; a
project may spend a year of credits in a month, because nothing paces the spend. The two are
not comparable and are never drawn on the same axis.</li>
<li><strong>Mean monthly allocation is ours, not the portal's.</strong> The relative view on
the project heatmap divides a project's usage by its credits spread evenly over the months
between its start and end dates. The portal grants credits as a lump for a period and never
states a monthly figure, so this is a construction, and it back-dates top-ups: a project
that doubled its award half way through shows its early months at half their true share.</li>
<li><strong>Job shape is missing here, and only here.</strong> The command-line report gains
three figures on job size and queue wait from <code>sacct</code> records captured on a login
node. The portal has no per-job view, so a browser cannot answer those questions at all.</li>
<li><strong>The usage is checked against the invoices.</strong> The badge at the top compares
what these figures sum to against <code>incurred_costs</code> &mdash; the same node hours by
a completely different route, since this deployment bills one credit per node hour. If it
does not say <em>ok</em>, suspect the pull before believing the headline.</li>
</ul>
</section>
`;
}
