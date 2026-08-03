# The utilisation report, in a browser

A browser extension that builds the same utilisation report as
`waldur-tools viz`, live, from a portal API token pasted into the page. No
install of this package, no Python, no snapshot — for the people who want the
answer without the toolchain.

```bash
pixi run web-vendor      # writes web/vendor/plotly.min.js, once
```

Then load `web/` as an unpacked extension — `chrome://extensions` →
*Developer mode* → *Load unpacked* — and click the toolbar button. Paste a
token, press **Build the report**.

## Why an extension, and not a web page

**Because the API's CORS allowlist has exactly one origin in it.** Measured
against the live deployment, by sending the preflight a series of `Origin`
headers:

| `Origin` sent | `access-control-allow-origin` returned |
| --- | --- |
| `https://portal.isambard.ac.uk` | echoed back |
| `http://localhost:8080` | *absent* |
| anything else | *absent* |

The preflight answers 200 either way; without the header the browser simply
discards the response, so a page served from a GitHub Pages URL, or opened from
disk (`file://` sends `Origin: null`), can never read the API. An extension with
`host_permissions` is not subject to CORS at all, which is the whole reason this
is packaged as one.

Two things in those same headers make the rest of it work:
`access-control-expose-headers` lists `x-result-count` and `Link`, which are
exactly the two headers the paging guards below need.

**If you would rather have a plain URL**, the route is to ask whoever runs the
portal to add your origin to Waldur's `CORS_ALLOWED_ORIGINS`. Nothing here would
have to change but the packaging: `src/` is a static page, and the only
extension-specific code is `background.js`, the `chrome.storage.session` block
in `report.js`, and the `chrome.permissions` request beside it.

## What it does and does not carry

Six figures, against the eight `waldur-tools viz` produces.

| Figure | Here | Why |
| --- | --- | --- |
| Monthly usage vs share | ✅ | |
| Stacked usage by project | ✅ | |
| Project × month heatmap | ✅ | |
| Total per project | ✅ | |
| Engagement | ✅ | |
| Demand: jobs and queue wait | ✅ | from `openportal-project-usage-reports` |
| Job-size distribution | ❌ | needs `sacct` |
| Queue wait by what was requested | ❌ | needs `sacct` |
| Spread of waits per month | ❌ | needs `sacct` |

The three missing ones read a SLURM capture. The portal has **no per-job view** —
its usage reports stop at daily totals — so what a job asked the scheduler for
exists only on the cluster, and a browser on a laptop cannot get to a login node.
That is a limit of the data, not of this port.

One thing it adds: the **invoice cross-check runs on the page**. `waldur-tools`
asks you to run `report reconcile` before quoting anything out of the visual
report, and nobody reading a web page is going to. It is one cheap endpoint and
the same arithmetic, so the badge beside the organisation picker says whether
the node hours in these figures agree with what the portal billed.

## How it stays fast

The usage endpoint is the whole cost: tens of thousands of rows across
nineteen-odd months, growing by roughly a thousand rows a month. Everything else
is about a hundred rows. So the page is built in three waves:

1. **Five small endpoints in parallel** — allocations, accounting summary,
   customers, projects, invoices. The header and credit tiles appear in about a
   second.
2. **The usage table, newest month first, six months in flight.** Pages *within*
   a month are a `Link`-header chain and stay sequential; months are independent,
   so the round trips overlap. Every month that lands redraws the figures, at
   most once every 300ms.
3. **The tail in the background** — the daily usage reports behind the demand
   figure, and the associations behind the "people with access" denominator.
   Neither blocks anything above it.

Complete months are then kept in IndexedDB. A calendar month that has ended is
not written to again, so a cached copy stays correct indefinitely; the month in
progress is never cached. A second visit refetches one month instead of twenty.

Because the usage endpoint is **not** scoped to an organisation, changing the
organisation, the node count or the share re-renders from rows already in hand
and costs no request at all.

### Two things worth measuring

Neither changes the design, only the speed, and both need a live token:

- **Does `page_size` go above 200?** At 200 the usage pull is around 155 pages.
  If the deployment accepts 1000 it is nearer 30.
- **Does `?field=` work?** The usage rows carry `full_name`, `allocation`, `user`
  and `username` where four columns would do, and the pull is the better part of
  ten megabytes of JSON. Unlike a filter, field selection is safe to verify — you
  can see whether the returned keys shrank. Beware that Waldur's DRF filters
  **silently ignore parameters they do not recognise**, so anything that looks
  like a filter must be checked against `X-Result-Count`, never assumed.

## The paging guards are not optional

`page`/`page_size` is `LIMIT`/`OFFSET` underneath, and that only enumerates a
queryset once if the ordering is total. `openportal-allocation-user-usage` is
ordered by `(year, month)` and nothing else, so paging it end to end returns some
rows two and three times and never returns others — which once put a month's node
hours well past the true figure and read as a finding until it was checked.

`api.js` reproduces every guard `client.py` grew in response:

- the `year=1900` probe, which catches the filter being silently ignored;
- a per-month row count checked against `X-Result-Count` for that same filter;
- a duplicate check on `(allocation, user, year, month)`, because duplicates and
  omissions cancel out in a count;
- the months summing to the unfiltered total for the table as a whole.

The last of those doubles as the cache's staleness check: if the portal backfills
an old month, the totals stop agreeing, the cache is dropped and the pull runs
again. Staleness is caught by arithmetic rather than by an expiry someone guessed.

## The token

Held in a variable, sent to the portal and to nothing else. If you tick *keep
until this browser closes* it goes in `chrome.storage.session`, which lives in
memory and is dropped on browser exit — never `localStorage`, which would leave a
token on disk long after the portal expired it. **Portal tokens expire within
hours, not days.** *Clear cached data* removes both the token and the cached
months.

## Tests

```bash
pixi run test-web       # runs pytest first, then all three files below
```

**`tests/parity.test.mjs` — the numbers.** `src/reports.js` is a second
implementation of the formulas in `waldur_tools.reports`, and two
implementations of the same arithmetic drift silently: nothing about a wrong
`mean_monthly_allocation` looks wrong on a chart. So they are pinned to each
other. `tests/test_web_parity.py` runs the Python reports over the ordinary test
fixtures and writes `tests/fixture.json` (the inputs, in API shape) and
`tests/expected.json` (what Python makes of them); this then runs the JavaScript
over the same fixture and asserts it lands on the same numbers.

**The Python is the definition.** Change a formula there and the pytest rewrites
`expected.json` *and fails* until you commit it; the node test then fails until
the JavaScript agrees again.

**`tests/api.test.mjs` — the guards.** Each one fails silently if it is wrong — a
dropped check looks exactly like a clean pull — so each is exercised against a
stub portal that misbehaves in the specific way the real one did: a short page, a
duplicated row, a filter silently ignored, months that do not add up, a `Link`
header pointing back at itself, and a cache gone stale under a backfill.

**`tests/figures.test.mjs` — the rules that are invisible in a diff.** No second
y-axis, no eighth hue, the tail folded into the neutral band and still counted in
the total, only colours the theme switch can swap, the partial month hatched, and
every view button in step with the trace count it rewrites.

Presentation is deliberately *not* pinned across languages: `viz.py` and
`src/figures.js` are two renderings of the same series and may differ. What may
not differ is any number either of them draws.

**None of this opens a browser.** It covers the arithmetic and the figure
specifications; whether plotly actually draws them is a question only a browser
answers. Load the extension and look at it before believing the suite.

## Layout

```
manifest.json          MV3: host permission for the API, one action, no content scripts
src/background.js      opens the report in a tab
src/report.html/.css   the page shell
src/report.js          the orchestrator: token, fetch order, progressive render
src/api.js             paging, and the guards that make the totals trustworthy
src/store.js           the IndexedDB month cache
src/reports.js         the analyses — the file the parity test pins
src/figures.js         the plotly figures
src/palette.js         colour, and the light/dark swap
src/page.js            prose, tiles, table views
tests/*.test.mjs       parity with the Python, the paging guards, the figure rules
tests/*.json           the golden fixture, generated by tests/test_web_parity.py
vendor/plotly.min.js   written by `pixi run web-vendor`, not committed
```

`vendor/plotly.min.js` comes out of `plotly.offline.get_plotlyjs()` — the same
bundle `waldur-tools viz` inlines — so the two reports cannot end up rendering on
different plotly versions. It is not committed: five megabytes of somebody else's
minified code, reproducible in one command.

There is no build step and no node dependency. The extension loads from source;
node appears only to run the parity test.

## Pointing it at another Waldur

Change the API URL on the form. `optional_host_permissions` covers it, and the
extension asks for that host the first time — so it stays useful against a second
deployment without claiming access to every site at install time.
