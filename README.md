# waldur-tools

Snapshot, analyse and report on data from the [Waldur](https://docs.waldur.com/)
portal behind [Isambard](https://portal.isambard.ac.uk) — the same API the
`gw4-isambard/rse-sharing` example script talks to, wrapped as a proper package.

## What it does

- **Snapshot** — pull whole endpoints into parquet, so large tables are fetched
  once rather than on every question, and so you can diff the estate over time.
- **Analyse** — reports return [polars](https://pola.rs) DataFrames, equally
  usable from the CLI or a notebook.
- **Report** — a small CLI over the analyses, with CSV/JSON/parquet export.
- **Visualise** — `viz` writes one self-contained HTML page answering "are we
  using our 10% of Isambard 3?", with interactive figures and no server.
- **Hand it over** — [`web/`](web/README.md) is the same report as a browser
  extension, built live off the portal tab you are already signed in to, for
  people who will never install this package.

## Setup

You need [pixi](https://pixi.sh). Nothing else — not even direnv.

```bash
pixi install
echo 'export WALDUR_API_TOKEN=<your token>' > .envrc.local   # gitignored
pixi run waldur-tools whoami
```

The token comes from the portal, under your own account menu. `.envrc.local` is
gitignored and never committed; `scripts/activate.sh` (committed, secret-free)
sources it on every `pixi run` and `pixi shell`, along with sensible defaults
for `WALDUR_API_URL` and `WALDUR_CACHE_DIR`.

> **Portal tokens expire within hours, not days.**
> A token that worked this morning will not work this afternoon, so rewriting
> `.envrc.local` before a session is part of the routine rather than a one-off.
> pixi re-runs the activation script on every command rather than caching it,
> which is what makes that painless: paste the new token, run the next command,
> no shell restart.

If you would rather keep the token outside the repository entirely, put it in
`~/.config/waldur/token` or point `WALDUR_TOKEN_FILE` at a file; the environment
variable wins if all are set.

Everything lives in one pixi environment, so no command needs an `-e` flag. If
you use direnv, the committed `.envrc` is two lines that hand the same
environment to your interactive shell — then you can drop the `pixi run` prefix
too, and it reloads by itself when you rewrite the token.

## Use

```bash
pixi run waldur-tools whoami                  # check auth + config
pixi run waldur-tools endpoints --counts      # what this deployment exposes
pixi run waldur-tools snapshot                # refresh the local cache (~9 min)
pixi run waldur-tools report credits
pixi run waldur-tools report reconcile        # does the usage match the invoices?
pixi run waldur-tools report membership --limit 0            # every row
pixi run waldur-tools report queue --sort num_jobs --desc -o queue.csv
pixi run waldur-tools slurm-jobs              # per-job sacct records (on the cluster)
pixi run waldur-tools viz -o utilisation.html            # the visual report
```

### Reports

| Name | Question it answers |
| --- | --- |
| `credits` | How much of each project's allocation is spent, and how many months of runway remain at the current burn rate? |
| `allocations` | What was each project awarded, over what span, and what does that average per month? |
| `membership` | Which users have access to which project, with their real names and emails? (the `rse-sharing` example, as a join rather than a request per row) |
| `utilisation` | Which allocations went unused *this month* against their node limit? (one row per service — see the MACS note below) |
| `monthly` | Node hours per project per month, against our share of the machine |
| `monthly-totals` | The same, one row per month: are we using our 10%? |
| `reconcile` | Do those node hours agree with what the portal actually billed us? |
| `user-usage` | Who are the heaviest users by cumulative node usage? |
| `queue` | Daily job counts and mean queue wait, per project and resource |

Common options: `--limit N` (`0` for all rows), `--sort COL --sort COL2`
with `--desc`, `-o FILE.csv|.json|.parquet`, and `--all` on `allocations`,
`membership`, `user-usage`, `monthly`, `monthly-totals` and `reconcile` to lift
the scope filter described below.

**Run `reconcile` before you quote anything from `monthly`, `monthly-totals` or
`viz`.** It puts the node hours those three sum out of
`openportal-allocation-user-usage` beside `incurred_costs` from `invoices` —
the same node hours by a completely different route, since this deployment
bills one credit per node hour — and prints one word per month:

```
month        node_hours   incurred_costs    difference   status
2024-05       30,000.00        15,000.00     15,000.00   usage high
2024-06       12,000.00        12,000.00          0.00   ok
```

(Illustrative — replace with your own snapshot's numbers.) `ok` means the two
sides are within 1% of each other, and `usage high` / `usage low` mean the
pull is the first thing to suspect — which is exactly the check that would
have caught the paging bug described below, before an inflated headline
could be read as a finding.

The reports are thin: they select and rename columns from one or two endpoints
and derive a handful of arithmetic ones. **[DEVELOPER.md](DEVELOPER.md) lists
every derived column with its formula** — read it before quoting a number,
particularly for `months_remaining` and `month_vs_limit_pct`, both of which mean
something narrower than their names suggest.

### The visual report

```bash
pixi run waldur-tools viz -o utilisation.html
```

One file, about 5 MB, with plotly.js inlined: it opens offline, needs no server,
and survives being emailed to someone who will never install this tool. Open it
in a browser; there is a dark-mode toggle in the corner.

It exists to answer one question. **Isambard 3 has 384 compute nodes and our GW4
share is 10% of them — 38.4 nodes, held for every hour of every month.** Every
percentage on the page is usage measured against that, which makes 100% "we ran,
on average, exactly the nodes we hold" rather than a ceiling. The share is an
accounting figure with no scheduler behind it: every SLURM priority weight on
this machine is zero, so the queue is first come, first served and nothing
reserves those nodes for us or stops us exceeding them. Months over 100% are
real and not an error — see
[What actually limits us](DEVELOPER.md#what-actually-limits-us).

Each figure has a table view underneath and, where two scales make sense,
buttons that swap the y-axis rather than adding a second one:

| Figure | What you learn |
| --- | --- |
| Monthly usage vs share | The headline, in node hours or % of share |
| Stacked usage by project | Where it came from, and how concentrated each month was |
| Project × month heatmap | Which projects were set up and never used — in node hours, or against each project's own award |
| Total per project | Whether utilisation rests on two research groups (log axis) |
| Engagement | Projects set up vs projects running vs people running |

Plus three more if a SLURM capture is present (see below): the job-size
distribution with a month slider, queue wait against what jobs *requested*, and
the spread of waits per month as violins.

Options: `--nodes` and `--share` if either figure changes, `--customer ''` to
include the separately funded UKRI and other organisations' projects the portal also shows
us, `--jobs FILE` to point at a capture explicitly, plus the usual `--use`,
`--live` and `--root`.

Three caveats the page states for itself, repeated here because they bound every
number in it. **The unit is assumed:** the portal calls the field `node_usage`
and never says what it counts; this reads it as node hours. **The month the
snapshot was taken in is partial**, so it is hatched in the first figure and
excluded from every average. And **mean monthly allocation is a construction**,
not something the portal reports: credits are granted as a lump for a period,
and this spreads them evenly across it.

### The same report, in a browser

For the people who want the answer and not the toolchain, [`web/`](web/README.md)
is a browser extension that builds the same report live from the portal tab —
no Python, no snapshot, nothing installed but the extension.

```bash
pixi run web-vendor    # once: writes the plotly bundle it loads
```

Then load `web/` unpacked in Chrome, open your organisation's dashboard in the
portal, and press the toolbar button. That is the whole interaction: the token,
the API URL and the organisation are all read out of that tab, so nothing has to
be pasted and no institution is named anywhere in `web/`. A paste box remains as
the fallback for when a reading fails.

It carries six of the eight figures. The three job-shape ones need `sacct` and
are simply absent; the demand figure the portal *can* answer is there. It adds
one thing the generated page does not have: the `reconcile` check runs on the
page, as a badge, because nobody reading a web page is going to run it
separately.

**Why an extension rather than a URL:** the deployment's CORS allowlist holds
exactly one origin, the portal's own front end, so a page served from anywhere
else — or opened from disk — can never read the API. An extension with host
permissions is not subject to CORS. [web/README.md](web/README.md) has the
measurement, and what would have to change to make it a plain web page.

The formulas are a second implementation, so they are pinned to this one: the
Python reports generate a golden fixture and CI fails if the JavaScript drifts
off it. `pixi run check` runs both halves.

### Job shape, from SLURM

```bash
pixi run waldur-tools slurm-jobs     # on a cluster login node
pixi run waldur-tools viz -o utilisation.html
```

The portal has no per-job view — its usage reports stop at daily totals — so
what a job *asked for*, the `--nodes` and `--time` in the batch script, exists
only in SLURM. `slurm-jobs` runs `sacct` and writes `slurm-jobs.parquet` into
the cache root; `viz` picks it up automatically and gains three figures.

This is the only command here that reads something other than the API, so it
only works while logged in to the cluster. Everything else, `viz` included,
builds from a snapshot alone — without the capture those three figures are
simply left out.

### Everything here describes `i3`, not `i3macs`

Isambard runs two clusters under one slurmdbd, and every project holds an
allocation on both. **The MACS side is unmetered**: no account on `i3macs`
carries a SLURM limit, all its `marketplace-component-usages` rows read `0.0`,
and every invoiced node hour sits under the `Isambard 3` offering — while real
jobs do run there. So MACS use is free and invisible to this tool.

Two consequences worth knowing:

- **Do not sum `node_limit` down `report utilisation`.** Each project appears
  once per service and both rows carry the *same* limit — one credit balance
  shown twice, not two pools. A MACS row reading `0.00` used is not idle
  capacity; it is a cluster nobody is charged for.
- `slurm-jobs` pins `--cluster i3` rather than inheriting the login node's, so a
  capture taken from a MACS node still describes the same machine as the rest of
  the report. Pass `--cluster all` if you want both.

### The cache

There are three states, and only one of them writes anything:

| Command | Reads | Writes |
| --- | --- | --- |
| `snapshot` | the API, in full | a **new** timestamped snapshot |
| `report` | the newest snapshot | nothing |
| `report --live` | the API | nothing |

Snapshots are immutable — `snapshot` never updates one in place, it takes
another. So **refreshing the cache means running `snapshot` again**; there is no
`--update`, because the portal offers no way to ask what changed since last
time (details in [DEVELOPER.md](DEVELOPER.md#the-cache)). `snapshots` lists what
you have and `report --use NAME` reads an older one.

> **Snapshots taken before the paging fix below are wrong, and will now say so.**
> `openportal-allocation-user-usage` was pulled by paging the whole table, which
> that endpoint does not support; the result double-counted usage in every month
> before the pull's last. Any command reading such a snapshot now fails with
> `SnapshotError: ... rows repeat ...`. Take a fresh one, then run
> `report reconcile`: the numbers a good snapshot produces agree with the
> portal's own billing to within a small fraction of a percent.

### Administrative scope

The portal is multi-tenant, and it is inconsistent about it: `allocations`,
`projects` and `users` come back filtered to your own organisation, while
`associations` and `allocation-user-usage` return the whole machine — with the
rows you may not read blanked out rather than omitted. A large fraction of
those association rows typically arrive with no username at all.

So `membership` and `user-usage` filter to the projects you administer, derived
from the allocations your token can actually see — thousands of association
rows across hundreds of project codes on the whole machine reduce to a much
smaller set of user/project pairings across the handful of codes you
administer. Pass `--all` to opt out.

`monthly`, `monthly-totals` and `viz` go one step further and filter to a single
*organisation*, University of Exeter by default: some of the project codes we
administer belong to other, separately funded organisations that share the
same token, so counting them would inflate our own share. `--all` (or
`--customer ''` on `viz`) widens it back to every code we administer.

### As a library

```python
from waldur_tools import WaldurClient, reports, viz
from waldur_tools.cache import Snapshot

with WaldurClient() as client:
    frame = reports.credits(client)          # live
    rows = client.list("openportal-allocations")

snapshot = Snapshot.latest(Path("data"))
frame = reports.queue(snapshot)                        # from a snapshot
everyone = reports.membership(client, scope=False)

months = reports.monthly_totals(snapshot)              # the utilisation series
figure = viz.figure_share(months, nodes=384, share=0.10)   # one plotly Figure
HTML(viz.render(snapshot))                             # the whole page, inline

checked = reports.reconcile(snapshot)          # one word per month, before quoting
```

## What the live data actually looks like

Findings from working against a live deployment, which shaped the reports:

- Every visible allocation appears **once per service** (`Isambard 3` and
  `Isambard 3 Multi Architecture System`), sharing a `groupname` and
  `node_limit`. Joining associations to allocations by URL therefore resolves a
  fraction of the estate; joining by project code resolves all of it. The
  upstream `rse-sharing` example requests each allocation individually and 404s.
- `openportal-allocations.node_usage` is **the current month's usage, not the
  cumulative total** — it matches `current_month_spend` to the penny for every
  project. Nothing in the field name says so.
- `openportal-allocation-user-usage` is the **only endpoint with a time axis** —
  one row per user, allocation and calendar month — so every figure in `viz` is
  built from it. Its `node_usage` *is* safe to sum, unlike the field of the same
  name on allocations.
- That same endpoint **cannot be paged end to end**. It is ordered by
  `(year, month)` and nothing else, so `LIMIT`/`OFFSET` returns some rows two or
  three times and never returns others: thousands of duplicate keys in a pull
  of tens of thousands of rows, which inflated one month's node hours well
  past the true figure. It is fetched a month at a time instead, and both the
  pull and every read are checked for repeated rows. Details in
  [DEVELOPER.md](DEVELOPER.md#one-endpoint-cannot-be-paged-straight-through).
- `invoices.incurred_costs` is **the same node hours by another route**. Every
  usage line on every invoice bills `1.0000000000` credits per hour, and
  `incurred_costs` equals those lines' quantities summed, to the last decimal
  place, on every one of them. That is what `reconcile` cross-checks against —
  and it is the only second opinion with a time axis, since the other pair that
  agrees (`allocations.node_usage` and `current_month_spend`) covers only the
  month in progress. Do not use `price` or `total` for this: they are net of
  the credit lines that zero a grant-funded invoice out, so an invoice can
  bill thousands of node hours and show a `total` near zero.
- Money and usage arrive as decimal *strings*, hence `frames.numeric`.
- `slurm-*`, `events` and `keys` return empty; `support-issues` returns 424.
- Unrecognised query parameters are silently ignored, so an unsupported filter
  is indistinguishable from one that matched everything.
- A full snapshot takes on the order of several minutes and lands tens of thousands of rows.

The portal does not expose an OpenAPI schema (`/api/schema/`, `/api-docs/` and
friends all 404), so the generated client cannot be regenerated against this
deployment — it is pinned from PyPI and may drift from what the portal returns.
That drift is the main reason the raw reader exists as a fallback.

## Development

```bash
pixi run check     # lint + typecheck + test + the browser extension's parity test
```

See [DEVELOPER.md](DEVELOPER.md) for the data model, the join keys, and what
each report does to its inputs, and [web/README.md](web/README.md) for the
browser extension and how its arithmetic is held to this one's.
