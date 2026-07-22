# Developer notes

What this package actually does to your data, and why it is shaped this way.
The [README](README.md) is enough to use it; this is for when you need to trust
a number, or change one.

## The short answer on data provenance

**Almost nothing is computed.** Every report selects columns straight from one
or two endpoints and renames a few. The derived columns are listed in full
below, all arithmetic you could do in your head. No imputation, no smoothing,
no filling of missing values, no inferred rows.

The things that are *not* pass-through, and that you should know about before
quoting a figure:

1. **Scoping.** `membership` and `user-usage` drop rows for projects you do not
   administer, by default; `monthly`, `monthly-totals` and the visual report go
   further and keep one organisation. See
   [Administrative scope](#administrative-scope).
2. **`queue` reshapes.** It explodes a nested JSON blob into one row per day.
   The numbers inside are untouched.
3. **The monthly reports introduce a denominator the portal does not have.**
   `entitlement_node_hours` is `nodes * share * 24 * days_in_month` — a figure
   assembled from two numbers you pass in, not one the API returns. See
   [The share, and what it assumes](#the-share-and-what-it-assumes).

Everything else is the portal's own view, rearranged into a table.

## Data model

Isambard names things in SLURM's terms, and the shape of every join here
follows from that:

| Field | Example | Structure |
| --- | --- | --- |
| allocation `groupname` | `brics.abc1` | `<cluster>.<project code>` |
| allocation `backend_id` | `abc1.brics` | `<project code>.<cluster>` |
| association `username` | `jdoe.abc1` | `<unix user>.<project code>` |
| association `useridentifier` | `jdoe.abc1.brics` | `<unix user>.<project code>.<cluster>` |
| user-usage `username` | `jdoe.abc1.brics` | as above |

The **project code** (e.g. `abc1`) is the join key throughout — see
`reports.project_code()`. It is stable, and unlike the allocation URL it does
not care that a project has several allocations.

That last point matters. Each project holds one allocation *per service*: the
portal returns two allocation rows per project, one each for
`Isambard 3` and `Isambard 3 Multi Architecture System`. They share a
`groupname`, a `node_limit` and a project, and differ in `service_name`,
`uuid`, `url` and `node_usage`. An association points at exactly one of them,
so **joining associations to allocations on the URL loses most of the estate**;
joining on the project code does not. That is the single biggest correctness
difference between this package and the upstream `rse-sharing` example.

## Administrative scope

The portal is multi-tenant. Isambard 3 is operated by Bristol's BriCS team, and
a token issued to, say, an Exeter administrator sees:

| Endpoint | Rows visible | Scope |
| --- | --- | --- |
| `openportal-allocations` | small | **yours only** |
| `customers` | small | yours only |
| `projects` | small | yours only |
| `users` | small | yours only |
| `openportal-associations` | large | **the whole machine** |
| `openportal-allocation-user-usage` | large | **the whole machine** |

So some endpoints are filtered for you and some are not, with no flag saying
which. Worse, the unfiltered ones are *partly* redacted: a substantial
fraction of those association rows come back with `username`, `groupname` and
`useridentifier` all `null`, leaving nothing but a UUID and an allocation URL.
Those are the blank rows you would see in a naive table.

`reports.in_scope()` resolves this by deriving your scope from the endpoint
that *is* filtered: **the project codes of the allocations you can see**. This
reduces associations spanning many project codes on the whole machine down to
the rows for the handful of codes you administer. Eyeballing for a missing
`project_name` gets you to roughly the same place, but this is the same rule
stated positively, and it tells you *which* projects rather than just which
rows to distrust.

There is no server-side filter for this. Waldur's DRF filter backends silently
ignore query parameters they do not recognise — a fabricated `modified_after`
returns the full result set with an unchanged `X-Result-Count` and no error —
so an unsupported filter looks exactly like a filter that matched everything.
Do not trust a filter you have not verified changes the count.

Pass `--all` (CLI) or `scope=False` (library) to opt out.

**The monthly reports narrow it once more.** Of the project codes a
GW4-partner token administers, most belong to that partner and a few do not:
other, separately funded projects that share the same token. So `monthly`,
`monthly_totals` and `viz.render` take a `customer` argument, defaulting to
`reports.DEFAULT_CUSTOMER`, and filter to that organisation's projects.
Counting the other ones in would inflate the very share the report exists to
measure. `--all`, `scope=False` or `customer=None` widens it back to every
code administered — which is as wide as these reports can honestly go, since
usage rows for *other* organisations arrive with project codes that resolve
to no name, no customer and no limit.

## Derived columns, in full

### `credits` — from `openportal-accounting-summary`

Pass-through: `project_name`, `customer_name`, `total_credits`, `total_spend`,
`current_month_spend`, `end_date`.

| Column | Formula |
| --- | --- |
| `remaining` | `total_credits - total_spend` |
| `used_pct` | `100 * total_spend / total_credits`, null when credits are 0 |
| `overspent` | `remaining < 0` |
| `months_remaining` | `remaining / current_month_spend`, null when this month's spend is 0 |

`months_remaining` goes **negative** for an overspent project, because
`remaining` is: an award of 40,000 credits with 42,000 spent is -2,000
remaining, and if 1,000 of that was spent this month that is `-2.0`. Read it
as "already over, and still burning". A null means the project spent nothing
this month — which is not the same as being safe. Sorted most-negative first,
so trouble leads.

### `membership` — from `openportal-associations` + `openportal-allocations` + `users`

| Column | Derivation |
| --- | --- |
| `unix_username` | association `username` before the first `.` |
| `project_code` | association `groupname` after the first `.` |
| `project_name`, `customer_name` | joined from `openportal-allocations` on `project_code` |
| `full_name`, `email` | joined from `users` on `unix_username` |
| `associations` | count of source rows collapsed into this pairing |

One row per user *per project*, not per association. The raw endpoint carries an
association per service, so in-scope rows roughly halve into pairings, nearly
all with `associations = 2`. A value other than 2 means the user is on a
different number of services than the usual pair — worth a look rather than a
worry.

`users` is one of the endpoints the portal already scopes to your organisation,
so `full_name` and `email` fill in for your own people and stay null for
everyone else — which under `--all` doubles as a second, independent signal of
who is yours.

Scoped by default. Sorted by customer, project, then user.

### `utilisation` — from `openportal-allocations`

Pass-through: `project_name`, `service_name`, `customer_name`, `node_limit`,
`is_active`, `state`.

| Column | Derivation |
| --- | --- |
| `node_usage_this_month` | `node_usage`, renamed (see below) |
| `project_code` | from `groupname` |
| `month_vs_limit_pct` | `100 * node_usage / node_limit`, null when the limit is 0 |

**`node_usage` is not cumulative.** It equals `current_month_spend` from the
accounting summary to the penny for every project observed (largest
discrepancy seen: 0.01, a rounding difference). It is this month's usage,
and the field name does not say so — hence the rename.

That is why this report's percentage looks nothing like `credits.used_pct`, and
why nothing here tops 100% while a project can be over budget over there:

| | numerator | denominator |
| --- | --- | --- |
| `credits.used_pct` | lifetime spend | lifetime credits |
| `month_vs_limit_pct` | **this month's** usage | remaining node limit |

`node_limit` empirically tracks `total_credits - total_spend` as of the last
SLURM sync — exact (to the nearest whole node hour, since it is truncated) for
most projects, within a few percent for the rest — and never goes negative,
so an overspent project still shows a positive limit. The portal documents
none of this; it is an observation, not a contract, and it is the reason
`month_vs_limit_pct` cannot exceed 100%.

`state` is Waldur's resource state machine (`Creating`, `OK`, `Erred`,
`Deleting`): whether the portal succeeded in provisioning the allocation onto
the cluster, not whether anyone is using it. Every allocation in normal
operation reads `OK`. `is_active` is the separate question of whether the
allocation is switched on.

### `monthly` and `monthly-totals` — from `openportal-allocation-user-usage`

The only endpoint with a time axis, and therefore the base of every figure in
the visual report. One source row per user, allocation and calendar month.
`monthly` groups by month and project; `monthly-totals` groups by month alone.
Neither is derivable from the other: distinct users per month cannot be
recovered by summing a per-project user count, so both aggregate the same
private base frame (`reports._monthly_rows`).

| Column | Derivation |
| --- | --- |
| `month` | `date(year, month, 1)` from the two integer columns |
| `node_hours` | `sum(node_usage)` — cumulative-safe here, unlike the same-named field on allocations |
| `active_users` | distinct `unix_username` **with non-zero usage**, so it reads as "who ran", not "who could have" |
| `active_projects` | distinct project codes with non-zero usage (totals only) |
| `projects_with_usage_rows` | distinct project codes reporting *at all* that month, zero included — the gap against `active_projects` is projects that existed and ran nothing (totals only) |
| `entitlement_node_hours` | `nodes * share * 24 * days_in_month` — see below |
| `pct_of_entitlement` | `100 * node_hours / entitlement_node_hours` |
| `mean_nodes` | `node_hours / (24 * days_in_month)`: nodes running, averaged over the month |
| `unused_node_hours` | `entitlement - node_hours`, negative when we went over (totals only) |
| `is_partial` | the month the data was taken in (totals only) |

On `monthly`, `pct_of_entitlement` measures **one project against the whole
organisation's share** — "how much of our slice did this project alone account
for?". It is not a per-project quota; nothing in the portal allocates the share
out to projects, and the columns will not sum to a project's own limit.

`is_partial` comes from `reports.as_of()`, which reads `created` out of the
snapshot's `meta.json` (falling back to today for a live client). A snapshot
taken on the 21st holds three weeks of that month, and averaging it in beside
complete months drags every headline down; the report marks it instead of
dropping it, and the visual report hatches that column and excludes it from the
cumulative figure and every average.

### The share, and what it assumes

Two assumptions sit under `entitlement_node_hours`, and neither comes from the
API.

**The machine and the share.** Isambard 3 has 384 compute nodes and the share
held here is 10% of them: `TOTAL_NODES` and `DEFAULT_SHARE` in `reports`, both
overridable per call and from `--nodes` / `--share`. If either changes, change
the constant rather than the arithmetic.

**The unit.** The portal calls the field `node_usage` and never states what it
counts. Everything here reads it as **node hours**, which is what makes
`38.4 nodes × 24 h × days` the right comparison. The reading is not arbitrary —
`node_usage` matches `current_month_spend` to the penny, credits are billed per
node-hour on this deployment, and the resulting percentages land in a plausible
band rather than orders of magnitude off. But it is a reading. If it
turns out to be node *days*, every percentage in the report is ~24× too small,
while the shape of every curve is unchanged.

**Above 100% is not an error.** The share is an average entitlement, not a
quota: SLURM fair-share lets a busy month borrow capacity nobody else claimed.
Some months in a snapshot exceed it, occasionally by a wide margin. This is
the opposite of `utilisation.month_vs_limit_pct`, which *cannot* exceed 100%
because its denominator is a node limit that tracks the remaining balance.

### `user-usage` — from `openportal-allocation-user-usage`

One source row per user, allocation and calendar month. Grouped by
`unix_username` and `full_name`.

| Column | Derivation |
| --- | --- |
| `total_node_usage` | `sum(node_usage)` — cumulative here, unlike the field of the same name on allocations |
| `projects` | distinct project codes, sorted, comma-joined |
| `months_active` | count of source rows with `node_usage > 0` |
| `first_year`, `last_year` | `min`/`max` of `year` |

`months_active` counts *rows*, so a user active on two allocations in one month
counts twice. Treat it as an activity score, not a calendar count. Scoped by
default.

### `queue` — from `openportal-project-usage-reports`

The only report that reshapes. Each source row is a project/resource/month
carrying a free-form `report` blob whose `reports` key maps a date to a day's
figures. Output is one row per project/resource/day.

| Column | Derivation |
| --- | --- |
| `num_jobs`, `total_wait_seconds` | verbatim from the day's entry |
| `mean_wait_seconds` | `total_wait_seconds / num_jobs`, **null** on a day with no jobs |
| `distinct_users` | `len(user_job_counts)` for that day |

monthly rows expand roughly thirtyfold into daily ones.

## The visual report

`waldur_tools.viz` builds the HTML page. `render()` returns a string rather than
writing a file, so a notebook can hand it to `IPython.display.HTML`; the CLI is
the only thing that touches disk. Every figure is a plain
`plotly.graph_objects.Figure` and is exported individually, so a single one can
be pulled into a slide deck without the page around it.

**Why one HTML file and not a Dash app.** The audience for this is a committee
and an allocation review, not a terminal. A Dash app needs a process, a port and
a host that is up when someone clicks the link; a file with plotly.js inlined
opens offline, five years from now, on a laptop that never had this tool
installed. `get_plotlyjs()` supplies the bundle, which is ~4.5 MB of the ~5 MB
page. **The bundle must be emitted in `<head>`, before the figures** — plotly
writes a `Plotly.newPlot` call inline beside each div, and those run as the
parser reaches them, so a bundle at the end of `<body>` yields a page of blank
boxes and no error.

### Rules the figures follow

These are not stylistic preferences; each one is there because the alternative
misleads. A change that undoes one of them should be deliberate.

- **No figure has a second y-axis.** Two measures on two scales invent a
  correlation out of where the axes happen to line up. Where a figure could show
  more than one measure, it carries *buttons* that rewrite the single axis
  (`viz._buttons`). This is also how absolute and relative views coexist:
  node hours, % of share, average nodes, % of the month.
- **Colour is assigned by entity, in fixed slot order.** `viz.SERIES` is a
  seven-slot palette validated for colour-vision deficiency — worst adjacent
  pair ΔE 9.1 light / 8.4 dark against a floor of 8, checked with a validator
  rather than by eye. The order is the safety mechanism, so slots are taken in
  order and an eighth hue is never generated. Past seven projects the tail folds
  into a neutral "Other" band (`viz._ranked`), which is why `keep=7` and not
  more.
- **Both themes are selected, not flipped.** Every colour is a
  `(light, dark)` pair; the page swaps by hex lookup in the browser
  (`_swap_map`), which is why *any colour a figure draws must appear in
  `SERIES` or `CHROME`* — one that does not will silently stay in its light step
  on a dark surface. `tests/test_viz.py` checks that.
- **Every figure has a table view.** Three of the light-mode series colours sit
  below 3:1 contrast against the surface; the documented relief for that is a
  readable table, so it is not optional decoration.
- **The heatmap is log-scaled.** A month of production is three orders of
  magnitude above a test job, and on a linear ramp everything but the peak
  renders as empty. Colour is `log10(1 + node_hours)`; the colourbar is
  relabelled `0, 10, 100, 1k, 10k` and the hover shows the real figure.
- **Sequential where the question is magnitude, categorical where it is
  identity.** The heatmap and the per-project totals bar are one hue; only the
  stacked figure and the engagement lines tell series apart.

### Supporting series

Two things the monthly reports do not carry:

| Function | Source | Why |
| --- | --- | --- |
| `projects_existing` | `projects.created` | The denominator behind "how many of the projects that existed ran something". Without it the active-project line reads as a plateau instead of a fraction. Returns empty rather than raising if `projects` is missing from an older snapshot, so the other six figures still render. |
| `people_with_access` | `reports.membership` | The denominator behind "how many of the people with access ran something". Access granted and never exercised is invisible in the usage endpoint, which only knows about people who ran. |
| `queue_monthly` | `reports.queue` | Rolls the daily queue report up to months. `mean_wait_hours` is total wait over total jobs, **not** the mean of daily means — a day with three jobs should not weigh as much as a day with three thousand. |

## The cache

`snapshot` writes a new timestamped directory of parquet files and `meta.json`.
Snapshots are **immutable**: nothing edits one in place, and refreshing means
taking another. A full pull is on the order of several minutes and tens of thousands of rows.

`meta.json` is written last, and `Snapshot.available()` only counts directories
that have one — so an interrupted pull is skipped rather than read as a
snapshot with silently missing endpoints.

There is no incremental update, and this is a limitation of the deployment, not
an omission. Delta fetching needs the server to answer "what changed since?",
and these endpoints accept no such parameter — and, per the warning above, they
ignore unrecognised ones rather than rejecting them, so a hand-rolled
`?modified_after=` would appear to work while silently returning everything.
Diffing client-side would cost a full re-fetch anyway, and deletions would
never be detected. Hence: full snapshots only.

`report` reads the newest snapshot; `--use NAME` pins an older one; `--live`
bypasses the cache entirely and writes nothing.

## HTTP layer

The transport is the official [`waldur-api-client`](https://pypi.org/project/waldur-api-client/),
which owns authentication, the base URL and the `httpx` client, and which does
ship the Isambard-specific `openportal-*` endpoints.

`waldur_tools.client` adds one thing on top: a uniform paginated **raw JSON**
reader. Two reasons. The endpoints carrying the most interesting data nest
free-form payloads that the generated attrs models flatten into opaque objects;
and iterating ~200 endpoints by name beats importing ~200 generated modules.
The typed API is still one attribute away — pass `WaldurClient.raw` to any
`waldur_api_client.api.*.sync` function.

Pagination follows `Link` headers, with a `seen`-set guard: a page that links to
itself raises rather than spinning forever. That guard exists because a test
once hung on exactly that.

This deployment publishes **no OpenAPI schema** (`/api/schema/`, `/api-docs/`
and friends all 404), so the generated client cannot be regenerated against it.
It is pinned from PyPI and may drift from what the portal returns; the raw
reader is the fallback that keeps working when it does.

## Frames

`frames.to_frame()` JSON-encodes nested dicts and lists into strings rather than
inferring a struct schema. Inferring across thousands of ragged free-form
`report` blobs is slow and fragile; callers explode only the part they need,
which is what `queue` does. Keys are unioned across records so rows with
missing optional fields still line up.

Waldur serialises money and usage as **decimal strings** (`"1234.56"`), so
`frames.numeric()` and `frames.integral()` cast explicitly. Without `integral`,
a year renders as `2,025.00`.

## Testing

`pytest`, with `respx` mocking `httpx` at the transport layer — no network. The
fixtures in `tests/conftest.py` deliberately reproduce the awkward parts of the
real data: a project with two service allocations, an association pointing at an
allocation the token cannot see, another organisation's user, and a row the
portal blanked entirely. If a change survives those, it survives the portal.

`tests/test_viz.py` renders the whole page once (module-level cache — it is 5 MB)
and asserts the properties that are easy to lose in a refactor and invisible in
a diff: nothing is fetched at view time, no figure declares a `yaxis2`, both
scales are offered, table views exist, and every palette colour has a dark step.

**None of that catches a blank plot.** The bundle-ordering bug above passed every
assertion in this file — the markup was correct and the JavaScript threw no
error, the figures simply never drew. If you change how the page is assembled,
open it, or screenshot it headlessly, before believing the suite.
