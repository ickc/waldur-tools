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
4. **One endpoint is fetched a month at a time**, because paging it end to end
   returns some rows twice and drops others. Nothing about the *numbers* is
   changed; the pull is. See
   [One endpoint cannot be paged straight through](#one-endpoint-cannot-be-paged-straight-through).
5. **`reconcile` compares two endpoints that should agree**, and is the only
   report whose output is a verdict rather than a table of the portal's own
   figures. Run it before quoting anything derived from the usage endpoint.

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
| `invoices` | small | yours only — one per month, one customer |

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

**Above 100% is not an error, but check the pull first.** The share is an
accounting entitlement, not a quota, and nothing on the machine enforces it —
see [What actually limits us](#what-actually-limits-us) below — so a month over
100% is possible. It is
also what a duplicated pull looks like, and that is what the months over 100%
in the snapshots taken before the paging fix actually were: see
[One endpoint cannot be paged straight through](#one-endpoint-cannot-be-paged-straight-through).
Before quoting a figure above 100%, run `report reconcile`, which does
that cross-check for you against the invoice the portal's own organisation
dashboard bills off.

This is the opposite of `utilisation.month_vs_limit_pct`, which *cannot* exceed
100% because its denominator is a node limit that tracks the remaining balance.

### What actually limits us

An earlier version of this document and of the report said months over 100%
were fair share letting us borrow idle capacity. That was wrong, and it was
wrong in a way worth recording, because it is the obvious guess. Check it on a
login node rather than assuming either way:

```console
$ scontrol show config | grep -iE 'Priority(Type|Weight|Decay|UsageReset)'
$ sacctmgr show assoc account=brics.<code> format=Account,GrpTRESMins
$ scontrol show config | grep -i AccountingStorageEnforce
```

On the deployment this package was written against, **every priority weight is
zero** — `FairShare`, `Age`, `Assoc`, `JobSize`, `Partition`, `QOS` alike. The
multifactor plugin is loaded and `sshare` prints a full fair-share tree, and
none of it reaches a job's priority: every job scores the same, so the queue is
first come, first served under `sched/backfill`. No account is penalised for
running over its share or favoured for running under it. There is no borrowing,
because there is nothing to borrow from.

**What is enforced is per project, not per organisation.** Each project's SLURM
account carries a `GrpTRESMins`, and an `AccountingStorageEnforce` including
`limits` and `safe` makes SLURM refuse to start a job that would exceed it. That
limit is the portal's `limits.node`, converted: `GrpTRESMins.cpu` is in
cpu-minutes, so dividing by `cores_per_node × 60` gives node hours, and the
result matches `limits.node` on the marketplace resource — and
`openportal-allocations.node_limit` is that figure truncated to an integer.
`sinfo -o '%c'` gives the core count. So the chain is: credits granted in Waldur
→ `limits.node` on the marketplace resource → `GrpTRESMins` on `brics.<code>` →
a job that will not start.

Two consequences for reading the report:

- **The organisation's share is an accounting construct with no scheduler
  behind it.** We exceed it in a month by having work to run when the machine is
  idle, and we fall short of it by not having work — not by being throttled.
  There is no organisation-level account to cap, either: the project accounts
  hang directly off the root.
- **A project's own limit is a burn-down of its whole award, not a monthly
  ration.** SLURM's usage counter does reset monthly here
  (`PriorityDecayHalfLife = 0` with `PriorityUsageResetPeriod = MONTHLY`), and
  the portal re-pushes a decremented limit on each sync, which is how a lifetime
  balance is implemented on top of a monthly-resetting counter. This is why
  `viz` shows no cumulative shortfall figure: there is no running balance of the
  organisation's share to be behind on.

#### MACS is unmetered, and the limit is `i3` only

Every project appears twice in `openportal-allocations`, once per service, and
both rows carry the *same* `node_limit`. That is a mirror of one credit balance,
not two pools — and only the `Isambard 3` side is enforced:

```console
$ sacctmgr show assoc cluster=i3,i3macs format=Cluster,Account,GrpTRESMins
```

**No account on `i3macs` carries a `GrpTRESMins` at all** — not one, on either
cluster's account list. `i3macs` is a separate, much smaller cluster in
slurmdbd (`sacctmgr show cluster` prints both with their TRES). Jobs do run
there, and Waldur bills none of it: every invoiced node hour sits under the
`Isambard 3` offering, while the `marketplace-component-usages` rows for the
MACS offering all read `0.0`.

So MACS is free at the point of use, and every figure in this package describes
`i3` alone. `openportal-allocation-user-usage` cannot see MACS either: its
`username` is `<user>.<code>.<cluster>` but the third field is `brics` on every
row, so there is no cluster axis in it to filter on.

### `allocations` — from `openportal-accounting-summary` + `openportal-allocations`

The denominator behind the *% of own allocation* view on the project heatmap.
Awards in this estate span more than two orders of magnitude, so measuring every
project against the same organisational share tells you only which project is
bigger; measuring each against its own award tells you which is being used.

`mean_monthly_allocation = total_credits / award_months`, with `award_months`
the calendar months from `start_date` to `end_date` inclusive. `end_date` is
null for open-ended projects, which are measured to the snapshot date instead.

The construction is ours, not the portal's — credits are granted as a lump for
a period and no monthly figure exists anywhere in the API. Four limits, in
descending order of how much they matter:

| Limit | Effect |
| --- | --- |
| **Top-ups are back-dated.** `total_credits` is the award *as it stands now*, and credits get added to live projects. There is no grant-history endpoint — no `created` on the credit, no order log in the snapshot — so an extension granted late in the award is spread over the months before it as well. | The denominator exceeds what those months were actually funded at, so a topped-up project's early months read **quieter** than they were — a project that doubled its award half way through shows its early months at half their true share. The largest known error, and it only ever understates. |
| **It is not a cap.** The enforced ceiling is the whole award, not a month of it, so a project may legitimately burn a year of credits in a fortnight. | Values well over 100% are normal, not errors. The view is deliberately unbounded. |
| **`start_date` is portal setup, not first job.** The first job typically lands well after the project is created. | Slightly understates the rate of projects that started slowly. |
| **Zero credits yields null, not zero.** Internal and workshop projects hold none. | They drop out of the relative view rather than reading as infinitely over budget. |

The join is on `project_uuid` and **not** on `project_name`, because the name is
not unique: an estate can carry two accounting rows sharing a name under
different UUIDs, where only one is a real provisioned project — holding the
credits, the allocations and the row in `projects` — and the other has zero
credits and no allocation at all. A name join could pick either. Check with
`report allocations` against your own snapshot before assuming names are unique.

This is **not** the two-services duplication. A project's `Isambard 3` and
`Isambard 3 Multi Architecture System` allocations share one `project_uuid`;
that duplication is handled in `membership` and `in_scope` by keying on
`project_code`.

#### Sum the awarded rate per month, not across all projects

`viz.committed` sums `mean_monthly_allocation` only over projects whose award
window covers the month in question. Summing every project regardless of dates
treats awards that never overlapped as concurrent, and inflates the total the
moment one project's window closes. The two agree for as long as no award has
expired yet, and that agreement is a coincidence rather than a licence to take
the shortcut.

#### What the awarded rate shows, and why the report draws it

Three quantities, all in node hours a month, all printed by the tool rather than
recorded here:

| | Where it comes from |
| --- | --- |
| Our share | `nodes * share * 24 * days_in_month` |
| Awarded to projects | `viz.committed`, summed over live awards |
| Actually used | `report monthly-totals` |

The order they come in is the finding, and on this estate it runs
share > used > awarded. Usage sitting *above* the awarded rate means projects
already run harder than their award periods pace them for — several are past
their award outright, which the portal permits because `node_limit` never goes
negative.

An earlier draft of this file read the gap between awarded and share as "no
amount of encouraging existing projects to run harder reaches it". That was
wrong, and in an interesting direction. Utilisation is bounded by **how much
credit reaches a project**, not by how hard the awarded projects work, and — see
below — not by any shortage of credit either. The headline figure draws both
lines so the two questions do not get confused.

### The customer-level credit fields, which are easy to miss

`customers` carries the only two organisation-level quantities in the whole API,
and they are worth more than most of the per-project data:

| Field | Meaning |
| --- | --- |
| `customer_credit` | credit the organisation holds |
| `customer_unallocated_credit` | the part of it assigned to no project |
| the difference | credit handed down to projects |

**The difference reconciles exactly**, which is what makes the two fields
trustworthy enough to quote. Sum the `limits.node` of the live (`OK`-state)
`Isambard 3` marketplace resources, add the credits of any project holding a
balance with no provisioned resource, and you land on
`customer_credit - customer_unallocated_credit` to the penny.
`viz.credit_position` is the accessor; verify it against your own snapshot
rather than taking this on trust.

Since a project's limit *is* its remaining balance, the allocated side is net of
spend — which makes `customer_credit` a remaining figure too, not a lifetime
grant.

The consequence is the one number most likely to be missed: an organisation can
hold a large unallocated balance while its utilisation looks poor, because
unallocated credit reaches no project, and a project is the only thing that can
spend it. An earlier draft of the report argued the headline percentage could
not pass 100% because the credit was not there to pay for it. Whether that is
true is a question about `customer_unallocated_credit`, and on this estate the
answer was no.

That makes the chain of constraints, in order of how much each actually binds:

1. **Credit not allocated to projects** — `customer_unallocated_credit`, shown
   as its own tile and expressed in months of the share.
2. **Credit allocated but paced over long award periods** — the awarded rate
   (`viz.committed`).
3. **Projects having work to run** — where usage exceeds the awarded rate, this
   binds least of the three.
4. **The scheduler** — does not bind at all; see
   [What actually limits us](#what-actually-limits-us).
### `reconcile` — from `openportal-allocation-user-usage` + `invoices`

The one report that checks the data rather than presenting it. Every other
report here trusts the pull; this one asks whether the pull adds up.

**Why it can.** `invoices.incurred_costs` is a running total in credits, and on
this deployment a credit *is* a node hour: every usage line on every invoice
in a snapshot carries `unit_price` exactly `1.0000000000` and
`measured_unit` `hours`, and `incurred_costs` equals the sum of those lines'
`quantity` to the last decimal place on every one of them. Billing rolls the
same node hours up through the marketplace resources, not through
`openportal-allocation-user-usage`, so the two figures are independent
measurements — which is the whole value of the comparison. The only other
overlap in this API is `openportal-allocations.node_usage` against
`accounting-summary.current_month_spend`, and those agree to the penny for the
month in progress and say nothing about any earlier one. `invoices` is the only
second opinion with a time axis.

**Use `incurred_costs`, never `price` or `total`.** Those are net of the credit
lines the portal writes to zero a grant-funded invoice out, and unevenly so —
an invoice can bill several thousand node hours and show a `total` anywhere
from a few hundred down to a fraction of a penny, depending on how much of it
a credit line cancels.

| Column | Derivation |
| --- | --- |
| `node_hours` | `sum(node_usage)` for the customer's projects that month — the same figure `monthly-totals` prints, from `reports._monthly_rows` |
| `incurred_costs` | `sum(incurred_costs)` over that month's invoices for the same customer, matched on the name inside `customer_details` |
| `difference` | `node_hours - incurred_costs`, **null** when a month is missing from one side entirely |
| `pct_difference` | `100 * difference / incurred_costs`, null on a zero invoice |
| `status` | see below; the comparison reads a missing side as zero, so a month nobody used and nobody billed is `ok` rather than a flag |
| `invoice_state` | the invoice's own `state` (`created`, `pending`), distinct values comma-joined |
| `is_partial` | the month the snapshot was taken in, as in `monthly-totals` |

`status` is `ok` when the two sides are within `reports.RECONCILE_TOLERANCE`
(1%) of each other or within `reports.RECONCILE_FLOOR` (2.0 node hours),
whichever allowance is larger; `usage high` or `usage low` when they are not;
`no invoice` for usage in a month the portal has not invoiced, and `no usage`
for an invoice with no usage rows behind it.

Both thresholds are set to catch a broken pull rather than to audit rounding.
The two sides are rolled up differently — the usage endpoint rounds each
user-month to two decimals, the invoice keeps ten — and on a correctly pulled
snapshot every complete month still agrees to a small fraction of a percent:
well under a node hour of drift on totals in the tens of thousands. The
absolute floor exists because an invoice can carry a project whose allocation
this token cannot see (a project can appear on an invoice with no visible
allocation behind it, for a small handful of node hours in some months), and
that lands as a fixed gap rather than a proportional one.

**What it would have caught.** Against a pre-fix snapshot — one of the
end-to-end pulls that put one month well over 100% of our share — most months
read `usage low` or `usage high` rather than `ok`: a month can be undercounted
by more than half, or overcounted by several times over, depending on which
pages the database happened to skip or repeat that request.

Only the months at the tail of the walk tend to agree, which is the signature
of unstable `LIMIT`/`OFFSET` paging: rows repeat and vanish across page
boundaries everywhere except at the tail. The damage runs in *both*
directions — a `usage low` month is one whose rows went to some other page and
never came back — and it does not cancel out in aggregate: a snapshot's worth
of months, summed, can read well above what the invoices total.

Re-running an end-to-end pull does not reproduce the same wrong numbers
either, and that is the second thing reconcile shows you: every end-to-end
pull shuffles differently, so a month that was overcounted one way in one pull
is overcounted a different way in the next. A number that moves between pulls
of the same finished month cannot be checked by looking at it — only against
something outside itself.

**De-duplicating would not have repaired it, which is why `cache.check()`
refuses to.** Dropping the repeated keys from a bad pull and running this
report again narrows most months but still leaves several under-billed. The
duplicates were the symptom; the rows that never arrived are the damage, and
no amount of tidying brings them back.

**Scope.** `reconcile` compares one organisation at a time, because an invoice
belongs to one. `--all` (`scope=False`, `customer=None`) drops that filter from
both sides at once and they stop corresponding: usage arrives for other
organisations' projects whose invoices go to their own organisations and are
not in this snapshot, so widening the scope reads `usage high` by however much
of that other, correctly billed work fell in scope. Under `--all` the report
is a description, not a check.

**What it cannot see.** A month where both sides are wrong the same way, and
anything that is not node hours. It also cannot attribute a mismatch to a
project on its own: invoice items *do* carry `project_uuid` and reconcile
per project to the same tolerance, so the drill-down is a join away, but the
month-level check is what catches a bad pull and the report stops there.

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

### `storage` and `storage-monthly` — from `openportal-project-storage-reports`

The other report that reshapes, and the fiddlier of the two. Each source row is
a project/resource/month carrying a `report` blob, and **the blob comes in two
shapes**: a finished month has a `daily_reports` dictionary keyed by date, while
the month in progress has no such key at all and carries only the top-level
snapshot. Both are read. That is not belt-and-braces — the top-level snapshot of
a finished month is generated *after* its last daily entry, so it is the only
place the last day of any month is ever reported, and it is the sole reading the
open month has.

Two more properties of the source that the parsing has to respect:

- **A day is sampled more than once.** The same filesystem is reported under
  every `resource` the project holds, by collectors running minutes apart, and
  their figures legitimately differ by whatever was written in between. These
  are repeat measurements of one quantity — storage belongs to the filesystem,
  not to the cluster — so they are kept as separate samples rather than
  deduplicated. Collapsing them would halve the evidence and make `peak` one
  collector's opinion of the peak.
- **Sizes are 1024-based.** The collector writes `"100.00 GB"` for the quota
  `lfs quota -h` calls `100G`, so its "GB" is a GiB. Only the absolute views
  depend on that reading; `fill_pct` divides two figures carrying the same unit.

`storage_samples()` is the shared tidy frame both reports and both figures are
built from — one row per scope, filesystem and sample. It takes the same
`scope` and `customer` filters as the monthly reports, and the visual report
passes its own `customer` down: the endpoint answers for every project the
token administers, which spans more than one organisation, and a page headed by
one customer's name must not draw another's disks. `storage_now()` and
`storage_by_month()` are the two aggregations over an already-parsed frame, so
a caller wanting both — the visual report wants the heatmap and the table under
it — pays for one read of the endpoint rather than two, and cannot end up with
a figure and a table describing different pulls.

| Column | Derivation |
| --- | --- |
| `usage_bytes`, `limit_bytes` | the blob's size strings parsed to bytes; **null** if the string is not a size, e.g. an unlimited quota |
| `fill_pct` | `100 * usage_bytes / limit_bytes`, **null** rather than a division when the limit is missing or zero |
| `kind` | `project` for the `projects` quota, `user` for `home` and `scratch` |

`storage` then keeps the newest sample per quota, sorted fullest first.
`storage-monthly` reduces each quota to one row per month:

| Column | Derivation |
| --- | --- |
| `peak_*` | the maximum over every sample in the month — the reading that decides whether writes failed |
| `end_*` | the last sample by `generated_at` — the level carried into the next month |
| `median_*` | the median over every sample, robust to one day's spike |
| `limit_bytes` | the quota **every** sample in the month agreed on; **null** when they did not |
| `days_observed` | distinct dates with a reading, which is **not** the sample count |
| `samples` | readings behind the row, normally two per day |
| `is_partial` | `days_observed < days in the calendar month` |

`limit_bytes` is stricter than "the last limit read", and that is what lets the
tooltip write a cell as "*X* of *Y*". The three statistics are each chosen
independently — the peak fill and the peak size can be different readings, and
the medians are interpolated between two — so they describe one reading only
while a single quota holds all month. While it does, that costs nothing:
`fill_pct` is then a fixed multiple of `usage_bytes`, and both the maximum and
the median carry straight through it, so `peak_fill_pct` really is `peak_bytes`
over that limit. The moment the quota moves, a peak of 90% and a peak size of
1.5 TB can sit beside a limit of 2 TB and none of the arithmetic works. A null
is how the figures are told to state the two figures and leave the relation
between them out.

**`is_partial` means something different here** from the column of the same name
on `monthly-totals`, where it marks the month the snapshot was taken in. Storage
readings lag their own collector rather than the snapshot, so the snapshot date
says nothing about whether a storage month is complete: collection can start
mid-month, stop mid-month, or drop a day. A mean is deliberately not among the
statistics — averaging a slowly drifting level is the mean of a random walk, and
it hides the peak, which the median already covers without doing so.

A snapshot taken before this endpoint was pulled simply has no file for it, and
every entry point here returns an empty frame rather than raising, so an old
snapshot loses two figures instead of the whole report.

#### The collector fails silently, and has

This endpoint is the only one here whose freshness is independent of the pull,
and the failure mode is worth stating because it has actually happened: an
upgrade of the OpenPortal agents stopped the storage collector without
surfacing anything. The endpoint went on answering, kept its schema and its
existing rows, and merely stopped gaining months — so a re-pull returns a table
byte-identical to the previous one rather than an error, and there is no field
that separates "the collector is dead" from "there was nothing to report".

Two consequences for anyone reading this code:

- **A stale month is left stale rather than repaired.** The last month the
  collector touched is frozen in the *month in progress* shape described above —
  it never gains a `daily_reports` dictionary and its top-level `generated_at`
  never advances. That is indistinguishable, structurally, from a month that is
  legitimately still open, which is exactly why `_storage_staleness()` keys off
  the newest reading's age rather than off the blob's shape.
- **`openportal-project-usage-reports` is the control.** It is fed by the same
  agents, so comparing the two separates a broken collector from a broken pull:
  both stale means suspect the snapshot or the token, usage current and storage
  stale means the collector, and that distinction is the first thing to
  establish before raising it with the portal team.

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
  node hours, % of share, % of the month, % of own allocation.
- **A control changes one aspect of one figure; it never stands in for a second
  figure.** Buttons swap the measure or the binning of the same graph and the
  slider on the job-size figure moves the month. Two unrelated series do not get
  bundled behind a toggle just because both happen to be monthly — that is a
  table of contents pretending to be a chart. This is why the demand section is
  three figures rather than one with five buttons.
- **Both bars are in the same unit or they do not share a figure.** The
  job-size figure plots share-of-jobs against share-of-node-hours, both as
  percentages of their own total, precisely so a count and a sum can be read
  side by side without a second axis.
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
- **The heatmap is log-scaled, and so is the per-project totals bar.** A month
  of production is three orders of magnitude above a test job, and on a linear
  ramp everything but the peak renders as empty. Heatmap colour is
  `log10(1 + node_hours)`, relabelled `0, 10, 100, 1k, 10k`; the totals bar uses
  a log *axis*, relabelled the same way. A log axis cannot place a zero, so
  projects that never ran are pinned at `viz.FLOOR_NODE_HOURS` and labelled `0`
  — dropping them would remove the whole point of that figure, and the hover
  reads the true value back out of `customdata`.
- **Queue waits are plotted as `log10(hours)`, not on a log axis.** Plotly fits
  a violin's kernel density in the axis's own coordinates, so a linear fit
  collapses the four decades of sub-second starts into a line. The transform is
  applied to the data and the ticks are written back in hours and days.
  A large share of jobs start within a second, so zero is floored at
  `viz.FLOOR_WAIT_HOURS` (one minute) to keep that spike drawable.
- **Sequential where the question is magnitude, categorical where it is
  identity.** The heatmap and the per-project totals bar are one hue; only the
  stacked figure and the engagement lines tell series apart.
- **A bounded fraction is linear and ends in red; an unbounded magnitude is
  logarithmic and stays one hue.** The quota heatmaps are the exception to the
  rule above them, and deliberately so: a fill percentage runs from empty to
  full and the whole decision lives at the top of that range, whereas node hours
  have no ceiling and span three decades. So the quota figures colour
  `min(fill_pct, viz.FILL_CEILING)` on a linear 0–100 ramp
  (`RAMP_FILL_LIGHT`/`RAMP_FILL_DARK`) that leaves the blues around two thirds
  and finishes through amber into red. Every hex in it still comes from `SERIES`
  or `CHROME`, so the theme swap needs no new pairs. **The rule applies within a
  figure, not to it**: the same heatmap's *size* views are `log10` bytes with no
  ceiling, so its buttons switch the ramp back to the activity blues along with
  the z values. Leaving them on the quota ramp would paint a large project red
  for being large.
- **Ramps are named, not assumed.** A trace carries `meta={"ramp": <name>}` and
  the repaint looks the name up, because there is now more than one ramp on the
  page and a repaint that assumed a single one would hand the quota figures the
  activity blues on every theme switch. A button's own arguments are fixed when
  the page is written, so they can only carry the *light* form of whichever ramp
  they select; `fixRamps()` listens for `plotly_restyle` and puts the scale back
  to what the trace's name and the reader's theme say between them. It keys on
  the last ramp it applied per trace, so re-asserting one cannot loop.
- **Where controls would need two dimensions, the row is flattened rather than
  stacked.** Plotly's button groups do not compose: a second row of buttons
  issues its own `update` and silently resets the first, so `Scratch` followed
  by `Median` would land on `Home · median`. The quota figures therefore carry
  one flat row naming each combination outright. What does not fit in six
  buttons goes to the tooltip instead — which is why sizes are on every quota
  cell's hover, and why the per-person figure spends its buttons on the
  filesystem while the per-project one spends them on absolute size.

### Supporting series

Two things the monthly reports do not carry:

| Function | Source | Why |
| --- | --- | --- |
| `projects_existing` | `projects.created` | The denominator behind "how many of the projects that existed ran something". Without it the active-project line reads as a plateau instead of a fraction. Returns empty rather than raising if `projects` is missing from an older snapshot, so the other six figures still render. |
| `people_with_access` | `reports.membership` | The denominator behind "how many of the people with access ran something". Access granted and never exercised is invisible in the usage endpoint, which only knows about people who ran. |
| `queue_monthly` | `reports.queue` | Rolls the daily queue report up to months. `mean_wait_hours` is total wait over total jobs, **not** the mean of daily means — a day with three jobs should not weigh as much as a day with three thousand. |
| `reports.allocations` | `openportal-accounting-summary` | The denominator behind *% of own allocation*. See [`allocations`](#allocations--from-openportal-accounting-summary--openportal-allocations). |

### The one source that is not the portal

`waldur_tools.slurm` shells out to `sacct`. Everything else in this package
reads the API; this does not, and the separation is deliberate.

**Why it has to exist.** The portal's finest-grained view of job activity is
`openportal-project-usage-reports`, whose blob nests a dictionary per *day*
carrying `num_jobs`, `total_wait_seconds` and consumed resource-seconds per
user. There is no record of an individual job anywhere in the API, and in
particular no record of what a job **asked for** — the `--nodes` and `--time`
in the batch script. Those are the two numbers the scheduler actually acts on,
so without them "why did this wait?" is unanswerable. `sacct` has them as
`ReqNodes` and `TimelimitRaw`.

**How it is kept from infecting the rest.** A separate command
(`waldur-tools slurm-jobs`) writes a separate file (`slurm-jobs.parquet` in the
cache root, `slurm.JOBS_FILENAME`), which `viz` picks up if present and ignores
if not. It is **not** written into a snapshot directory: a snapshot is an
immutable record of what the portal said at one instant, and this is a
re-derivable local capture of something the portal never said. Re-running
overwrites it, which is safe because `sacct` keeps the history and a later
capture is a superset.

Three details in `slurm.parse` that are decisions rather than plumbing:

- **`TimelimitRaw` and `ElapsedRaw`, not `Timelimit` and `Elapsed`.** The
  formatted variants are `DD-HH:MM:SS` with the day part present only when
  non-zero; the raw ones are plain minutes and plain seconds, so no parser can
  get the ambiguous cases wrong.
- **A job that never started has a null wait, not a zero one.** It did not wait
  no time at all; it has no wait to report. Same for `Partition_Limit` in
  `TimelimitRaw` — that is the absence of a request, and substituting the
  partition limit would invent one the user did not make.
- **`State` is truncated at the first space.** The twenty-odd distinct
  `CANCELLED by <uid>` values are one outcome; the uid is who pressed the
  button, which is not a property of the job.

`sacct -a` returns every account's jobs on this deployment, which is what makes
an organisation-wide report possible from an ordinary user account. If a site
disabled that, `capture` would silently return only the caller's own jobs.

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

### One endpoint cannot be paged straight through

`page`/`page_size` is `LIMIT`/`OFFSET` underneath, and that only enumerates a
table once if the query is **totally** ordered.
`openportal-allocation-user-usage` is ordered by `(year, month)` and nothing
else, so within a month the database returns rows in whatever order suits it —
a different one per request. Walking the whole table therefore hands back some
rows two or three times and never shows others.

It is not subtle. A pull of all tens of thousands of rows contained thousands
of duplicate `(allocation, user, year, month)` keys, most of them straddling
two adjacent pages, and correspondingly a comparable number of rows that never
appeared at all. Summed into a monthly total:

| Node hours | paged end to end | pulled month by month | portal dashboard |
| --- | --- | --- | --- |
| Month A | over 100% | under 100% | — |
| Month B | over 100% | under 100% | matches |
| Month C | **well over 100%** | **under 100%** | **matches** |
| Month D | matches | matches | matches |

The middle column is right and the left one is not, which is where the
inflated headline came from. The figure was also *irreproducible* —
every pull shuffled differently — which is the tell for this class of bug.

So `cache.BY_MONTH` routes that endpoint through
`WaldurClient.iter_list_by_month()`, which walks `(year, month)` from
`client.EARLIEST_MONTH` to today and pulls each month as its own filtered
query. Filtering shrinks the queryset to something the server enumerates
consistently: month-at-a-time pulls come back duplicate-free and match the
portal's own dashboard to within rounding. `cache.fetch()` is the single place
that chooses, so `snapshot` and `report --live` cannot drift apart.

Four guards, because every failure here is silent:

- **The count might not be there at all.** Every guard below is arithmetic
  against `X-Result-Count`, so a response without one has no guards — and the
  shape of that failure is the worst available: `count()` answers `None`, a
  caller that reads `None` as a zero takes the month for an empty one, and a
  snapshot of no rows is written with no error anywhere. So an unreadable count
  is an error in its own right, in `client.py` as in the extension.
- **The filter might be ignored.** Waldur's DRF filters drop parameters they do
  not recognise (see the warning above), so an endpoint without `year`/`month`
  would be fetched once per month and yield the whole table over and over.
  A count for `year=1900` must come back zero, or the walk refuses to start.
- **A month might come back short.** Each month's row count is checked against
  `X-Result-Count` for that same filter, and the months must add up to the
  unfiltered total.
- **A row might come back twice anyway.** Duplicates and the omissions that
  accompany them cancel out in a row *count*, so counting cannot see them.
  `cache.ROW_KEYS` names the columns that identify a row and `cache.check()`
  rejects the pull if any key repeats — on write *and* on read, so an older
  snapshot taken before this fix fails loudly instead of quietly reporting a
  wildly inflated month. The client runs the same test per month, which is what
  lets it rule on the case below.

### The live month grows while you read it

A month that is still being written to is a *different* fault from unstable
paging, and it took the opposite answer. `X-Result-Count` is a count taken with
the first page, while every later page's `OFFSET` resolves against the table as
it is by then — so a usage row landing mid-crawl lengthens the tail, and the
pull ends holding **more** rows than the count promised. The guard above read
that as instability and ended the run, which cost a reader their whole report
for the portal doing its job.

The checks are therefore ranked rather than run in sequence, in both
`WaldurClient.iter_list_by_month()` and the extension's `pullMonth`:

| What came back | Ruling |
| --- | --- |
| A repeated key | **Fail**, always — where one row came twice another never came at all, and no live month excuses that |
| Fewer rows than the count | **Fail** — rows arriving during a read cannot explain rows missing from it |
| More rows, no repeats, a fresh count that agrees | **Keep** — every row in hand is a distinct real row, so the pull holds at least what the opening count described, and a count equal to what is in hand settles that it holds exactly them |
| More rows, anything else | **Fail** — unresolved |

That last confirmation costs one extra count request, and only when the numbers
disagree. The whole-table check at the end of the walk is ruled on the same way,
since a month that legitimately grew would otherwise fail there instead — with
one difference in what it *means*. A short walk there is not a short pull: every
month in it has already been checked row by row against its own count, so the
rows are not missing from the months, there are months missing from the window.
That is the `months_until`/`monthsUntil` horizon being too narrow, which is a
fault in this code and not a race, and it is reported without the retry advice
below.

Every one of those faults is a race, so a month is re-pulled
`client.MONTH_ATTEMPTS` times before it is reported — one month is a handful of
requests against a pull that is otherwise done. The extension reports each retry
through `onRetry`, because a stall on one month otherwise reads as a hang.

### Saying so when the only cure is another run

A race that survives all of that is nobody's mistake, and the one useful thing to
say about it is "run it again" — which is only advice if it comes with something
to run. So those failures are marked: `WaldurError(..., transient=True)`, and
`SnapshotError` alike.

- **The CLI** prints the command back, as it was invoked and with its arguments
  quoted to be pasted. `cli.main()` does it in one place, and only for the marked
  failures: telling someone to try again after a rejected token or a dropped
  filter costs a run and fixes nothing.
- **The extension** puts a *button* in the error box rather than the word
  "retry". The refresh control is in the controls bar, which is not where anyone
  looks after watching the page fail — and after a failure in the first wave it
  is not on screen at all. The button re-runs the same work rather than reloading,
  so the cached months stay and only what failed is fetched, and a second failure
  comes back with the button still there. A marked failure also gets a sentence
  saying the portal changed under the read, which is the difference between
  pressing the button and filing a bug.

And one check that does not depend on knowing how the pull works at all:
[`report reconcile`](#reconcile--from-openportal-allocation-user-usage--invoices)
puts the summed node hours beside `invoices.incurred_costs` for the same month.
The three guards above know the shape of *this* failure; reconcile only knows
what the total ought to be, so it is the one that would still fire on the next
way this endpoint finds to be wrong. Run it after every snapshot.

`check()` raises rather than de-duplicating, deliberately. A repeated row means
the pull enumerated the table unreliably, and for every row returned twice
another was returned not at all; keeping one of each would leave a snapshot
that looks clean and still under-reports. The fix is to pull again, not to
tidy up after.

### `openportal-associations` is not clean either

This section used to say that it was. It is not: paged end to end against the
live deployment, **the row count agrees on every attempt while a row or two
comes back twice and as many never arrive**, and how many depends on
`page_size` — which is what marks it a paging artefact rather than duplicate
data. It is the same fault as the usage table, three orders of magnitude
smaller, and the count check cannot see it for the same reason.

There is no fix of the usual shape available. The endpoint has no time axis to
slice on, and this deployment ignores `o=` and `ordering=` exactly as silently
as it ignores any other parameter it does not recognise — so the ordering
cannot be made total from the client side at all.

The browser extension therefore detects rather than prevents: `api.js`'s
`list()` takes an optional `rowKeys`, a repeat raises by default, and the
associations pull is the one caller that opts out via `onRepeats`, because the
error moves one denominator on one tile by a row or two and losing the tile
would be the worse trade. The tile says so on the page when it happens.

**The Python side is still exposed.** `cache.ROW_KEYS` names only the usage
endpoint, so `snapshot` pulls associations through plain `iter_list` with the
count check alone, and the count always agrees. Adding `openportal-associations:
("uuid",)` to `ROW_KEYS` would surface it — but `check()` raises, so every
snapshot would then fail on an endpoint that is a row or two short out of
thousands, which is a policy decision rather than an obvious fix. Left as it
is, and written down here rather than forgotten.

### What the deployment does and does not honour

Measured directly, because every one of these fails silently and none of them
is documented:

| Parameter | Behaviour |
| --- | --- |
| `page_size` | Honoured up to **300**; above that, 300 comes back with no error |
| `field=` | **Ignored** — every column is returned whatever is asked for |
| `o=`, `ordering=` | **Ignored** — the ordering cannot be changed from here |
| unrecognised filters | **Ignored**, with the full unfiltered `X-Result-Count` |

The cap being 300 rather than 1000 is why `DEFAULT_PAGE_SIZE` stays at 200 in
both implementations: the saving is about a third of the requests, not five
sixths, and having the two tools page identically is worth more than that — a
discrepancy between them is then never the page size.

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
allocation the token cannot see, another organisation's user, a row the portal
blanked entirely, and an invoice whose `total` is zeroed by a credit line while
`incurred_costs` still bills 15,000 node hours. If a change survives those, it
survives the portal.

The `reconcile` tests are regression tests for the paging bug written the way
the report sees it: a month summed twice against its invoice, a month whose rows
went missing, and — the case a row-key guard cannot catch — usage doubled across
two *legitimately distinct* allocation rows of the same project.

`tests/test_viz.py` renders the whole page once (module-level cache — it is 5 MB)
and asserts the properties that are easy to lose in a refactor and invisible in
a diff: nothing is fetched at view time, no figure declares a `yaxis2`, both
scales are offered, table views exist, and every palette colour has a dark step.

**None of that catches a blank plot.** The bundle-ordering bug above passed every
assertion in this file — the markup was correct and the JavaScript threw no
error, the figures simply never drew. If you change how the page is assembled,
open it, or screenshot it headlessly, before believing the suite.

### The browser extension, and why it cannot drift

`web/` reimplements a subset of `reports.py` in JavaScript, because a browser
cannot run this package. Two implementations of the same arithmetic drift, and
the drift is silent — nothing about a wrong `mean_monthly_allocation` looks
wrong on a chart — so they are pinned to each other with a golden fixture.

`tests/test_web_parity.py` runs the Python reports over the same
`tests/conftest.py` fixtures everything else uses, and writes two committed
files: `web/tests/fixture.json`, the inputs in the shape the API returns them,
and `web/tests/expected.json`, what the Python makes of them.
`web/tests/parity.test.mjs` runs the JavaScript over that fixture and asserts it
lands on the same numbers, comparing rows as multisets and checking separately
the three orderings a figure reads straight off an axis.

**The Python is the definition.** Change a formula in `reports.py` and the
pytest rewrites `expected.json` *and fails*, so the new expectations end up in
the working tree — where the node test immediately says which parts of the
JavaScript have not followed — and cannot reach main uncommitted. Both halves
run in CI, and `pixi run check` runs both.

What is deliberately **not** pinned is presentation: `viz.py` and
`web/src/figures.js` are two renderings of the same series and are allowed to
differ. What may not differ is any number either of them draws. A third test in
that file guards the guard, asserting the fixture still exercises a partial
month, a filtered-out organisation, a project with no usable award rate and a
blanked association row — a golden file over trivial data proves nothing. On the
storage side it asserts the same of that endpoint's awkward parts: a finished
month whose last day exists only in the top-level snapshot, a month in progress
with no `daily_reports` at all, one day reported twice by two collectors, a
limit that is not a size, a quota raised part-way through a month, and a reading
dated outside the month its row claims.

The same warning as above applies with more force: none of this opens a browser.
The parity test covers the arithmetic and nothing else, so a change to the
fetch order, the progressive render or the figure builders needs the extension
loaded and looked at.

That is not a hypothetical. The first time the extension was loaded and looked
at, the view buttons were drawing straight over the figure titles — three
things wanting the same strip above the plot: plotly's modebar, the button row
and a centred title. Every assertion in `figures.test.mjs` passed throughout,
because the figure *specification* was right and only the geometry was wrong,
and no amount of checking a spec object catches a title that renders as
"…month by mon|". It is now pinned left in container coordinates with a top
margin deep enough for two bands, and there is a test for that — but the test
was written after the browser found it, which is the point.

The command-line report inherited the fault rather than the fix, because it
carries titles in only one place: `viz.py` puts section headings in the HTML
around each figure, and the two quota heatmaps are the only figures there with
a title *inside* the plot — and six buttons beside it. They now use the same
geometry, and `tests/test_viz.py` pins it the same way.

## Platforms

`tool.pixi.workspace.platforms` names five, and `.github/workflows/ci.yml` runs
the whole check on one runner for each of them — `ubuntu-latest` for linux-64,
`ubuntu-24.04-arm` for linux-aarch64, `macos-latest` for osx-arm64,
`macos-15-intel` for osx-64 and `windows-latest` for win-64. A platform that is
solved for but never run on is a claim rather than a fact. The matrix does not
stop at the first failure, because which of them are healthy is the question it
is being asked, and every step in it is a `pixi run <task>` — the runners do not
agree on a shell, pixi's own shell is the same everywhere, and `pixi run check`
locally runs the same list in the same order.

Almost nothing here is particular to a machine: the package is pure Python, the
extension is loaded from source with no build step, and `sacct` — the only thing
shelled out to — exists on the cluster and nowhere else. Three smaller things
did have to be ported, and they are the ones to remember when writing more.

**An activation script is a shell script, and cmd.exe is not that shell.** So
there are two, selected by pixi target: `scripts/activate.sh` on unix,
`scripts/activate.bat` on win-64. They set the same two defaults,
`WALDUR_API_URL` and `WALDUR_CACHE_DIR`, and neither contains a secret. What the
batch file cannot do is *source* `.envrc.local`, which is a shell file and is
where the token lives — so `config._token_from_envrc` reads the token out of it
directly instead. That runs on every platform rather than only on the one that
needs it, which is what puts it under test; it is deliberately not a shell,
taking `WALDUR_API_TOKEN=` off a line and ignoring everything else in a file
that is allowed to contain arbitrary shell. The environment still wins over the
file, so on unix, where the shell has already sourced it, this never fires.

**`%-d` is a glibc extension, not a date format.** Windows raises `ValueError:
Invalid format string` on it, and because the stale-storage sentence is built
during `viz.render`, that took the entire page down rather than one line of it.
Unpadded numbers are worth spelling out of the date object — `{read.day}` — and
`web/src/page.js` asks for the same thing with `day: 'numeric'`, so the two
reports agree on what the sentence looks like.

**A generated file must come out the same bytes wherever it was generated.**
Both golden files under `web/tests/` and the vendored plotly bundle are written
with an explicit `newline="\n"` rather than Python's platform default, entry
names in the release archive are `as_posix()` because the zip format says so and
Chrome would read a backslash as part of a file name, and `.gitattributes` pins
the checkout to LF so that none of it depends on which machine cloned the
repository. The claim that two builds of the extension can be compared is only
true if all of that holds.

## Releasing the extension

The extension ships as a zip on a GitHub release, built by
`.github/workflows/release.yml` when a `web-v*` tag is pushed.

```bash
# edit "version" in web/manifest.json, commit it
git tag web-v0.3.0
git push origin web-v0.3.0
```

The tag is prefixed because this repository has two shippable things, and an
unprefixed `v0.3.0` would silently claim to release the Python package too. The
prefix says *which artefact*, not which version line: the two are deliberately
kept at the same number. `web/manifest.json`, the `version` in `pyproject.toml`
and `waldur_tools.__version__` move together, even when a release only changed
one side. One number describing the whole repository is easier to hold in your
head than two that drift, and the cost is an occasional bump that means nothing
on one of them.

`web/manifest.json` is the only place the extension's version is written down.
The workflow reads it, refuses a tag that disagrees, and names the asset from
it — so the archive's name and the version the extension reports about itself
cannot come apart. A tag can be pushed at a commit that never went through CI,
so the workflow runs `pixi run check` in full before it packs anything.

`pixi run web-pack` is the same command the workflow runs, so the archive can
be built and inspected locally before tagging. It depends on `web-vendor`,
which is the whole reason it is a task rather than shell in the YAML: the
plotly bundle is generated and not committed, and an archive built without it
would be an extension that loads and then cannot draw. `pixi.lock` is
committed, so CI vendors the same bundle a checkout does. Entries are written
sorted and with a fixed timestamp, so the same source produces the same bytes
and two builds can be compared.

What goes in is what Chrome loads, plus the README: the tests, their golden
fixtures and the `package.json` that marks `src/` as ES modules for node all
stay out. Paths inside are relative to `web/`, because Chrome wants
`manifest.json` at the root of what it loads.

**A downloaded zip is not installable as it stands** — Chrome only takes an
unpacked folder outside the Web Store, so the release notes say to unpack it
first. Publishing to the Chrome Web Store later would not change any of this:
the same archive is what the store takes, uploadable from this workflow with an
API key. Self-hosting a signed CRX with an `update_url` would buy auto-update,
but Chrome refuses non-store extensions on Windows and ChromeOS, which is most
of the audience.
