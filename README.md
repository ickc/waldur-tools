# waldur-tools

Snapshot, analyse and report on data from the [Waldur](https://docs.waldur.com/)
portal behind [Isambard](https://portal.isambard.ac.uk) — the same API the
`gw4-isambard/rse-sharing` example script talks to, wrapped as a proper package.

## TL;DR

The rest of this README is reference material to come back to. This section is
what a colleague needs before touching anything.

### Three ways to consume it, one picture

Everything here is **read-only** against the portal — no command in this repo
writes anything back to Waldur. That is a property of this code, not of the
token: a Waldur personal access token carries the scopes and roles of the
account that issued it, so what *it* can do is whatever you can do. Treat it
accordingly.

| | How you run it | Token | Who it is for |
| --- | --- | --- | --- |
| **Chrome extension** — [`web/`](web/README.md) | `chrome://extensions` → *Developer mode* → *Load unpacked* on `web/`, open your organisation's dashboard in the portal, press the toolbar button | read out of the portal tab for you | anyone who wants the answer and not the toolchain |
| **CLI** | `pixi run waldur-tools report <name>` | pasted by hand into `.envrc.local` | ad-hoc questions, CSV/JSON/parquet export |
| **HTML report** | `pixi run waldur-tools viz -o utilisation.html` | ditto | one self-contained page you can email |

Run the two Python paths from an Isambard 3 login node where you can: `sacct`
lives there, it is the only source of per-job shape, and `viz` gains three
figures when a capture is present. Neither path requires it.

**The extension is the baseline experience** — nothing installed but the
extension, nothing pasted, built live off the tab you are already signed in to
and cached in the browser. It is an extension rather than a URL you could just
visit because the deployment's CORS allowlist holds exactly one origin, the
portal's own front end, so a hosted page — or one opened from disk — can never
read the API; host permissions are the way around that.

The Python side has had the closer scrutiny, and the two are kept in lock-step:
the Python reports emit a golden fixture and CI fails if the JavaScript
arithmetic drifts off it. If maintaining both ever stops being worth it, the
proposal is to keep the extension and retire the Python side, not the reverse.

### How it was built, and how far to trust it

- **Agentically.** The high-level design and the review are mine; I have not
  read the detailed implementation closely.
- **The uncertainty lives in the API, not in the code.** Getting data back is
  easy; knowing what it means is not, and this deployment publishes no schema.
  Two cases that have already bitten: one endpoint cannot be paged end to end
  (`LIMIT`/`OFFSET` returns some rows two or three times and never returns
  others, which inflated a month's headline), and `node_usage` means "the month
  so far" on one endpoint and a summable monthly figure on another, with nothing
  in the name to say so.
- **So correctness is asserted at the top, not the bottom.** The check that
  counts is agreement with the official Isambard dashboard (portal → your
  organisation → *Dashboard*). BriCS build that from the same API, so matching
  *their* reading of it is the safest evidence available. Two automated
  cross-checks back it up: `report reconcile` puts our node hours beside what
  the portal actually invoiced — the same quantity by a completely different
  route — and every pull and every read rejects repeated rows.
- **The stakes are low.** A summary statistic or a figure being wrong costs us
  little. The corollary matters more than the reassurance: **when a number looks
  surprising, treat it as suspect rather than as a finding** — check it against
  `sacct`, the official dashboard, the affected users, or BriCS staff before
  repeating it.
- **The one thing that is not low stakes is secrets.** Hence the hard rule that
  nothing snapshot-derived — figures, project codes, usernames — goes into git
  (see [CLAUDE.md](CLAUDE.md)), and no token is ever committed. What bounds the
  damage is the hours-long expiry, and that the token is only ever sent to the
  one origin `WALDUR_API_URL` names — not that it is read-only, which it is
  not: a portal token carries its holder's own scopes and roles.

### The number people misread

Every percentage in the report is usage against **our 10% share of Isambard 3 —
38.4 of its 384 nodes, held for every hour of every month**. 100% means "we ran,
on average, exactly the nodes we hold", not a ceiling: nothing reserves those
nodes for us and nothing stops us exceeding them, so months over 100% are real.

### How the extension gets your token, and why that is sound

Pressing the toolbar button injects a small read-only function into the portal
tab (`activeTab`, plus a host permission for the portal front end so a portal
tab that is merely open can be read too). It reads three things: the session
token from `localStorage` under `waldur/auth/token` — the same string the
account menu's *Copy API token* hands you — the API origin, and your
organisation's UUID. The readings sit in a `Map` in the service worker, keyed by
the report tab they were taken for, and are dropped the moment that tab collects
them.

Four properties are what make that sound rather than merely convenient:

- **It does not touch disk.** The token lives in a variable, and only in
  `chrome.storage.session` — gone at browser exit — if you tick *remember*.
  Never `localStorage`.
- **It only goes back where it came from.** The token is sent to an API URL
  inferred from that same tab. When the URL cannot be worked out, the extension
  shows its gate with the URL box blank rather than falling back to a built-in
  default — precisely so one deployment's credential can never be sent to
  another. That is enforced rather than arranged: every request checks the
  origin it is about to be sent to, including the absolute URLs the portal
  itself names in its `Link` headers, and a remembered token is stored with the
  origin it was issued for and dropped rather than reused against a different
  one.
- **It is verified, not assumed.** `waldur/auth/token` is a HomePort internal,
  not a documented API, and a Waldur upgrade may rename it. So the token is put
  to `users/me/` before anything is built, and one the portal rejects demotes
  cleanly to the paste box with the reason on it.
- **It is cheap to lose.** About an hour of life, and a 401 mid-session is
  answered by re-reading the portal tab — whose front end has been refreshing
  the token all along — and retrying once. Not *harmless* to lose: it is your
  own credential with your own roles on it, which is why the hour and the single
  origin are the properties that matter.

Worst case is therefore your own credential with an hour to live, held in
memory, sent to exactly one origin. [web/README.md](web/README.md) has the
detail.

### Where to go next

- [DEVELOPER.md](DEVELOPER.md) — the data model and every derived column with
  its formula. Read it before quoting a number.
- [web/README.md](web/README.md) — the extension, and how its arithmetic is held
  to the Python one's.
- `pixi run waldur-tools report reconcile` — run it before quoting anything
  monthly.

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
chmod 600 .envrc.local                                       # see below
pixi run waldur-tools whoami
```

The token comes from the portal, under your own account menu. `.envrc.local` is
gitignored and never committed; `scripts/activate.sh` (committed, secret-free)
sources it on every `pixi run` and `pixi shell`, along with sensible defaults
for `WALDUR_API_URL` and `WALDUR_CACHE_DIR`.

**The `chmod` matters on a shared machine.** The usual umask leaves a new file
readable by every other account on the box, and this one holds the whole of
your access to the portal. Nothing here can set it for you — the file is yours,
written by hand, and quietly changing the mode of a file we did not create is
not ours to do — so a command that finds it readable by others prints a warning
and carries on. The same goes for `~/.config/waldur/token` and anything
`WALDUR_TOKEN_FILE` points at.

**What the tool writes, it locks itself.** Snapshots (directory `0700`, files
`0600`), the SLURM job capture, exports from `-o`, and the generated
`utilisation.html` are all narrowed to their owner after writing — they carry
an organisation's spend, project names and who ran what, and on a shared login
node the default `0644` publishes that to everyone with an account. On Windows
`chmod` cannot express this and the calls are no-ops.

Windows works the same way from the outside. `scripts/activate.bat` sets those
defaults there, and since cmd.exe cannot source a shell file, the token is read
out of `.envrc.local` by the package itself — so the line above, and rewriting
it when the token expires, is the same on every platform.

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
| `storage` | How full is every project and personal disk quota, fullest first? |
| `storage-monthly` | The same per month, as peak/end/median — what the quota heatmaps draw |

Common options: `--limit N` (`0` for all rows), `--sort COL --sort COL2`
with `--desc`, `-o FILE.csv|.json|.parquet`, and `--all` on `allocations`,
`membership`, `user-usage`, `monthly`, `monthly-totals`, `reconcile`,
`storage` and `storage-monthly` to lift the scope filter described below.

**The node hours in `monthly`, `monthly-totals` and `viz` come off the
invoice.** This deployment bills one credit per node hour, and each invoice
itemises its usage lines by project, so the ledger answers "how many node hours
did this project run in this month?" directly. The reason to prefer it is not
precision but permanence: **when a project's allocation is terminated the portal
stops returning its usage rows entirely** — every month it ever ran, not just
the months since — while the invoices it appeared on stand untouched. A total
summed out of `openportal-allocation-user-usage` therefore shrinks every time a
project finishes, and shrinks *retrospectively*, so last January's figure is
smaller today than it was in January.

The usage endpoint is still the only thing with a user axis, so `user-usage`,
and the *active users* column everywhere else, still come from it — and are a
**lower bound** in any month that had a project since terminated. Node hours in
those months are complete; the head count is not.

**Run `reconcile` before you quote anything.** It puts the two routes side by
side and prints one word per month:

```
month        node_hours   incurred_costs    difference   missing   status
2024-05       30,000.00        15,000.00     15,000.00      0.00   usage high
2024-06       12,000.00        12,000.00          0.00      0.00   ok
2024-07        8,000.00        10,000.00     -2,000.00  2,000.00   project ended
```

(Illustrative — replace with your own snapshot's numbers.) `ok` means the two
sides are within 1% of each other. `usage high` / `usage low` mean the pull is
the first thing to suspect — which is exactly the check that would have caught
the paging bug described below, before an inflated headline could be read as a
finding. `project ended` means the usage side is short by exactly what a
terminated project was billed: nothing needs fixing and no reported figure is
wrong, because the reported figures are the invoice's.

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
| Project quota heatmap | How full each project's shared disk got each month — as % of quota, or as a size |
| Personal quota heatmap | The same per person, switching between home and scratch |

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

### Disk quotas

`openportal-project-storage-reports` is the only endpoint that carries them, and
it carries both halves: a project-wide quota on `projects`, and a per-person one
on `home` and `scratch`. The limits it reports agree with what `lfs quota` says
on the machine itself, so it is a trustworthy source for *what you were given*.

Two things about the shape are worth knowing before reading the figures.

**Disk is a level, not a flow.** Node hours are consumed during a month and sum
over it; a quota reading is a measurement of how full something is right now.
There is nothing to add up, so a month has to be summarised by picking a
statistic — hence the `Peak` / `End` / `Median` buttons, defaulting to peak,
which is the reading that decides whether writes actually failed. A mean is
deliberately not offered: averaging a slowly drifting level is close to
meaningless, and it hides the peak.

Each of the three is picked on its own, so a tooltip reads "*X* of *Y*" only
where one quota held for the whole month. Where it was raised mid-month the
percentage and the size are still both true of the month, but they are measured
against different limits, so the sentence relating them is left off rather than
written with a limit that only half of it divides by.

**Colour is a percentage, not a size.** Quotas are uniform by default but not by
rule — a project or a person can be granted more — so a byte count means a
different thing on every row of the grid, while 100% means the same thing on all
of them: writes fail. On a size ramp whoever was granted the most room would be
painted as the one in the most trouble. Home is also a fraction of the size of
scratch, so those two are never comparable as sizes at all. The ramp is linear
from empty to full and ends in red, because the only part anyone acts on is the
top of that range. Sizes are on every tooltip, and the project figure offers
them as a view.

The daily series behind all this is deliberately *not* shipped: a year of it is
tens of thousands of readings, which is too much to embed in a self-contained
page for a question nobody asks per-day. The monthly reduction is around a
hundred cells and answers the same question.

> **Check `Last read` before quoting any of it.** These readings are only as
> current as the collector behind them, which is a different thing from how
> fresh your snapshot is: the endpoint can go months without a new reading while
> everything else in the pull is same-day. Both figures mark a month that was
> not observed on every day with a `*`, the table gives the date of each
> reading, and the page says so in words when the newest one is more than six
> weeks old.
>
> That is not a hypothetical risk. The collector has already stopped once, in an
> upgrade of the OpenPortal agents that broke it **silently** — the endpoint kept
> answering, kept its schema, and simply stopped gaining months, while every
> other endpoint in the same pull stayed same-day. Nothing in the API
> distinguishes "no new readings" from "nothing to report", and a re-pull of a
> dead collector returns a byte-identical table rather than an error, so the
> staleness check is the only thing standing between you and quoting a disk as
> it stood months ago. `openportal-project-usage-reports` is the useful control:
> it comes from the same agents, so if it is current and storage is not, the
> break is on the collector rather than on your token or your snapshot.

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
- **Terminating a project erases its usage, retrospectively.** Once an
  allocation is terminated, `openportal-allocation-user-usage` returns *no rows
  at all* for that project code — not blanked rows, and not only the months
  since it ended, but its whole history. Its invoices are untouched. This is
  not something the reports can join their way around, and widening the scope to
  include terminated `marketplace-resources` returns identical totals, because
  there is nothing left on the other side to match. It is why the node-hour
  figures are taken off the ledger; see the note above `reconcile`.
- `invoices.incurred_costs` is **the same node hours by another route**. Every
  usage line on every invoice bills `1.0000000000` credits per hour, and
  `incurred_costs` equals those lines' quantities summed, to the last decimal
  place, on every one of them. Each of those lines also carries a `project_uuid`
  and a `project_name`, so the ledger splits by project as well as by month —
  which is what makes it usable as the primary source and not only as a
  cross-check. Do not use `price` or `total` for this: they are net of the
  credit lines that zero a grant-funded invoice out, so an invoice can bill
  thousands of node hours and show a `total` near zero. And beware the credit
  line itself: its `billing_type` has been seen to read `usage`, so the usage
  lines are picked out by `measured_unit == "hours"` and `unit_price == 1`.
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
