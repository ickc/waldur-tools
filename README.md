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

```bash
pixi install -e dev
echo 'export WALDUR_API_TOKEN=<your token>' > .envrc.local   # gitignored
direnv allow
```

The token comes from the portal, under your own account menu. `.envrc` (committed)
sets `WALDUR_API_URL` and `WALDUR_CACHE_DIR` and sources `.envrc.local`
(gitignored, never committed — there is also a pre-commit hook that refuses it).

If you would rather keep the token outside the repository entirely, put it in
`~/.config/waldur/token` or point `WALDUR_TOKEN_FILE` at a file; the environment
variable wins if all are set.

## Use

```bash
pixi run -e dev waldur-tools whoami                  # check auth + config
pixi run -e dev waldur-tools endpoints --counts      # what this deployment exposes
pixi run -e dev waldur-tools snapshot                # pull the report data set
pixi run -e dev waldur-tools report credits
pixi run -e dev waldur-tools report queue -o queue.csv
```

### Reports

| Name | Question it answers |
| --- | --- |
| `credits` | How much of each project's allocation is spent, and how many months of runway remain at the current burn rate? |
| `membership` | Which users have access to which service? (the `rse-sharing` example, as a join rather than a request per row) |
| `utilisation` | Which allocations are sitting idle against their node limit? |
| `user-usage` | Who are the heaviest users by node usage? |
| `queue` | Daily job counts and mean queue wait, per project and resource |

### As a library

```python
from waldur_tools import WaldurClient
from waldur_tools.cache import Snapshot
from waldur_tools import reports

with WaldurClient() as client:
    frame = reports.credits(client)          # live
    rows = client.list("openportal-allocations")

frame = reports.queue(Snapshot.latest(Path("data")))   # from a snapshot
```

## Design notes

The HTTP layer is the official [`waldur-api-client`](https://pypi.org/project/waldur-api-client/),
which owns authentication, the base URL and the `httpx` transport — and which
does ship the Isambard-specific `openportal-*` endpoints.

On top of it `waldur_tools.client` adds a uniform paginated *raw JSON* reader,
because the endpoints carrying the most interesting data (the daily usage
reports) nest free-form payloads that the generated attrs models flatten into
opaque objects. The typed API is still available: pass `WaldurClient.raw` to any
`waldur_api_client.api.*.sync` function.

### What the live data actually looks like

Findings from a full a live snapshot, which shaped the reports:

- `openportal-associations` references **far more** distinct allocations, but only
  **36** are visible via `openportal-allocations`, and fetching an invisible one
  returns 404. The upstream `rse-sharing` example requests each allocation
  individually and so cannot complete against this data; `membership` uses a
  left join and flags unresolved rows via a `resolved` column instead.
- **a substantial fraction of many** associations have a null `username`.
- Money and usage arrive as decimal *strings*, hence `frames.numeric`.
- `slurm-*`, `events` and `keys` return empty; `support-issues` returns 424.
- A full snapshot takes on the order of several minutes and lands tens of thousands of rows.

The portal does not expose an OpenAPI schema (`/api/schema/`, `/api-docs/` and
friends all 404), so the generated client cannot be regenerated against this
deployment — it is pinned from PyPI and may drift from what the portal returns.
That drift is the main reason the raw reader exists as a fallback.

## Development

```bash
pixi run -e dev check     # lint + typecheck + test
```
