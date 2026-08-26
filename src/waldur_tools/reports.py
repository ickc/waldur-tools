"""Analyses built on top of snapshotted (or live) endpoint data.

Every report takes a source -- a :class:`~waldur_tools.cache.Snapshot` or a live
:class:`~waldur_tools.client.WaldurClient` -- and returns a polars DataFrame, so
they compose in notebooks as readily as in the CLI.

**How much is the API's and how much is ours?** Mostly the API's. Each report
selects and renames columns straight from one or two endpoints; the only
computed columns are the handful named in each docstring below, and DEVELOPER.md
tabulates every one of them with its formula. Nothing is silently aggregated or
imputed. The one exception worth knowing about is ``scope``: by default the
reports drop rows describing projects outside your administrative reach, because
the portal returns those rows with most fields blanked out. See :func:`in_scope`.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from .cache import SnapshotError, load
from .frames import integral, numeric

if TYPE_CHECKING:
    from .cache import Snapshot
    from .client import WaldurClient

#: Compute nodes in Isambard 3 phase 1, the machine the usage figures describe.
TOTAL_NODES = 384

#: The GW4 partner share of that machine held by this organisation.
DEFAULT_SHARE = 0.10

#: The customer whose projects count as "ours" in :func:`monthly`. The portal
#: also shows other, separately funded projects administered by the same
#: token, which would inflate our own share if counted in.
DEFAULT_CUSTOMER = "University of Exeter"

#: How far :func:`reconcile` lets the summed usage drift from the invoice before
#: it calls a month a mismatch, as a fraction of the invoice. The two sides are
#: independent roll-ups of the same node hours -- the usage endpoint rounds each
#: user-month to two decimals, the invoice keeps ten -- so on a month that is
#: genuinely fine they agree to a tiny fraction of a percent, orders of
#: magnitude inside this. It is set to catch a broken pull, not to audit
#: rounding.
RECONCILE_TOLERANCE = 0.01

#: The absolute floor under that tolerance, in node hours, so a month billed at
#: a handful of node hours is not called a mismatch over a fraction of one. An
#: invoice can also carry a project whose allocation this token cannot see,
#: contributing a node hour or two of its own, and that lands as an absolute
#: gap rather than a proportional one.
RECONCILE_FLOOR = 2.0


def project_code(column: str) -> pl.Expr:
    """Extract the SLURM project code from a Waldur ``groupname``/``username``.

    Isambard's SLURM names are structured: an allocation's ``groupname`` is
    ``brics.<code>`` (or ``group.<name>`` for legacy internal projects), an
    association's ``username`` is ``<unix user>.<code>``, and its
    ``useridentifier`` is ``<unix user>.<code>.<cluster>``. The code -- e.g.
    ``abc1`` -- is the stable key that ties a person to a project, and unlike
    the allocation URL it survives a project having several allocations.
    """
    return pl.col(column).str.split(".").list.get(1, null_on_oob=True)


def in_scope(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """The projects this token administers, one row per SLURM project code.

    The portal is multi-tenant: Isambard 3 is run by Bristol, and a token issued
    to (say) an Exeter administrator sees every *association* on the machine but
    only its own organisation's *allocations* -- a small set of allocations
    against a much larger set of associations spanning many more project codes
    than the token administers -- and the out-of-scope association rows come
    back with ``username``, ``groupname`` and ``useridentifier`` all null,
    since the portal blanks what you may not read. There is no documented API
    filter for "mine"; the visible allocations *are* the answer, so this
    derives the scope from them rather than guessing from blank fields.
    """
    allocations = load(source, ["openportal-allocations"])["openportal-allocations"]
    if allocations.is_empty():
        return pl.DataFrame(schema={"project_code": pl.String})
    return (
        allocations.select(
            project_code=project_code("groupname"),
            project_name="project_name",
            customer_name="customer_name",
            project_uuid="project_uuid",
        )
        .drop_nulls("project_code")
        .unique(subset=["project_code"], keep="first")
    )


def credits(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """Credit burn-down per project, with a runway estimate.

    Everything except ``remaining``, ``used_pct``, ``overspent`` and
    ``months_remaining`` is verbatim from ``openportal-accounting-summary``.

    ``months_remaining`` is the unspent balance divided by *this* month's spend,
    answering "at this rate, when do we run dry?". Two caveats it pays to know:

    * It is **negative when the project has already overspent**, because
      ``remaining`` is negative -- e.g. an award of 40,000 credits, 42,000
      spent, is -2,000 remaining, and if 1,000 of that was spent this month
      that is ``-2.0``. Read a negative value as "already over, and still
      spending"; ``overspent`` says so outright.
    * It is null when nothing has been spent this month, since the rate is zero,
      not because the project is fine.

    The sort puts the most negative first, so the projects in trouble lead.
    """
    frame = load(source, ["openportal-accounting-summary"])["openportal-accounting-summary"]
    if frame.is_empty():
        return frame

    frame = numeric(frame, "total_credits", "total_spend", "current_month_spend")
    return (
        frame.with_columns(
            remaining=pl.col("total_credits") - pl.col("total_spend"),
            used_pct=(100 * pl.col("total_spend") / pl.col("total_credits").replace(0.0, None)),
            months_remaining=(
                (pl.col("total_credits") - pl.col("total_spend"))
                / pl.col("current_month_spend").replace(0.0, None)
            ),
        )
        .with_columns(overspent=pl.col("remaining") < 0)
        .select(
            "project_name",
            "customer_name",
            "total_credits",
            "total_spend",
            "current_month_spend",
            "remaining",
            "used_pct",
            "overspent",
            "months_remaining",
            "end_date",
        )
        .sort("months_remaining", nulls_last=True)
    )


def allocations(
    source: Snapshot | WaldurClient,
    *,
    customer: str | None = DEFAULT_CUSTOMER,
    scope: bool = True,
) -> pl.DataFrame:
    """What each project was awarded, over what span, and its monthly average.

    The denominator behind the *relative* view in ``viz``: a project's usage in
    a month means nothing next to the whole organisation's share, because no
    project was ever given the whole organisation's share. It means something
    next to its own award.

    ``mean_monthly_allocation`` is ``total_credits / award_months``, where
    ``award_months`` counts calendar months from ``start_date`` to ``end_date``
    inclusive. A project holding 12,000 node hours over a year is being asked
    for 1,000 a month, and a month at 1,500 is a month it ran at 150% of its
    own steady rate. Nothing in the portal states a monthly figure -- credits
    are granted as a lump for a period -- so the lump spread evenly across the
    period is the construction, and it is the one the ``viz`` report names.

    **Four things it is not, which matter before quoting it.**

    * **It is not a monthly cap.** The real ceiling is a single SLURM
      ``GrpTRESMins`` on the ``brics.<code>`` account, which Waldur sets to the
      *remaining* credits and which is enforced against the whole award, not
      against a month of it. A project may legitimately burn a year's credits
      in a fortnight; that reads as 1,200% here and is not an error. See
      :func:`utilisation` for the limit as the portal reports it.
    * **It back-dates top-ups.** ``total_credits`` is the award as it stands
      *now*, and credits get added to a live project. There is no grant-history
      endpoint -- no ``created`` on the credit, no order log in the snapshot --
      so an extension granted in month ten is spread across months one to ten
      as well. The denominator is therefore larger than the rate those early
      months were actually funded at, and the percentage correspondingly
      **smaller**: a topped-up project's first months read as quieter than they
      were. A project that doubled its award half way through will show its
      early months at half their true share. This is the largest known error in
      the figure, and it runs one way -- it never overstates.
    * **It ignores when the project actually started running.** ``start_date``
      is when the project was set up in the portal, which precedes the first
      job by weeks in most cases here.
    * **It is undefined, not zero, when there are no credits.** The internal
      and workshop projects hold none, so their ratio is null and they drop out
      of the relative view rather than reading as infinitely over budget.

    ``end_date`` is null for the open-ended internal projects; those are
    measured to the snapshot date instead, so the span is "so far" rather than
    unbounded.
    """
    projects = in_scope(source)
    if not scope:
        customer = None
    if customer is not None:
        projects = projects.filter(pl.col("customer_name") == customer)
    if projects.is_empty():
        return pl.DataFrame(
            schema={
                "project_code": pl.String,
                "project_name": pl.String,
                "customer_name": pl.String,
                "start_date": pl.Date,
                "end_date": pl.Date,
                "total_credits": pl.Float64,
                "award_months": pl.Int64,
                "mean_monthly_allocation": pl.Float64,
            }
        )

    summary = load(source, ["openportal-accounting-summary"])["openportal-accounting-summary"]
    if summary.is_empty() or "project_uuid" not in summary.columns:
        return projects.drop("project_uuid").with_columns(
            start_date=pl.lit(None, pl.Date),
            end_date=pl.lit(None, pl.Date),
            total_credits=pl.lit(None, pl.Float64),
            award_months=pl.lit(None, pl.Int64),
            mean_monthly_allocation=pl.lit(None, pl.Float64),
        )

    # The join is on `project_uuid` and not on the name, because the name is
    # not unique: an estate can carry two accounting rows sharing a name under
    # different UUIDs, where only one is a real provisioned project -- holding
    # the credits, the allocations and the row in `projects` -- and the other
    # has zero credits and no allocation. A name join could pick either. (This
    # is *not* the two-services duplication: a project's Isambard 3 and MACS
    # allocations share one `project_uuid`.)
    #
    # The dates are filled in when absent rather than assumed present, so a
    # snapshot taken before the portal carried them yields a null rate instead
    # of raising -- the relative view is an extra, and losing it should not cost
    # the caller the other columns.
    def dated(column: str) -> pl.Expr:
        if column not in summary.columns:
            return pl.lit(None, pl.Date).alias(column)
        return pl.col(column).cast(pl.String).str.to_date(strict=False).alias(column)

    awarded = numeric(summary, "total_credits").select(
        "project_uuid", dated("start_date"), dated("end_date"), total_credits="total_credits"
    )
    horizon = as_of(source)
    finish = pl.col("end_date").fill_null(horizon)
    months = (
        (finish.dt.year() - pl.col("start_date").dt.year()) * 12
        + (finish.dt.month() - pl.col("start_date").dt.month())
        + 1
    )
    return (
        projects.join(awarded, on="project_uuid", how="left")
        .drop("project_uuid")
        .with_columns(award_months=pl.max_horizontal(months, pl.lit(1)).cast(pl.Int64))
        .with_columns(
            mean_monthly_allocation=(
                pl.col("total_credits").replace(0.0, None) / pl.col("award_months")
            )
        )
        .select(
            "project_code",
            "project_name",
            "customer_name",
            "start_date",
            "end_date",
            "total_credits",
            "award_months",
            "mean_monthly_allocation",
        )
        .sort("mean_monthly_allocation", descending=True, nulls_last=True)
    )


def membership(source: Snapshot | WaldurClient, *, scope: bool = True) -> pl.DataFrame:
    """Who has access to what: one row per user/project pairing.

    This is the ``count_users()`` example from ``gw4-isambard/rse-sharing``,
    rebuilt as a join. The upstream version issues one allocation GET per
    association row, which cannot complete here: associations reference far
    more distinct allocations than a typical token can fetch, and the rest
    return 404.

    ``project_code`` is parsed from ``groupname`` (see :func:`project_code`) and
    ``unix_username`` from ``username``; the project and customer names are
    joined in from ``openportal-allocations``, and the real name and email from
    ``users``, which is already scoped to your organisation.

    The project join is on ``project_code``, not on the allocation URL. Each
    project has an allocation per service -- Isambard 3 and Isambard 3 MACS both
    appear -- so an association points at only one of them, and a URL join
    resolves only the allocations you can fetch individually, where a code
    join resolves every row belonging to a visible project.

    That same duplication means the raw endpoint carries one association per
    user *per service*. Since a person's access is per project, those collapse
    to a single row, with ``associations`` keeping the count so nothing is
    hidden -- in-scope association rows roughly halve into pairings.

    With ``scope=True`` (the default) only in-scope rows are returned.
    ``scope=False`` returns everything -- mostly other organisations' users,
    plus the rows the portal blanked entirely.
    """
    data = load(source, ["openportal-associations", "openportal-allocations", "users"])
    associations = data["openportal-associations"]
    if associations.is_empty():
        return associations

    people = data["users"].select(
        unix_username="unix_username", full_name="full_name", email="email"
    )
    frame = (
        associations.with_columns(
            project_code=project_code("groupname"),
            unix_username=pl.col("username").str.split(".").list.first(),
        )
        .join(in_scope(source).drop("project_uuid"), on="project_code", how="left")
        .join(people, on="unix_username", how="left")
    )

    if scope:
        frame = frame.filter(pl.col("project_name").is_not_null())

    return (
        frame.group_by("unix_username", "project_code")
        .agg(
            full_name=pl.col("full_name").first(),
            email=pl.col("email").first(),
            project_name=pl.col("project_name").first(),
            customer_name=pl.col("customer_name").first(),
            associations=pl.len(),
        )
        .select(
            "unix_username",
            "full_name",
            "project_code",
            "project_name",
            "customer_name",
            "email",
            "associations",
        )
        .sort(["customer_name", "project_name", "unix_username"], nulls_last=True)
    )


def utilisation(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """This month's node usage per allocation, against the SLURM node limit.

    Beware the name: ``node_usage`` on ``openportal-allocations`` is **not**
    cumulative. It matches ``current_month_spend`` from the accounting summary
    to the penny for every project observed, so it is the
    current month's usage only -- which is why this report's ``month_vs_limit_pct``
    is so much smaller than ``credits.used_pct``, and why nothing here ever
    exceeds 100% while a project can be over budget in ``credits``. The two
    columns answer different questions:

    ==========================  =========================================
    ``credits.used_pct``        lifetime spend / lifetime credits
    ``month_vs_limit_pct``      this month's usage / remaining node limit
    ==========================  =========================================

    ``node_limit`` empirically tracks ``total_credits - total_spend`` as of the
    last SLURM sync (exact for most projects, within a few percent for the
    rest) and never goes negative, so overspent projects show a stale positive
    limit. The portal does not document how it is derived; that correspondence
    is an observation, not a contract.

    ``state`` is Waldur's resource state machine -- ``Creating``, ``OK``,
    ``Erred``, ``Deleting`` -- describing whether the portal succeeded in
    provisioning the allocation onto the cluster, not whether anyone is using
    it. Every allocation in normal operation reads ``OK``. ``is_active`` is the
    separate question of whether the allocation is switched on.

    **Do not sum ``node_limit`` down this report.** Every project appears twice,
    once per service, and the two rows carry the *same* ``node_limit`` -- it is
    one credit balance rendered against both, not two pools. Summing the column
    doubles the estate's apparent capacity.

    **The MACS rows are not idle capacity, they are unmetered.** An
    ``Isambard 3 Multi Architecture System`` row reads 0.00 used against a
    healthy limit and looks like the emptiest allocation you have; it is not.
    Nothing on that cluster is charged: no account on ``i3macs`` carries a
    ``GrpTRESMins``, its ``marketplace-component-usages`` rows all read ``0.0``,
    and every invoiced node hour sits under the ``Isambard 3`` offering -- while
    real jobs do run there. So its
    ``node_usage`` is not a measurement of anything, and the limit beside it is
    the Isambard 3 balance showing through. Filter on
    ``service_name == "Isambard 3"`` before reading this as utilisation.

    Sorted emptiest first: those are the allocations holding capacity nobody
    used this month -- subject to the paragraph above.
    """
    frame = load(source, ["openportal-allocations"])["openportal-allocations"]
    if frame.is_empty():
        return frame

    frame = numeric(frame, "node_usage", "node_limit")
    return (
        frame.with_columns(
            project_code=project_code("groupname"),
            month_vs_limit_pct=(
                100 * pl.col("node_usage") / pl.col("node_limit").replace(0.0, None)
            ),
        )
        .rename({"node_usage": "node_usage_this_month"})
        .select(
            "project_name",
            "project_code",
            "service_name",
            "customer_name",
            "node_usage_this_month",
            "node_limit",
            "month_vs_limit_pct",
            "is_active",
            "state",
        )
        .sort("month_vs_limit_pct", nulls_last=True)
    )


def as_of(source: Snapshot | WaldurClient) -> date:
    """The day the data describes: a snapshot's creation date, or today if live.

    Only used to decide which month is still in progress. A snapshot taken on
    the 21st holds three weeks of that month, and averaging it in alongside
    complete months would drag every headline down.
    """
    created = getattr(source, "meta", {}).get("created") if hasattr(source, "meta") else None
    if isinstance(created, str):
        try:
            return date.fromisoformat(created[:10])
        except ValueError:
            pass
    return date.today()


def _monthly_rows(
    source: Snapshot | WaldurClient,
    *,
    customer: str | None = DEFAULT_CUSTOMER,
    scope: bool = True,
) -> pl.DataFrame:
    """Per user, project and month node usage for the projects we count as ours.

    The shared base of :func:`monthly` and :func:`monthly_totals`. Both need the
    same rows but aggregate them differently -- distinct users per month cannot
    be recovered by summing a per-project user count, so neither report can be
    derived from the other.

    ``scope=False`` widens ``customer`` to every project the token administers,
    which is as wide as these reports can honestly go: usage rows for other
    organisations do arrive, but their project codes resolve to no name, no
    customer and no node limit, so there is nothing to attribute them to.
    """
    if not scope:
        customer = None
    frame = load(source, ["openportal-allocation-user-usage"])["openportal-allocation-user-usage"]
    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "month": pl.Date,
                "project_code": pl.String,
                "project_name": pl.String,
                "customer_name": pl.String,
                "unix_username": pl.String,
                "node_usage": pl.Float64,
            }
        )

    projects = in_scope(source).drop("project_uuid")
    if customer is not None:
        projects = projects.filter(pl.col("customer_name") == customer)

    frame = integral(numeric(frame, "node_usage"), "year", "month").with_columns(
        project_code=project_code("username"),
        unix_username=pl.col("username").str.split(".").list.first(),
    )
    return (
        frame.join(projects, on="project_code", how="inner")
        .with_columns(
            month=pl.date(pl.col("year"), pl.col("month"), 1),
        )
        .select(
            "month",
            "project_code",
            "project_name",
            "customer_name",
            "unix_username",
            "node_usage",
        )
        .drop_nulls("month")
    )


def _entitlement(nodes: int, share: float) -> pl.Expr:
    """Node hours our share of the machine is worth in a given calendar month.

    ``nodes * share`` nodes held for every hour of the month. It is an
    accounting entitlement rather than a cap, and a percentage above 100 is
    both possible and unremarkable.

    **The share is not enforced anywhere on the machine.** Isambard 3 runs
    ``PriorityType=priority/multifactor`` with every weight -- ``FairShare``,
    ``Age``, ``JobSize``, ``QOS``, ``Partition`` -- set to zero, which leaves
    every job at the same priority and the queue running first come, first
    served under ``sched/backfill``. There is a fair-share tree in ``sshare``
    and it decides nothing. The only enforced ceiling is per *project*: a
    ``GrpTRESMins`` on the ``brics.<code>`` account, which the portal sets from
    that project's remaining credits and which SLURM enforces via
    ``AccountingStorageEnforce`` including ``limits`` and ``safe``. Its ``cpu``
    figure is in cpu-minutes, so dividing by ``cores_per_node * 60`` gives node
    hours and lands on the portal's own ``limits.node`` for that project. So
    nothing holds these nodes for us, and nothing stops us using more of the
    machine than our share when it is idle.

    Confirm on a login node rather than trusting this paragraph::

        scontrol show config | grep -iE 'Priority(Type|Weight)'
        sacctmgr show assoc account=brics.<code> format=Account,GrpTRESMins

    It is also, historically, what a bad pull looked like. Paging
    ``openportal-allocation-user-usage`` end to end returned some rows twice and
    put several months well over 100%; pulled a month at a time (see
    :const:`waldur_tools.cache.BY_MONTH`) the same months came back well under
    100%, matching the portal's own dashboard. Treat anything over 100% as
    worth cross-checking rather than as a finding.
    """
    days = pl.col("month").map_elements(
        lambda value: calendar.monthrange(value.year, value.month)[1],
        return_dtype=pl.Int64,
    )
    return nodes * share * 24 * days


def monthly(
    source: Snapshot | WaldurClient,
    *,
    nodes: int = TOTAL_NODES,
    share: float = DEFAULT_SHARE,
    customer: str | None = DEFAULT_CUSTOMER,
    scope: bool = True,
) -> pl.DataFrame:
    """Node hours per project per calendar month, against our share of the machine.

    ``openportal-allocation-user-usage`` is the only endpoint with a time axis:
    one row per user, allocation and month, and its ``node_usage`` *is*
    cumulative-safe to sum, unlike the identically named field on
    ``openportal-allocations``. This groups those rows by project and month.

    Summing is only safe because the pull is. That endpoint cannot be paged end
    to end -- it repeats rows across page boundaries -- so it is fetched a month
    at a time and checked for repeated keys before any of this runs. See
    :const:`waldur_tools.cache.BY_MONTH`.

    ``entitlement_node_hours`` is the whole organisation's monthly share --
    ``nodes * share * 24 * days_in_month``, so 384 nodes at 10% is roughly
    28,570 node hours in a 31-day month -- and ``pct_of_entitlement`` measures
    one project against all of it, answering "how much of our slice did this
    project alone account for?". It is not a per-project quota; nothing in the
    portal allocates the share out to projects.

    ``customer`` restricts to one organisation's projects (ours by default);
    ``scope=False``, or ``customer=None``, widens it to every project the token
    administers -- which adds the separately funded UKRI and other, separately funded
    projects, and so overstates our own share.
    """
    rows = _monthly_rows(source, customer=customer, scope=scope)
    if rows.is_empty():
        return rows

    return (
        rows.group_by("month", "project_code", "project_name", "customer_name")
        .agg(
            node_hours=pl.col("node_usage").sum(),
            active_users=pl.col("unix_username").filter(pl.col("node_usage") > 0).n_unique(),
        )
        .with_columns(entitlement_node_hours=_entitlement(nodes, share))
        .with_columns(
            pct_of_entitlement=100 * pl.col("node_hours") / pl.col("entitlement_node_hours"),
            mean_nodes=pl.col("node_hours") / (pl.col("entitlement_node_hours") / (nodes * share)),
        )
        .sort("month", "node_hours", descending=[False, True])
    )


def monthly_totals(
    source: Snapshot | WaldurClient,
    *,
    nodes: int = TOTAL_NODES,
    share: float = DEFAULT_SHARE,
    customer: str | None = DEFAULT_CUSTOMER,
    scope: bool = True,
) -> pl.DataFrame:
    """One row per month: how much of our share of the machine we actually used.

    The headline series behind ``waldur-tools viz``. ``pct_of_entitlement`` is
    the number the report is built around -- 100% means we ran, on average
    across the month, exactly the ``nodes * share`` nodes our share is worth.

    ``active_projects`` and ``active_users`` count only those with non-zero
    usage, so they read as "who actually ran something", not "who could have".

    ``is_partial`` marks the month the snapshot was taken in, which is
    incomplete by construction and must be kept out of any average.
    """
    rows = _monthly_rows(source, customer=customer, scope=scope)
    if rows.is_empty():
        return rows

    today = as_of(source)
    return (
        rows.group_by("month")
        .agg(
            node_hours=pl.col("node_usage").sum(),
            active_projects=pl.col("project_code").filter(pl.col("node_usage") > 0).n_unique(),
            active_users=pl.col("unix_username").filter(pl.col("node_usage") > 0).n_unique(),
            projects_with_usage_rows=pl.col("project_code").n_unique(),
        )
        .with_columns(entitlement_node_hours=_entitlement(nodes, share))
        .with_columns(
            pct_of_entitlement=100 * pl.col("node_hours") / pl.col("entitlement_node_hours"),
            mean_nodes=pl.col("node_hours") / (pl.col("entitlement_node_hours") / (nodes * share)),
            unused_node_hours=pl.col("entitlement_node_hours") - pl.col("node_hours"),
            is_partial=pl.col("month") == date(today.year, today.month, 1),
        )
        .sort("month")
    )


def invoiced(
    source: Snapshot | WaldurClient, *, customer: str | None = DEFAULT_CUSTOMER
) -> pl.DataFrame:
    """The node hours the portal billed, one row per calendar month.

    ``invoices.incurred_costs`` is a running total in credits, and on this
    deployment a credit *is* a node hour: every usage line on every invoice in
    a snapshot carries ``unit_price`` exactly ``1.0000000000`` and
    ``measured_unit`` ``hours``, and ``incurred_costs`` equals the sum of those
    lines' ``quantity`` to the last decimal place on every one of them. So the
    field is a second, independent measurement of the same node hours
    :func:`monthly_totals` sums out of the usage endpoint -- which is what makes
    :func:`reconcile` possible at all.

    Use ``incurred_costs`` and not ``price`` or ``total``. Those are net of the
    credit lines the portal writes to zero a grant-funded invoice out, and they
    do it unevenly -- an invoice can bill thousands of node hours and still
    show a ``total`` near zero once the credit line cancels it.

    ``customer`` filters on the name inside ``customer_details``, so the usage
    side and the invoice side are asked about the same organisation. ``None``
    keeps every invoice the token can see.
    """
    frame = load(source, ["invoices"])["invoices"]
    empty = pl.DataFrame(
        schema={"month": pl.Date, "incurred_costs": pl.Float64, "invoice_state": pl.String}
    )
    if frame.is_empty():
        return empty

    frame = integral(numeric(frame, "incurred_costs"), "year", "month")
    if customer is not None and "customer_details" in frame.columns:
        frame = frame.filter(pl.col("customer_details").str.json_path_match("$.name") == customer)
    if frame.is_empty():
        return empty

    return (
        frame.with_columns(month=pl.date(pl.col("year"), pl.col("month"), 1))
        .drop_nulls("month")
        .group_by("month")
        .agg(
            incurred_costs=pl.col("incurred_costs").sum(),
            invoice_state=pl.col("state").unique().sort().str.join(", "),
        )
        .sort("month")
    )


def reconcile(
    source: Snapshot | WaldurClient,
    *,
    customer: str | None = DEFAULT_CUSTOMER,
    scope: bool = True,
    tolerance: float = RECONCILE_TOLERANCE,
) -> pl.DataFrame:
    """Summed usage against the invoice, month by month: does the pull add up?

    Two routes to one number. ``node_hours`` is
    ``openportal-allocation-user-usage`` summed over a user, an allocation and a
    month, exactly as :func:`monthly_totals` does it; ``incurred_costs`` is what
    the portal billed the organisation for that month, and equals node hours on
    this deployment (see :func:`invoiced`). They come from different endpoints,
    aggregated by different sides of the portal, so agreement is evidence and
    disagreement is a defect -- in the pull, in the scope, or in the billing.

    **This is the check that would have caught the paging bug**, before an
    inflated headline could be read as a finding. Run against a
    snapshot taken by paging that endpoint end to end, most months read
    ``ok`` while a handful read ``usage high`` -- overcounted by a wide
    margin, since a duplicated page counts the same usage twice.

    Only the months at the tail of a table-wide walk tend to agree, which is
    the signature of unstable ``LIMIT``/``OFFSET`` paging: rows repeat and
    vanish across page boundaries everywhere except at the tail. A correctly
    pulled snapshot instead agrees with the invoice to a small fraction of a
    percent in every month.

    ``difference`` is ``node_hours - incurred_costs``, so it is **positive when
    we counted usage nobody billed** -- the shape a duplicated pull takes -- and
    negative when the invoice knows about usage the pull does not. ``status``
    reduces that to one word per month:

    ``ok``
        Within ``tolerance`` of the invoice, or within :data:`RECONCILE_FLOOR`
        node hours of it, whichever is the larger allowance. A month with no
        usage and a zero invoice is ``ok`` rather than empty.
    ``usage high`` / ``usage low``
        Outside it, in the direction named. Suspect the pull first.
    ``no invoice``
        Usage in a month the portal has not invoiced. Nothing to check against;
        not a finding on its own.
    ``no usage``
        An invoice with no usage rows behind it at all.

    ``is_partial`` marks the month the snapshot was taken in. It reconciles like
    any other -- both sides stop at the same instant -- but neither figure is
    the month's final one.

    ``scope=False`` (or ``customer=None``) widens *both* sides to everything the
    token can see, and the two do not correspond: usage arrives for other
    organisations' projects, whose invoices go to their own organisations and
    are not in this snapshot. Expect ``usage high`` for every month with a
    mismatch that is real billing rather than a bad pull.
    """
    if not scope:
        customer = None
    usage = (
        _monthly_rows(source, customer=customer, scope=scope)
        .group_by("month")
        .agg(node_hours=pl.col("node_usage").sum())
    )
    billed = invoiced(source, customer=customer)
    if usage.is_empty() and billed.is_empty():
        return pl.DataFrame(
            schema={
                "month": pl.Date,
                "node_hours": pl.Float64,
                "incurred_costs": pl.Float64,
                "difference": pl.Float64,
                "pct_difference": pl.Float64,
                "status": pl.String,
                "invoice_state": pl.String,
                "is_partial": pl.Boolean,
            }
        )

    today = as_of(source)
    # A month absent from one side is not the same as a zero on it, so the join
    # keeps the null and `status` decides what a missing side means. The
    # comparison itself reads a missing side as zero, which is what makes a
    # month nobody used and nobody billed reconcile instead of raising a flag.
    gap = pl.col("node_hours").fill_null(0.0) - pl.col("incurred_costs").fill_null(0.0)
    allowed = pl.max_horizontal(
        pl.lit(RECONCILE_FLOOR), tolerance * pl.col("incurred_costs").fill_null(0.0).abs()
    )
    return (
        usage.join(billed, on="month", how="full", coalesce=True)
        .with_columns(
            difference=pl.col("node_hours") - pl.col("incurred_costs"),
            pct_difference=(
                100
                * (pl.col("node_hours") - pl.col("incurred_costs"))
                / pl.col("incurred_costs").replace(0.0, None)
            ),
            status=(
                pl.when(gap.abs() <= allowed)
                .then(pl.lit("ok"))
                .when(pl.col("incurred_costs").is_null())
                .then(pl.lit("no invoice"))
                .when(pl.col("node_hours").is_null())
                .then(pl.lit("no usage"))
                .when(gap > 0)
                .then(pl.lit("usage high"))
                .otherwise(pl.lit("usage low"))
            ),
            is_partial=pl.col("month") == date(today.year, today.month, 1),
        )
        .select(
            "month",
            "node_hours",
            "incurred_costs",
            "difference",
            "pct_difference",
            "status",
            "invoice_state",
            "is_partial",
        )
        .sort("month")
    )


def user_usage(
    source: Snapshot | WaldurClient,
    *,
    year: int | None = None,
    scope: bool = True,
) -> pl.DataFrame:
    """Per-user node usage summed across allocations and months, heaviest first.

    Source is ``openportal-allocation-user-usage``, one row per user, allocation
    and calendar month. ``username`` there is ``<unix user>.<code>.<cluster>``,
    so this groups on the leading unix name to combine a person's projects, and
    sums ``node_usage`` -- which *is* cumulative here, unlike the identically
    named field on ``openportal-allocations``.

    ``months_active`` counts source rows with non-zero usage, so a user on two
    allocations in one month counts twice; treat it as an activity score rather
    than a calendar count.

    This endpoint is not scoped to your organisation, so ``scope=True`` (the
    default) keeps only users on a project you administer -- a much larger row
    count across the whole machine otherwise.
    """
    frame = load(source, ["openportal-allocation-user-usage"])["openportal-allocation-user-usage"]
    if frame.is_empty():
        return frame

    frame = integral(numeric(frame, "node_usage"), "year", "month").with_columns(
        unix_username=pl.col("username").str.split(".").list.first(),
        project_code=project_code("username"),
    )
    if year is not None:
        frame = frame.filter(pl.col("year") == year)
    if scope:
        codes = in_scope(source)["project_code"].to_list()
        frame = frame.filter(pl.col("project_code").is_in(codes))

    return (
        frame.group_by("unix_username", "full_name")
        .agg(
            total_node_usage=pl.col("node_usage").sum(),
            projects=pl.col("project_code").unique().sort().str.join(", "),
            months_active=pl.col("node_usage").filter(pl.col("node_usage") > 0).len(),
            first_year=pl.col("year").min(),
            last_year=pl.col("year").max(),
        )
        .sort("total_node_usage", descending=True)
    )


def queue(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """Daily job counts and mean queue wait, unpacked from the usage reports.

    ``openportal-project-usage-reports`` returns one row per project, resource
    and month, with a free-form ``report`` blob nesting a dictionary per day.
    This is the only report that reshapes rather than selects: it explodes that
    blob to one row per project/resource/day, passing ``num_jobs`` and
    ``total_wait_seconds`` through untouched. ``mean_wait_seconds`` is their
    quotient (null on a day with no jobs, rather than a division by zero) and
    ``distinct_users`` is the length of the day's ``user_job_counts`` map.
    """
    frame = load(source, ["openportal-project-usage-reports"])["openportal-project-usage-reports"]
    if frame.is_empty():
        return frame

    rows = []
    for record in frame.iter_rows(named=True):
        payload = json.loads(record["report"]) if record.get("report") else {}
        for day, daily in (payload.get("reports") or {}).items():
            jobs = daily.get("num_jobs") or 0
            wait = daily.get("total_wait_seconds") or 0
            rows.append(
                {
                    "date": day,
                    "project_identifier": record.get("project_identifier"),
                    "resource": record.get("resource"),
                    "num_jobs": jobs,
                    "total_wait_seconds": wait,
                    "mean_wait_seconds": wait / jobs if jobs else None,
                    "distinct_users": len(daily.get("user_job_counts") or {}),
                }
            )

    if not rows:
        return pl.DataFrame()
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col("date").str.to_date(strict=False))
        .sort("date", "project_identifier")
    )


#: Binary multipliers for the sizes the storage collector writes. Its "GB" is a
#: GiB: it reports ``"100.00 GB"`` for the same home quota ``lfs quota -h``
#: calls ``100G``, and a 1000-based reading of that figure would be 93.13 GiB.
#: Only the absolute views depend on the choice -- ``fill_pct`` divides two
#: figures carrying the same unit, so it is base-independent either way.
_SIZE_UNITS = {
    "B": 1,
    "KB": 1024**1,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
    "PB": 1024**5,
}

#: The same units, smallest first, for writing a size back out. Taken from the
#: table above rather than repeated, so parsing and rendering cannot drift.
_BYTE_UNITS = tuple(_SIZE_UNITS)

_SIZE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGTP]?B)\s*$", re.IGNORECASE)

#: The filesystems the collector reports a project-wide quota for, as opposed to
#: a per-user one. Everything else in ``user_quotas`` is charged to a person.
PROJECT_FILESYSTEMS = ("projects",)

_STORAGE_SCHEMA = {
    "observed_at": pl.String,
    "date": pl.Date,
    "month": pl.Date,
    "kind": pl.String,
    "project_code": pl.String,
    "username": pl.String,
    "filesystem": pl.String,
    "usage_bytes": pl.Float64,
    "limit_bytes": pl.Float64,
    "fill_pct": pl.Float64,
}


def _size_bytes(text: object) -> float | None:
    """``"1.50 TB"`` as a number of bytes, or ``None`` if it is not a size.

    Returns ``None`` rather than raising. This parses a free-form field inside
    a free-form blob, and one unrecognised string should blank one cell rather
    than take down the whole report.
    """
    if not isinstance(text, str):
        return None
    match = _SIZE.match(text)
    if match is None:
        return None
    return float(match.group(1)) * _SIZE_UNITS[match.group(2).upper()]


def humanise_bytes(value: object) -> str:
    """A byte count written the way the collector wrote it: ``"46.79 GB"``.

    The inverse of :func:`_size_bytes`, and the only place the 1024-based
    convention is spelled out for display -- the CLI table and the figure
    hovers both come here rather than each rounding in their own direction.
    """
    if not isinstance(value, int | float):
        return ""
    size = float(value)
    # Every unit but the last: the loop divides its way down to petabytes and
    # then stops, since there is nothing above them to scale into.
    for unit in _BYTE_UNITS[:-1]:
        if abs(size) < 1024:
            return f"{size:,.0f} B" if unit == "B" else f"{size:,.2f} {unit}"
        size /= 1024
    return f"{size:,.2f} {_BYTE_UNITS[-1]}"


def storage_samples(
    source: Snapshot | WaldurClient,
    *,
    customer: str | None = None,
    scope: bool = True,
) -> pl.DataFrame:
    """Every quota reading in the snapshot, one row per scope, filesystem and sample.

    ``openportal-project-storage-reports`` returns one row per project, resource
    and month, with a ``report`` blob holding quota readings. Like :func:`queue`
    this reshapes rather than selects, and it is the frame both storage reports
    and both storage figures are built from.

    **Two shapes share the blob.** A finished month carries ``daily_reports``,
    a dictionary keyed by date; the month still in progress carries no such
    dictionary at all, only the top-level snapshot. Both are read here, which
    matters twice over: the top-level snapshot of a finished month is taken
    *after* its last daily entry, so reading it recovers a final day the daily
    dictionary alone would lose, and it is the only reading the open month has.

    **A day can be sampled more than once.** The same filesystem is reported
    under each ``resource`` the project holds, by collectors that run minutes
    apart, so two readings of one day legitimately disagree by whatever was
    written in between. They are kept as separate samples rather than
    deduplicated: storage is a property of the filesystem and not of the
    cluster, so these are repeat measurements of one quantity, and letting the
    monthly aggregation see all of them is what makes ``peak`` honest.

    ``fill_pct`` is ``usage_bytes`` over ``limit_bytes``, the only figure that
    compares across filesystems whose limits differ by two orders of magnitude.
    It is null where the limit is missing or zero rather than dividing by it.

    ``customer`` narrows the scope further to one organisation's projects, the
    way :func:`monthly` does, and like there it means nothing without the
    scope: ``scope=False`` is the whole machine, and drops it.
    """
    if not scope:
        customer = None
    # Snapshots taken before storage was pulled simply do not have the file,
    # and every caller here degrades to "no storage figures" rather than
    # failing -- re-snapshotting is a nine-minute job to ask of someone who
    # only wanted the node hours.
    try:
        frame = load(source, ["openportal-project-storage-reports"])[
            "openportal-project-storage-reports"
        ]
    except SnapshotError:
        return pl.DataFrame(schema=_STORAGE_SCHEMA)
    if frame.is_empty():
        return pl.DataFrame(schema=_STORAGE_SCHEMA)

    rows: list[dict[str, object]] = []
    for record in frame.iter_rows(named=True):
        payload = record.get("report")
        if isinstance(payload, str):
            payload = json.loads(payload) if payload else {}
        if not isinstance(payload, dict):
            continue

        year, month = record.get("year"), record.get("month")
        stamp = f"{year}-{month:02d}" if year and month else None
        readings = [*(payload.get("daily_reports") or {}).items(), (None, payload)]
        for day, reading in readings:
            if not isinstance(reading, dict):
                continue
            observed = reading.get("generated_at") or ""
            when = day or observed[:10]
            # The row's own year and month are what the API says it describes;
            # a reading dated outside them would put a day in the wrong column.
            if not when or (stamp and when[:7] != stamp):
                continue

            identifier = reading.get("project") or record.get("project_identifier") or ""
            code = identifier.split(".")[0] or None

            for filesystem, quota in (reading.get("project_quotas") or {}).items():
                rows.append(_storage_row(observed, when, "project", code, None, filesystem, quota))
            for who, quotas in (reading.get("user_quotas") or {}).items():
                parts = who.split(".")
                for filesystem, quota in (quotas or {}).items():
                    rows.append(
                        _storage_row(observed, when, "user", code, parts[0], filesystem, quota)
                    )

    if not rows:
        return pl.DataFrame(schema=_STORAGE_SCHEMA)

    samples = (
        pl.DataFrame(rows)
        .with_columns(pl.col("date").str.to_date(strict=False))
        .with_columns(month=pl.col("date").dt.truncate("1mo"))
        .with_columns(
            fill_pct=pl.when(pl.col("limit_bytes") > 0)
            .then(100 * pl.col("usage_bytes") / pl.col("limit_bytes"))
            .otherwise(None)
        )
        .drop_nulls("date")
        .select(list(_STORAGE_SCHEMA))
        .sort("observed_at", "kind", "project_code", "username", "filesystem")
    )
    if scope:
        projects = in_scope(source)
        if customer is not None and "customer_name" in projects.columns:
            projects = projects.filter(pl.col("customer_name") == customer)
        samples = samples.filter(pl.col("project_code").is_in(projects["project_code"].to_list()))
    return samples


def _storage_row(
    observed: str,
    when: str,
    kind: str,
    code: str | None,
    username: str | None,
    filesystem: str,
    quota: object,
) -> dict[str, object]:
    """One reading, with both sizes already in bytes."""
    quota = quota if isinstance(quota, dict) else {}
    return {
        "observed_at": observed,
        "date": when,
        "kind": kind,
        "project_code": code,
        "username": username,
        "filesystem": filesystem,
        "usage_bytes": _size_bytes(quota.get("usage")),
        "limit_bytes": _size_bytes(quota.get("limit")),
    }


def storage(source: Snapshot | WaldurClient, *, scope: bool = True) -> pl.DataFrame:
    """How full every quota was when it was last read, fullest first.

    The current-state view, and the one the terminal is good at: one row per
    project or person per filesystem, carrying the most recent reading of it.
    Sorted by ``fill_pct`` descending, so the answer to "who is about to run
    out" is the top of the table rather than something to be searched for.

    ``observed_at`` is part of the answer rather than decoration. These
    readings are only as current as the collector behind them, which is not
    the same thing as how fresh the snapshot is, and a row whose reading is
    months old should be read as history rather than as the state of the disk.
    """
    return storage_now(storage_samples(source, scope=scope))


def storage_now(samples: pl.DataFrame) -> pl.DataFrame:
    """:func:`storage`, over a frame of samples that has already been parsed.

    Split out so a caller wanting both storage views -- the visual report wants
    the heatmap and the table under it -- can pay for one parse of the endpoint
    and one read of the allocations rather than two, and so that the two views
    are guaranteed to have come from the same read.
    """
    if samples.is_empty():
        return samples.drop("month")
    keys = ["kind", "project_code", "username", "filesystem"]
    return (
        samples.sort("observed_at")
        .group_by(keys, maintain_order=False)
        .agg(pl.col("observed_at", "date", "usage_bytes", "limit_bytes", "fill_pct").last())
        .select(
            "kind",
            "project_code",
            "username",
            "filesystem",
            "usage_bytes",
            "limit_bytes",
            "fill_pct",
            "date",
        )
        .sort(
            "fill_pct",
            "kind",
            "project_code",
            "username",
            descending=[True, False, False, False],
        )
    )


def storage_monthly(source: Snapshot | WaldurClient, *, scope: bool = True) -> pl.DataFrame:
    """Each quota reduced to one row per month, for the storage heatmaps.

    Disk usage is a *level*, not a flow: unlike node hours there is nothing to
    sum, and a month has to be summarised by choosing a statistic rather than
    by adding one up. Three are offered, and which one is right depends on the
    question:

    ``peak``
        The fullest the quota got. The one that decides whether writes failed,
        and the default everywhere in this package.
    ``end``
        The last reading of the month, which is the level carried into the
        next one.
    ``median``
        The typical level, robust to a single day's spike.

    A mean is deliberately absent. Averaging a slowly drifting level is close
    to meaningless -- it is the mean of a random walk -- and it hides the peak,
    which is the part that actually breaks jobs; the median covers the same
    "ignore one bad day" ground without that.

    ``limit_bytes`` is the quota **every** reading in the month agreed on, and
    ``null`` when they did not -- a quota raised mid-month, or one reading whose
    limit was not a size. That is stricter than the last limit read, and it is
    what keeps the three statistics honest. Each is chosen independently: the
    peak fill and the peak size can be different readings, and the medians are
    interpolated between two. While one limit holds all month that costs
    nothing, because ``fill_pct`` is then a fixed multiple of ``usage_bytes``
    and both the maximum and the median carry straight through it -- so
    ``peak_fill_pct`` really is ``peak_bytes`` over that limit. The moment the
    limit moves, that stops being true, and nothing downstream may write the
    three as one reading. A ``null`` here is how they are told not to.

    ``is_partial`` means *fewer daily readings than the month has days*, which
    is a different claim from the ``is_partial`` of :func:`monthly_totals` --
    there it marks the month the snapshot was taken in. Storage readings lag
    their own collector, so freshness here cannot be derived from the snapshot
    date, and a month can be short of readings at either end: collection
    starting mid-month, stopping mid-month, or simply missing a day.
    """
    return storage_by_month(storage_samples(source, scope=scope))


def storage_by_month(samples: pl.DataFrame) -> pl.DataFrame:
    """:func:`storage_monthly`, over a frame of samples already parsed.

    The counterpart of :func:`storage_now`, and split out for the same reason.
    """
    if samples.is_empty():
        return pl.DataFrame(
            schema={
                "month": pl.Date,
                "kind": pl.String,
                "project_code": pl.String,
                "username": pl.String,
                "filesystem": pl.String,
                "peak_fill_pct": pl.Float64,
                "end_fill_pct": pl.Float64,
                "median_fill_pct": pl.Float64,
                "peak_bytes": pl.Float64,
                "end_bytes": pl.Float64,
                "median_bytes": pl.Float64,
                "limit_bytes": pl.Float64,
                "days_observed": pl.Int64,
                "samples": pl.Int64,
                "is_partial": pl.Boolean,
            }
        )

    keys = ["month", "kind", "project_code", "username", "filesystem"]
    return (
        samples.sort("observed_at")
        .group_by(keys, maintain_order=False)
        .agg(
            peak_fill_pct=pl.col("fill_pct").max(),
            end_fill_pct=pl.col("fill_pct").last(),
            median_fill_pct=pl.col("fill_pct").median(),
            peak_bytes=pl.col("usage_bytes").max(),
            end_bytes=pl.col("usage_bytes").last(),
            median_bytes=pl.col("usage_bytes").median(),
            # The limit the month *held*, not the last one read. See the
            # docstring: it is what makes the three statistics above safe to
            # write beside it as a fraction of one quota.
            limit_bytes=pl.when(pl.col("limit_bytes").n_unique() == 1)
            .then(pl.col("limit_bytes").first())
            .otherwise(pl.lit(None, dtype=pl.Float64)),
            days_observed=pl.col("date").n_unique().cast(pl.Int64),
            samples=pl.len().cast(pl.Int64),
        )
        .with_columns(
            is_partial=pl.col("days_observed")
            < pl.col("month").map_elements(
                lambda when: calendar.monthrange(when.year, when.month)[1],
                return_dtype=pl.Int64,
            )
        )
        .sort("month", "kind", "project_code", "username", "filesystem")
    )


#: Report name -> callable, for the CLI and for discovery.
REPORTS = {
    "credits": credits,
    "allocations": allocations,
    "membership": membership,
    "utilisation": utilisation,
    "monthly": monthly,
    "monthly-totals": monthly_totals,
    "reconcile": reconcile,
    "user-usage": user_usage,
    "queue": queue,
    "storage": storage,
    "storage-monthly": storage_monthly,
}

#: Reports whose signature accepts ``scope=``, so the CLI can offer ``--all``.
SCOPED = {
    "allocations",
    "membership",
    "user-usage",
    "monthly",
    "monthly-totals",
    "reconcile",
    "storage",
    "storage-monthly",
}
