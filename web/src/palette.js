/**
 * Colour, and the theme switch that swaps it.
 *
 * A straight port of the palette tables at the top of `waldur_tools.viz`, and
 * of the theme JavaScript that file emits into its page. The design notes there
 * apply here unchanged, and are worth repeating because they are easy to undo:
 *
 * - Series colours come from a palette validated for colour-vision deficiency
 *   (worst adjacent pair ΔE 9.1 light / 8.4 dark, against a floor of 8). **The
 *   slot order is the safety mechanism**: assign slots in order, and never
 *   generate an eighth hue. "Other" takes the neutral, which is why seven is
 *   enough.
 * - Every colour a figure draws with must appear in one of these tables,
 *   because the theme switch works by hex lookup.
 * - Three of the light-mode series colours sit below 3:1 contrast, and the
 *   documented relief for that is the table view under every figure. It is not
 *   an optional extra.
 */

/** Categorical slots, in the order they must be assigned. Light, then dark. */
export const SERIES = [
  ['#2a78d6', '#3987e5'], // blue
  ['#eb6834', '#d95926'], // orange
  ['#1baf7a', '#199e70'], // aqua
  ['#eda100', '#c98500'], // yellow
  ['#e87ba4', '#d55181'], // magenta
  ['#008300', '#008300'], // green
  ['#4a3aa7', '#9085e9'], // violet
];

/** Marks that are not series: the neutral bucket, reference lines, ink, grid. */
export const CHROME = {
  surface: ['#fcfcfb', '#1a1a19'],
  plane: ['#f9f9f7', '#0d0d0d'],
  ink: ['#0b0b0b', '#ffffff'],
  ink_soft: ['#52514e', '#c3c2b7'],
  muted: ['#898781', '#898781'],
  grid: ['#e1e0d9', '#2c2c2a'],
  axis: ['#c3c2b7', '#383835'],
  other: ['#898781', '#898781'],
  critical: ['#d03b3b', '#d03b3b'],
  good: ['#0ca30c', '#0ca30c'],
};

/**
 * Sequential ramp for the activity heatmap: one hue, low end nearest the
 * surface in each mode -- so light reads more-is-darker and dark reads
 * more-is-brighter, rather than flipping one into an unreadable copy.
 */
export const RAMP_LIGHT = [
  '#f0efec', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#0d366b',
];
export const RAMP_DARK = [
  '#242423', '#104281', '#184f95', '#256abf', '#3987e5', '#6da7ec', '#cde2fb',
];

/**
 * Sequential ramp for the quota heatmaps, with a hot tail.
 *
 * Unlike node hours, a fill percentage is *bounded*: the whole decision runs
 * from empty to full, and the top of that range is the only part anyone acts
 * on. So the ramp leaves the blues around two thirds and finishes through
 * amber into red, which puts the quotas about to fail in a colour nothing else
 * on the page uses. Every hex comes from `SERIES` or `CHROME`, so the
 * light/dark swap needs no new pairs.
 */
export const RAMP_FILL_LIGHT = [
  '#f0efec', '#d8e8f8', '#9ec5f4', '#6da7ec', '#eda100', '#eb6834', '#d03b3b',
];
export const RAMP_FILL_DARK = [
  '#242423', '#104281', '#256abf', '#3987e5', '#eda100', '#eb6834', '#d03b3b',
];

/** Ramps by the name traces carry in `meta.ramp`. */
export const RAMPS = {
  activity: { light: RAMP_LIGHT, dark: RAMP_DARK },
  fill: { light: RAMP_FILL_LIGHT, dark: RAMP_FILL_DARK },
};

export const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';

/**
 * Where a zero is drawn on a logarithmic node-hour axis. A tenth of a node hour
 * is below anything the portal records as real work, so the bar reads as
 * "nothing" at a glance while still having a length to draw.
 */
export const FLOOR_NODE_HOURS = 0.1;

/** The light-mode hex for a chrome role. */
export function light(role) {
  return CHROME[role][0];
}

/** Plotly's colorscale form for a ramp. */
export function colorscale(ramp) {
  return ramp.map((colour, index) => [index / (ramp.length - 1), colour]);
}

const SWAP = Object.fromEntries(
  [...SERIES, ...Object.values(CHROME)].filter(([pale, dark]) => pale !== dark),
);
const UNSWAP = Object.fromEntries(Object.entries(SWAP).map(([pale, dark]) => [dark, pale]));

function shade(hex, dark) {
  const map = dark ? SWAP : UNSWAP;
  return map[hex] ?? hex;
}

/**
 * Repaint every rendered figure for the given mode.
 *
 * Restyles rather than redraws: a redraw would lose whichever view button or
 * slider position the reader had selected, which is most of what the figures
 * offer.
 */
export function paint(dark) {
  const index = dark ? 1 : 0;
  for (const div of document.querySelectorAll('.plotly-graph-div')) {
    if (!div.data) continue;
    div.data.forEach((trace, position) => {
      const style = {};
      if (trace.meta && trace.meta.ramp) {
        // Named rather than assumed: activity is one hue, the quota ramps end
        // in red, and repainting must not swap one figure's scale for another's.
        const ramp = RAMPS[trace.meta.ramp] ?? RAMPS.activity;
        style.colorscale = [colorscale(dark ? ramp.dark : ramp.light)];
        style['colorbar.tickfont.color'] = [CHROME.muted[index]];
        style['colorbar.title.font.color'] = [CHROME.ink_soft[index]];
      } else {
        if (trace.marker && typeof trace.marker.color === 'string') {
          style['marker.color'] = [shade(trace.marker.color, dark)];
        }
        if (trace.marker && trace.marker.line) {
          style['marker.line.color'] = [CHROME.surface[index]];
        }
        if (trace.line && typeof trace.line.color === 'string') {
          style['line.color'] = [shade(trace.line.color, dark)];
        }
        if (trace.textfont) style['textfont.color'] = [CHROME.ink_soft[index]];
      }
      if (Object.keys(style).length) Plotly.restyle(div, style, [position]);
    });
    Plotly.relayout(div, {
      paper_bgcolor: CHROME.surface[index],
      plot_bgcolor: CHROME.surface[index],
      'font.color': CHROME.ink_soft[index],
      'title.font.color': CHROME.ink[index],
      'legend.font.color': CHROME.ink_soft[index],
      'xaxis.linecolor': CHROME.axis[index],
      'xaxis.tickcolor': CHROME.axis[index],
      'xaxis.tickfont.color': CHROME.muted[index],
      'xaxis.gridcolor': CHROME.grid[index],
      'xaxis.title.font.color': CHROME.ink_soft[index],
      'yaxis.linecolor': CHROME.axis[index],
      'yaxis.tickcolor': CHROME.axis[index],
      'yaxis.tickfont.color': CHROME.muted[index],
      'yaxis.gridcolor': CHROME.grid[index],
      'yaxis.zerolinecolor': CHROME.axis[index],
      'yaxis.title.font.color': CHROME.ink_soft[index],
      'updatemenus[0].bgcolor': CHROME.surface[index],
      'updatemenus[0].bordercolor': CHROME.axis[index],
      'updatemenus[0].font.color': CHROME.ink_soft[index],
    });
  }
}

const THEME_KEY = 'waldur-viz-theme';

/** Apply a mode, repaint the figures, and remember the choice. */
export function setTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  const button = document.querySelector('.theme');
  if (button) button.textContent = mode === 'dark' ? 'Light mode' : 'Dark mode';
  paint(mode === 'dark');
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch {
    // Storage is a convenience here; the page works without remembering.
  }
}

export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

/** The reader's stored preference, or what their system asks for. */
export function preferredTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(THEME_KEY);
  } catch {
    stored = null;
  }
  if (stored) return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
