# The utilisation report, in a browser

A browser extension that builds the same utilisation report as
`waldur-tools viz`, live, off the portal tab you are already signed in to. No
install of this package, no Python, no snapshot — for the people who want the
answer without the toolchain.

**To use it**, take the zip from the [latest
release](../../../releases?q=web&expanded=true), unpack it, and load the
unpacked folder — `chrome://extensions` → *Developer mode* → *Load unpacked*.
Chrome cannot install a zip directly, which is why it has to be unpacked first.
The plotly bundle is already inside, so this needs neither Python nor a
checkout.

**To work on it**, load `web/` from a checkout instead, after writing the one
file that is generated rather than committed:

```bash
pixi run web-vendor      # writes web/vendor/plotly.min.js, once
```

`pixi run web-pack` builds the release zip out of the same tree, into `dist/`.

**Open your organisation's dashboard in the portal and press the toolbar
button.** That is the whole interaction. The report opens in a new tab and
starts building.

## Nothing to fill in

The three things this used to ask for are all in the tab you pressed the button
from, and `src/portal.js` reads them out of it:

| What | Where it comes from | If that fails |
| --- | --- | --- |
| The API token | `localStorage`, key `waldur/auth/token` | the paste box, as before |
| The API URL | an origin the page has fetched `/api/` from; else the favicon's origin; else the remembered resource filter; else `portal.` → `portal-api.` | a text box |
| The organisation | the UUID in `/organizations/<uuid>/`; else the owner of the project in `/projects/<uuid>/`, through the allocations; else the remembered filter; else the `customer`-scoped entry in `users/me/`; else the largest in scope | the picker, which is always there |

The organisation one is the point. **No institution is named anywhere in this
source.** An RSE at any partner opens *their* organisation's dashboard, presses
the button, and gets *their* figures — nobody has to edit a default and nobody
needs an account at another institution to make it work. The command-line tool
keeps its `--customer` default because it is run from a checkout by whoever
configured it; the browser has a better answer available and uses it.

The two things the portal genuinely cannot answer — how many nodes the machine
has and what share of it your organisation holds — sit on the report itself, as
editable assumptions beside the organisation picker rather than a gate in front
of it. There is no per-organisation entitlement in the API to read them from:
`customer-credits` carries a credit balance but no expected consumption, and
there is no customer quota endpoint. **The 10% default is the GW4 partner share
and may not be yours**; changing either box re-renders from rows already in
hand and costs no request.

## What is read, and how much to trust it

`waldur/auth/token` is a HomePort internal, not a documented API, and a Waldur
upgrade may rename it. So it is never trusted on its own: the token is put to
the API before anything is built, and one the portal rejects falls back to the
paste box with the reason on it — the same box, demoted from front door to
fallback. The portal's account menu still offers *Copy API token* for that path.

**A token read off a portal tab goes only to an API URL inferred from that same
tab.** The URL box carries this deployment's address as its default, and the
inference answers null rather than guessing at a hostname it does not
recognise — so falling back to that default would take one deployment's
credential and send it to another. When the URL cannot be worked out the gate
appears instead, with the token filled in and the URL blank, and the reader says
where it goes.

Reading it needs `activeTab` and `scripting`, plus a host permission for the
portal front end so a portal tab that is merely *open* can be read too — the
button then works from any tab, not only from the portal. `background.js` holds
the readings in memory, keyed by the report tab, and drops them the moment that
tab collects them.

**Tokens live an hour.** A report left open long enough will meet a 401, and
the answer is not to make you fetch a new one: the front end in the portal tab
has been refreshing the token all along, so `WaldurClient` asks the background
to read it again and retries once. A *pasted* token is not retried — there is
nowhere to go back to, and silently substituting another account's token would
be worse than the error.

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

Eight figures, against the ten `waldur-tools viz` produces.

| Figure | Here | Why |
| --- | --- | --- |
| Monthly usage vs share | ✅ | |
| Stacked usage by project | ✅ | |
| Project × month heatmap | ✅ | |
| Total per project | ✅ | |
| Engagement | ✅ | |
| Demand: jobs and queue wait | ✅ | from `openportal-project-usage-reports` |
| Project quota heatmap | ✅ | from `openportal-project-storage-reports` |
| Personal quota heatmap | ✅ | the same endpoint, `home` and `scratch` |
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
the node hours in these figures agree with what the portal billed — and the
table under it says *which months and by how much*, because a summary cannot
tell a known accounting quirk from an unstable pull and the reader usually
can. Every month is listed, agreeing ones included: a gap with nothing beside
it has no scale to be read against.

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
   figure, the storage reports behind the two quota figures, and the
   associations behind the "people with access" denominator. None of them blocks
   anything above it.

Complete months are then kept in IndexedDB. A calendar month that has ended is
not written to again, so a cached copy stays correct indefinitely; the month in
progress is never cached. A second visit refetches one month instead of twenty.

Because the usage endpoint is **not** scoped to an organisation, changing the
organisation, the node count or the share re-renders from rows already in hand
and costs no request at all.

### Two things worth measuring, measured

Both were open questions about speed rather than about the design. Both have
now been put to the live deployment, and neither changed it:

- **`page_size` is capped at 300.** Ask for 1000 or 5000 and 300 comes back,
  with no error and no indication that the request was trimmed — the same
  silence every other unrecognised parameter gets. So the ceiling is real but
  modest: raising the page size from 200 buys about a third fewer requests, not
  the five-fold cut a cap of 1000 would have. `DEFAULT_PAGE_SIZE` stays at 200,
  which is what the Python client uses, so the two tools page identically and a
  discrepancy between them is never the page size.
- **`?field=` does not work.** The usage rows come back carrying every column —
  `full_name`, `username`, `allocation`, `user` and the rest — whatever is asked
  for. The parameter is dropped like any other the filter backend does not know,
  so there is no payload to save here. `o=` and `ordering=` go the same way,
  which matters more than the size of the response: **the ordering cannot be
  made total from the client side**, and that is why slicing by month is the
  only fix available for the usage table.

The measurement itself is the lesson. Waldur's DRF filters **silently ignore
parameters they do not recognise**, so `page_size=1000` returning 300 rows and
`field=allocation` returning every field are both indistinguishable from
success unless you look at what came back. Nothing that looks like a filter may
be assumed to have applied; it has to be checked against `X-Result-Count` or
against the keys on a row.

### Associations is not totally ordered either

`openportal-associations` backs the "people with access" denominator on one
tile, and it is pulled straight through because it has no time axis to slice
on. Against the live deployment it turns out to page as badly as the usage
table does, only in miniature: **the row count agrees on every attempt while a
row or two comes back twice and as many never arrive**, and how many varies
with `page_size` — which is what makes it a paging artefact and not duplicate
data. `o=` and `ordering=` being ignored means there is nothing to reach for.

So `list()` takes an optional `rowKeys`, and a repeat is fatal by default. The
associations pull is the one caller that opts out of that, via `onRepeats`: the
error moves one denominator on one tile by a row or two, and losing the tile
over it would be the worse trade. **The tile says so itself when it happens**,
rather than quietly showing a number that is one short.

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
- the months summing to the unfiltered total for the table as a whole;
- and `X-Result-Count` being *readable at all*. Every guard above is arithmetic
  against that header, and it crosses an origin only while the deployment names
  it in `access-control-expose-headers`. Were it to stop doing so, `count()`
  would answer null, null would read as an empty month, and the report would
  draw itself out of no rows without an error anywhere. So a missing header is
  an error in its own right.

The last of those doubles as the cache's staleness check: if the portal backfills
an old month, the totals stop agreeing, the cache is dropped and the pull runs
again. Staleness is caught by arithmetic rather than by an expiry someone guessed.

## The token

Held in a variable, sent to the portal and to nothing else. On the ordinary path
it is read from the portal tab at the moment you press the button and is never
written down at all. On the paste path, ticking *keep until this browser closes*
puts it in `chrome.storage.session`, which lives in memory and is dropped on
browser exit — never `localStorage`, which would leave a token on disk long
after the portal expired it. **Portal tokens expire within the hour.** *Clear
cached data* removes both the token and the cached months.

## Tests

```bash
pixi run test-web       # runs pytest first, then all five files below
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

**`tests/background.test.mjs` — the handover, which no browser tool can see.**
An extension's own pages are invisible to every browser-automation tool going,
so "load it and click" is the only manual check and it is a slow one. The click
handler is therefore driven against a fake `chrome`. The case it exists for is a
race: `tabs.create` resolves with the new tab and only *then* can the readings
be filed under its id, while the page in that tab has been loading the whole
time and may ask first. Losing that race drops the reader on the paste form for
no reason, on some runs and not others. So a request that arrives early is
parked rather than refused, and a report tab nobody is bringing readings for —
one opened by typing its URL — is answered by a timeout instead.

**`tests/portal.test.mjs` — the inference nobody can exercise by hand.** The
API-URL and organisation routes above each exist for a case the developer's own
account cannot reach: an unconventional hostname, a page with no UUID in it, a
reader at another institution. Those paths are checked here against readings
written out by hand, with fabricated UUIDs and a `.test` hostname.

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
manifest.json          MV3: host permissions, activeTab + scripting, one action
src/background.js      reads the portal tab, then opens the report knowing
src/portal.js          what to read off the portal, and how far to trust it
src/report.html/.css   the page shell
src/report.js          the orchestrator: handover, fetch order, progressive render
src/api.js             paging, and the guards that make the totals trustworthy
src/store.js           the IndexedDB month cache
src/reports.js         the analyses — the file the parity test pins
src/figures.js         the plotly figures
src/palette.js         colour, and the light/dark swap
src/page.js            prose, tiles, table views
tests/*.test.mjs       parity with the Python, the paging guards, the portal
                       inference, the figure rules
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

Nothing to configure for another *organisation* on this deployment — that is the
whole design above. For another *deployment*, the API URL is inferred from
whatever portal you press the button on, and `activeTab` covers reading that tab
whichever host it is. The API host itself is covered by
`optional_host_permissions`, so the extension stays useful against a second
deployment without claiming access to every site at install time.

That permission can only be *asked for* from a click, and the automatic path is
not one — a `permissions.request` on page load is refused. So the first visit to
another deployment stops at the gate, with the token filled in and the reason on
it, and pressing its button raises the prompt. One click, once: afterwards the
permission is held and the automatic path runs, token re-read from the tab and
401s retried as usual. The one loss is that the first build goes through the
paste path, which cannot renew an expired token; a reload after granting picks
the automatic path back up.

The open-tab fallback is narrower than this and stays so. `tabs.query` can only
see URLs the extension holds a permission for, and `host_permissions` names one
deployment — so on another Waldur the portal has to be the *active* tab, which
`activeTab` covers. Reaching an already-open tab on any host would mean the
`tabs` permission over every site, which is far more than the convenience is
worth.
