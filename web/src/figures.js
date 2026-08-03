/**
 * The figures, ported from `waldur_tools.viz`.
 *
 * Each function returns `{data, layout}` for `Plotly.react`. The design rules
 * they encode are the ones stated in `viz.py` and worth restating, because a
 * later change can undo them without looking wrong:
 *
 * - **No figure has two y-axes.** Where a figure could show more than one
 *   measure it offers buttons that rewrite the single axis, so nothing is ever
 *   plotted against a scale it does not belong to. That is also how absolute
 *   and relative views are toggled.
 * - **Colour is assigned by slot, in order**, from a palette validated for
 *   colour-vision deficiency. Seven slots, and the eighth is never generated:
 *   the tail becomes one neutral "Other" band instead.
 * - **Every figure carries a table view.** Three of the light-mode series
 *   colours sit below 3:1 contrast, and the table is their relief.
 *
 * The three job-shape figures in `viz.py` are absent by construction: they read
 * a `sacct` capture, the portal has no per-job view, and a browser on a laptop
 * has no way to reach a login node.
 */

import {
  CHROME, FLOOR_NODE_HOURS, FONT, RAMP_LIGHT, SERIES, colorscale, light,
} from './palette.js';
import { monthLabel } from './reports.js';

/** Shared layout: recessive chrome, generous padding, hover on by default. */
function layout(overrides = {}) {
  return {
    font: { family: FONT, size: 13, color: light('ink_soft') },
    paper_bgcolor: light('surface'),
    plot_bgcolor: light('surface'),
    margin: { l: 70, r: 30, t: 60, b: 110 },
    height: 460,
    hovermode: 'x unified',
    hoverlabel: { font: { family: FONT } },
    // Below the axis, not above it: the top band already carries the title and
    // the view buttons, and a two-row legend up there collides with both as
    // soon as the project names get long.
    legend: {
      orientation: 'h',
      yanchor: 'top',
      y: -0.16,
      x: 0,
      font: { color: light('ink_soft') },
    },
    xaxis: {
      showgrid: false,
      linecolor: light('axis'),
      tickcolor: light('axis'),
      tickfont: { color: light('muted') },
    },
    yaxis: {
      gridcolor: light('grid'),
      zerolinecolor: light('axis'),
      linecolor: light('axis'),
      tickfont: { color: light('muted') },
      title: { font: { color: light('ink_soft') } },
    },
    ...overrides,
  };
}

/** A y-axis definition with a title, since every figure wants one. */
function yaxis(title, extra = {}) {
  return {
    gridcolor: light('grid'),
    zerolinecolor: light('axis'),
    linecolor: light('axis'),
    tickfont: { color: light('muted') },
    title: { text: title, font: { color: light('ink_soft') } },
    ...extra,
  };
}

/**
 * A single row of view-switching buttons above a figure.
 *
 * These are how a figure offers absolute *and* relative without a second
 * y-axis: each button rewrites the one axis.
 */
function buttons(labels, args) {
  return [
    {
      type: 'buttons',
      direction: 'right',
      showactive: true,
      x: 1.0,
      xanchor: 'right',
      y: 1.03,
      yanchor: 'bottom',
      pad: { r: 0, t: 0 },
      bgcolor: light('surface'),
      bordercolor: light('axis'),
      font: { family: FONT, size: 12, color: light('ink_soft') },
      buttons: labels.map((label, index) => ({
        label,
        method: 'update',
        args: args[index],
      })),
    },
  ];
}

function title(text) {
  return { text, font: { size: 16, color: light('ink') } };
}

/**
 * The headline: usage per month against what our share is worth.
 *
 * A column per month with a reference line at the entitlement, and buttons that
 * restate the same comparison in node hours or as a percentage.
 *
 * `awarded` adds a second reference line: the node hours a month actually
 * promised to projects. It is the more useful of the two, because the
 * entitlement is an accounting figure nobody has committed to anyone, while
 * this is the rate the credits behind the work were paced at. Usage above it is
 * projects spending their awards faster than the award period assumed, which is
 * only sustainable until the credits run out.
 */
export function figureShare(totals, nodes, share, awarded = null) {
  const labels = totals.map((row) => monthLabel(row.month));
  const held = nodes * share;
  const usage = totals.map((row) => row.node_hours);
  const worth = totals.map((row) => row.entitlement_node_hours);
  const percent = totals.map((row) => row.pct_of_entitlement);
  const hundred = labels.map(() => 100);
  // The in-progress month is hatched as well as annotated: a reader who ignores
  // the caption still sees that the last column is not comparable.
  const pattern = totals.map((row) => (row.is_partial ? '/' : ''));

  const promised = awarded ?? labels.map(() => 0);
  const promisedPct = promised.map((value, index) =>
    worth[index] ? (100 * value) / worth[index] : 0,
  );
  const hasAwards = promised.some((value) => value);

  const data = [
    {
      type: 'bar',
      x: labels,
      y: usage,
      name: 'Used',
      marker: { color: SERIES[0][0], pattern: { shape: pattern, solidity: 0.6 } },
      hovertemplate: '%{y:,.0f} node hours<extra></extra>',
      meta: { slot: 0 },
    },
    {
      type: 'scatter',
      x: labels,
      y: worth,
      name: `Our share (${held.toFixed(1)} nodes, every hour)`,
      mode: 'lines',
      line: { color: light('muted'), width: 2 },
      hovertemplate: '%{y:,.0f} node hours<extra></extra>',
      meta: { chrome: 'muted' },
    },
  ];
  if (hasAwards) {
    data.push({
      type: 'scatter',
      x: labels,
      y: promised,
      name: 'Awarded to projects',
      mode: 'lines',
      line: { color: SERIES[1][0], width: 2, dash: 'dot' },
      hovertemplate: '%{y:,.0f} node hours awarded<extra></extra>',
      meta: { slot: 1 },
    });
  }

  const absolute = hasAwards ? [usage, worth, promised] : [usage, worth];
  const relative = hasAwards ? [percent, hundred, promisedPct] : [percent, hundred];

  return {
    data,
    layout: layout({
      title: title('Monthly node hours used, against our share of the machine'),
      yaxis: yaxis('Node hours', { rangemode: 'tozero' }),
      updatemenus: buttons(
        ['Node hours', '% of share'],
        [
          [
            { y: absolute, hovertemplate: '%{y:,.0f} node hours<extra></extra>' },
            { 'yaxis.title.text': 'Node hours' },
          ],
          [
            { y: relative, hovertemplate: '%{y:,.1f}% of our share<extra></extra>' },
            { 'yaxis.title.text': '% of our monthly share' },
          ],
        ],
      ),
    }),
  };
}

/**
 * Where the usage comes from: a stacked column per month, by project.
 *
 * Three views of one stack: node hours, each project against the whole
 * organisation's share, and the month normalised to 100% -- which answers the
 * different question of concentration, "was this month one project?".
 */
export function figureProjects(banded, totals) {
  const months = totals.map((row) => row.month);
  const labels = months.map(monthLabel);
  const worth = new Map(totals.map((row) => [row.month, row.entitlement_node_hours]));
  const monthTotal = new Map(totals.map((row) => [row.month, row.node_hours]));

  const sizes = new Map();
  for (const row of banded) {
    sizes.set(row.band, (sizes.get(row.band) ?? 0) + row.node_hours);
  }
  // "Other" is context, not a series, so it takes the neutral and sits last.
  const order = [...sizes.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name]) => name)
    .filter((name) => name !== 'Other projects');
  if (sizes.has('Other projects')) order.push('Other projects');

  const data = [];
  const absolute = [];
  const relative = [];
  const normalised = [];
  order.forEach((name, index) => {
    const perMonth = new Map();
    for (const row of banded) {
      if (row.band !== name) continue;
      perMonth.set(row.month, (perMonth.get(row.month) ?? 0) + row.node_hours);
    }
    const values = months.map((month) => perMonth.get(month) ?? 0);
    absolute.push(values);
    relative.push(values.map((value, at) => (100 * value) / worth.get(months[at])));
    normalised.push(
      values.map((value, at) => {
        const total = monthTotal.get(months[at]);
        return total ? (100 * value) / total : 0;
      }),
    );
    const isOther = name === 'Other projects';
    data.push({
      type: 'bar',
      x: labels,
      y: values,
      name,
      // A 2px surface gap separates the segments; no borders drawn.
      marker: {
        color: isOther ? light('other') : SERIES[index][0],
        line: { width: 2, color: light('surface') },
      },
      hovertemplate: '%{fullData.name}: %{y:,.0f}<extra></extra>',
      meta: isOther ? { chrome: 'other' } : { slot: index },
    });
  });

  return {
    data,
    layout: layout({
      barmode: 'stack',
      height: 520,
      title: title('Which projects the usage came from, month by month'),
      yaxis: yaxis('Node hours'),
      updatemenus: buttons(
        ['Node hours', '% of share', '% of the month'],
        [
          [
            { y: absolute, hovertemplate: '%{fullData.name}: %{y:,.0f}<extra></extra>' },
            { 'yaxis.title.text': 'Node hours' },
          ],
          [
            { y: relative, hovertemplate: '%{fullData.name}: %{y:,.1f}%<extra></extra>' },
            { 'yaxis.title.text': '% of our monthly share' },
          ],
          [
            { y: normalised, hovertemplate: '%{fullData.name}: %{y:,.1f}%<extra></extra>' },
            { 'yaxis.title.text': "% of that month's usage" },
          ],
        ],
      ),
    }),
  };
}

/**
 * Every project against every month, so dormancy is visible as blank space.
 *
 * Sequential rather than categorical: the question is magnitude, and this is
 * the one figure that can carry every project at once. Colour is on a log scale
 * in both views, because a month of real work is three orders of magnitude
 * above a test job and a linear ramp would render everything but the peak as
 * empty.
 *
 * The second view divides each cell by that project's own mean monthly
 * allocation. It runs past 100% freely and is meant to: an award is a lump for
 * a period, not a monthly ration, so a project doing a year's work in two
 * months reads far above 100% for both and is behaving normally. Projects
 * holding no credits have no denominator and stay blank.
 */
export function figureHeatmap(perProject, totals, allocation) {
  const months = totals.map((row) => row.month);
  const labels = months.map(monthLabel);

  const sizes = new Map();
  for (const row of perProject) {
    sizes.set(row.project_name, (sizes.get(row.project_name) ?? 0) + row.node_hours);
  }
  const order = [...sizes.entries()].sort((a, b) => a[1] - b[1]).map(([name]) => name);

  const rates = new Map();
  for (const row of allocation ?? []) {
    if (row.mean_monthly_allocation) rates.set(row.project_name, row.mean_monthly_allocation);
  }

  const absolute = [];
  const absoluteText = [];
  const relative = [];
  const relativeText = [];
  for (const name of order) {
    const perMonth = new Map();
    for (const row of perProject) {
      if (row.project_name !== name) continue;
      perMonth.set(row.month, (perMonth.get(row.month) ?? 0) + row.node_hours);
    }
    // Undefined, not zero: a month with no row is a month the project held no
    // allocation, and drawing it as zero would claim it was idle.
    const values = months.map((month) => (perMonth.has(month) ? perMonth.get(month) : null));
    absolute.push(values.map((value) => (value === null ? null : Math.log10(value + 1))));
    absoluteText.push(
      values.map((value) =>
        value === null ? 'no allocation' : `${format(value, 0)} node hours`,
      ),
    );

    const rate = rates.get(name);
    const percents = values.map((value) =>
      value === null || !rate ? null : (100 * value) / rate,
    );
    // log10 of the percentage, so 100% sits at 2 and a project an order of
    // magnitude either side of its own rate is one step of colour away.
    relative.push(percents.map((value) => (value === null ? null : Math.log10(value + 0.1))));
    relativeText.push(
      percents.map((value) => {
        if (rate === undefined) return 'no credits awarded';
        if (value === null) return 'no usage';
        return `${format(value, 0)}% of its ${format(rate, 0)}/month`;
      }),
    );
  }

  const bar = {
    title: { text: 'Node hours', font: { color: light('ink_soft') } },
    tickvals: [0, 1, 2, 3, 4, 5],
    ticktext: ['0', '10', '100', '1k', '10k', '100k'],
    tickfont: { color: light('muted') },
    outlinewidth: 0,
    thickness: 12,
  };

  return {
    data: [
      {
        type: 'heatmap',
        x: labels,
        y: order,
        z: absolute,
        text: absoluteText,
        colorscale: colorscale(RAMP_LIGHT),
        hovertemplate: '%{y}<br>%{x}: %{text}<extra></extra>',
        xgap: 2,
        ygap: 2,
        colorbar: bar,
        meta: { ramp: true },
      },
    ],
    layout: layout({
      height: 170 + 26 * order.length,
      hovermode: 'closest',
      margin: { l: 240, r: 30, t: 60, b: 60 },
      title: title('Project activity: node hours per project per month'),
      yaxis: { linecolor: light('axis'), tickfont: { color: light('muted') }, showgrid: false },
      updatemenus: rates.size
        ? buttons(
            ['Node hours', '% of own allocation'],
            [
              [
                {
                  z: [absolute],
                  text: [absoluteText],
                  'colorbar.title.text': 'Node hours',
                  'colorbar.tickvals': [[0, 1, 2, 3, 4, 5]],
                  'colorbar.ticktext': [['0', '10', '100', '1k', '10k', '100k']],
                },
                {},
              ],
              [
                {
                  z: [relative],
                  text: [relativeText],
                  'colorbar.title.text': '% of allocation',
                  'colorbar.tickvals': [[-1, 0, 1, 2, 3]],
                  'colorbar.ticktext': [['0', '1%', '10%', '100%', '1000%']],
                },
                {},
              ],
            ],
          )
        : [],
    }),
  };
}

/**
 * Lifetime node hours per project, ranked -- how concentrated we are.
 *
 * One measure, one hue: colouring these bars by size would double-encode the
 * length they already show. The horizontal form is for the project names, which
 * are too long to sit under columns.
 *
 * The axis is logarithmic, because the spread is several orders of magnitude
 * and on a linear axis every project below the top two is a stub of identical
 * length. A log axis cannot draw a zero, so projects that never ran are pinned
 * at `FLOOR_NODE_HOURS` and labelled 0 -- they are the point of the figure, and
 * dropping them would be the worse distortion.
 */
export function figureTotalsByProject(ranked) {
  const grand = ranked.reduce((total, row) => total + row.total, 0) || 1;
  const values = ranked.map((row) => row.total);
  return {
    data: [
      {
        type: 'bar',
        x: values.map((value) => Math.max(value, FLOOR_NODE_HOURS)),
        y: ranked.map((row) => row.project_name),
        orientation: 'h',
        marker: { color: SERIES[0][0] },
        text: values.map(
          (value) => `${format(value, 0)}  (${format((100 * value) / grand, 1)}%)`,
        ),
        textposition: 'outside',
        textfont: { color: light('ink_soft') },
        cliponaxis: false,
        customdata: values,
        hovertemplate: '%{y}: %{customdata:,.1f} node hours<extra></extra>',
        meta: { slot: 0 },
      },
    ],
    layout: layout({
      height: 170 + 26 * ranked.length,
      hovermode: 'closest',
      margin: { l: 240, r: 120, t: 60, b: 60 },
      title: title('Total node hours per project, all months'),
      xaxis: {
        type: 'log',
        gridcolor: light('grid'),
        linecolor: light('axis'),
        tickfont: { color: light('muted') },
        title: { text: 'Node hours (log scale)', font: { color: light('ink_soft') } },
        tickvals: [FLOOR_NODE_HOURS, 1, 10, 100, 1000, 10000, 100000],
        ticktext: ['0', '1', '10', '100', '1k', '10k', '100k'],
      },
      yaxis: { linecolor: light('axis'), tickfont: { color: light('muted') } },
    }),
  };
}

/**
 * Projects and people actually running work, month by month.
 *
 * All three series are counts of the same kind of thing, so they share one axis
 * honestly. The gap between "projects set up" and "projects that ran something"
 * is the recruitment problem; the active-user line is whether the usage rests
 * on more than one person.
 */
export function figureEngagement(totals, existing) {
  const labels = totals.map((row) => monthLabel(row.month));
  const data = [];
  if (existing && existing.length) {
    const lookup = new Map(existing.map((row) => [row.month, row.projects]));
    data.push({
      type: 'scatter',
      x: labels,
      y: totals.map((row) => lookup.get(row.month) ?? 0),
      name: 'Projects set up',
      mode: 'lines',
      line: { color: light('muted'), width: 2 },
      hovertemplate: '%{y} projects exist<extra></extra>',
      meta: { chrome: 'muted' },
    });
  }
  data.push(
    {
      type: 'scatter',
      x: labels,
      y: totals.map((row) => row.active_projects),
      name: 'Projects that ran something',
      mode: 'lines+markers',
      line: { color: SERIES[0][0], width: 2 },
      marker: { size: 8 },
      hovertemplate: '%{y} active projects<extra></extra>',
      meta: { slot: 0 },
    },
    {
      type: 'scatter',
      x: labels,
      y: totals.map((row) => row.active_users),
      name: 'People who ran something',
      mode: 'lines+markers',
      line: { color: SERIES[1][0], width: 2 },
      marker: { size: 8 },
      hovertemplate: '%{y} active users<extra></extra>',
      meta: { slot: 1 },
    },
  );
  return {
    data,
    layout: layout({
      title: title('Engagement: projects set up, projects running, people running'),
      yaxis: yaxis('Count', { rangemode: 'tozero' }),
    }),
  };
}

/**
 * Jobs submitted and how long they waited, month by month.
 *
 * All the portal can answer about demand: its usage reports stop at daily
 * totals and hold no record of an individual job. What a job *asked* the
 * scheduler for exists only in SLURM, which a browser cannot reach -- so the
 * three job-shape figures of the command-line report have no counterpart here.
 */
export function figureQueue(monthlyQueue) {
  if (!monthlyQueue.length) return null;
  const labels = monthlyQueue.map((row) => monthLabel(row.month));
  const jobs = monthlyQueue.map((row) => row.num_jobs);
  const wait = monthlyQueue.map((row) => row.mean_wait_hours);
  return {
    data: [
      {
        type: 'bar',
        x: labels,
        y: jobs,
        name: 'Jobs',
        marker: { color: SERIES[0][0] },
        hovertemplate: '%{y:,.0f} jobs<extra></extra>',
        meta: { slot: 0 },
      },
    ],
    layout: layout({
      title: title('Demand: jobs submitted, and how long they waited'),
      yaxis: yaxis('Jobs submitted', { rangemode: 'tozero' }),
      updatemenus: buttons(
        ['Jobs', 'Mean wait'],
        [
          [
            { y: [jobs], hovertemplate: '%{y:,.0f} jobs<extra></extra>' },
            { 'yaxis.title.text': 'Jobs submitted' },
          ],
          [
            { y: [wait], hovertemplate: '%{y:,.1f} hours waited, mean<extra></extra>' },
            { 'yaxis.title.text': 'Mean queue wait (hours)' },
          ],
        ],
      ),
    }),
  };
}

/** Thousands separators and fixed decimals, as the Python format strings do. */
export function format(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return '';
  return value.toLocaleString('en-GB', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export { CHROME };
