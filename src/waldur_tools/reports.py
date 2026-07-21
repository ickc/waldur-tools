"""Analyses built on top of snapshotted (or live) endpoint data.

Every report takes a source -- a :class:`~waldur_tools.cache.Snapshot` or a live
:class:`~waldur_tools.client.WaldurClient` -- and returns a polars DataFrame, so
they compose in notebooks as readily as in the CLI.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import polars as pl

from .cache import load
from .frames import integral, numeric

if TYPE_CHECKING:
    from .cache import Snapshot
    from .client import WaldurClient


def credits(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """Credit burn-down per project, with a runway estimate.

    ``months_remaining`` divides the unspent balance by the current month's
    spend, so it answers "at this rate, when do we run dry?". It is null where
    nothing has been spent this month.
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
        .select(
            "project_name",
            "customer_name",
            "total_credits",
            "total_spend",
            "current_month_spend",
            "remaining",
            "used_pct",
            "months_remaining",
            "end_date",
        )
        .sort("months_remaining", nulls_last=True)
    )


def membership(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """Who has access to what: one row per user/service pairing.

    This is the ``count_users()`` example from ``gw4-isambard/rse-sharing``,
    except the allocation lookup is a join rather than an HTTP call per row.

    Two things about the real data shape this. Associations reference far more
    allocations than a typical token can fetch individually, and fetching an
    invisible one returns 404 -- which is why the upstream example, which
    requests each allocation individually, cannot complete. A left join
    degrades instead: unresolved rows keep their username and sort last, and
    ``resolved`` says which is which. Some
    associations also carry no username at all, so those sort last too
    rather than leading the report with a wall of blanks.
    """
    data = load(source, ["openportal-associations", "openportal-allocations"])
    associations, allocations = data["openportal-associations"], data["openportal-allocations"]
    if associations.is_empty():
        return associations

    lookup = allocations.select(
        allocation="url",
        service_name="service_name",
        project_name="project_name",
        customer_name="customer_name",
    )
    return (
        associations.join(lookup, on="allocation", how="left")
        .with_columns(resolved=pl.col("service_name").is_not_null())
        .select(
            "username",
            "service_name",
            "project_name",
            "customer_name",
            "groupname",
            "resolved",
        )
        .sort(
            [pl.col("resolved").not_(), pl.col("username"), pl.col("service_name")],
            nulls_last=True,
        )
    )


def utilisation(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """Allocation headroom: node usage against the configured node limit.

    Sorted with the emptiest active allocations first -- those are the ones
    holding capacity nobody is using.
    """
    frame = load(source, ["openportal-allocations"])["openportal-allocations"]
    if frame.is_empty():
        return frame

    frame = numeric(frame, "node_usage", "node_limit")
    return (
        frame.with_columns(
            used_pct=100 * pl.col("node_usage") / pl.col("node_limit").replace(0.0, None)
        )
        .select(
            "project_name",
            "service_name",
            "customer_name",
            "backend_id",
            "node_usage",
            "node_limit",
            "used_pct",
            "is_active",
            "state",
        )
        .sort("used_pct", nulls_last=True)
    )


def user_usage(source: Snapshot | WaldurClient, *, year: int | None = None) -> pl.DataFrame:
    """Per-user node usage aggregated across allocations, heaviest first."""
    frame = load(source, ["openportal-allocation-user-usage"])["openportal-allocation-user-usage"]
    if frame.is_empty():
        return frame

    frame = integral(numeric(frame, "node_usage"), "year", "month")
    if year is not None:
        frame = frame.filter(pl.col("year") == year)

    return (
        frame.group_by("username", "full_name")
        .agg(
            total_node_usage=pl.col("node_usage").sum(),
            months_active=pl.col("node_usage").filter(pl.col("node_usage") > 0).len(),
            first_year=pl.col("year").min(),
            last_year=pl.col("year").max(),
        )
        .sort("total_node_usage", descending=True)
    )


def queue(source: Snapshot | WaldurClient) -> pl.DataFrame:
    """Daily job counts and mean queue wait, unpacked from the usage reports.

    The ``report`` payload nests a per-day dictionary inside each monthly row;
    this flattens it to one row per project/resource/day.
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


#: Report name -> callable, for the CLI and for discovery.
REPORTS = {
    "credits": credits,
    "membership": membership,
    "utilisation": utilisation,
    "user-usage": user_usage,
    "queue": queue,
}
