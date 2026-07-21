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
pixi run waldur-tools report membership --limit 0            # every row
pixi run waldur-tools report queue --sort num_jobs --desc -o queue.csv
```

### Reports

| Name | Question it answers |
| --- | --- |
| `credits` | How much of each project's allocation is spent, and how many months of runway remain at the current burn rate? |
| `membership` | Which users have access to which project, with their real names and emails? (the `rse-sharing` example, as a join rather than a request per row) |
| `utilisation` | Which allocations went unused *this month* against their node limit? |
| `user-usage` | Who are the heaviest users by cumulative node usage? |
| `queue` | Daily job counts and mean queue wait, per project and resource |

Common options: `--limit N` (`0` for all rows), `--sort COL --sort COL2`
with `--desc`, `-o FILE.csv|.json|.parquet`, and `--all` on `membership` and
`user-usage` to lift the scope filter described below.

The reports are thin: they select and rename columns from one or two endpoints
and derive a handful of arithmetic ones. **[DEVELOPER.md](DEVELOPER.md) lists
every derived column with its formula** — read it before quoting a number,
particularly for `months_remaining` and `month_vs_limit_pct`, both of which mean
something narrower than their names suggest.

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

### As a library

```python
from waldur_tools import WaldurClient, reports
from waldur_tools.cache import Snapshot

with WaldurClient() as client:
    frame = reports.credits(client)          # live
    rows = client.list("openportal-allocations")

frame = reports.queue(Snapshot.latest(Path("data")))   # from a snapshot
everyone = reports.membership(client, scope=False)
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
pixi run check     # lint + typecheck + test
```

See [DEVELOPER.md](DEVELOPER.md) for the data model, the join keys, and what
each report does to its inputs.
