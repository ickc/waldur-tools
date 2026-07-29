"""Per-job records read from SLURM's accounting database, via ``sacct``.

**Why this exists at all.** Everything else in this package comes from the
Waldur portal, and the portal's finest-grained view of job activity is
``openportal-project-usage-reports``: one blob per project, resource and month,
nesting a dictionary per *day* that carries ``num_jobs``, ``total_wait_seconds``
and consumed resource-seconds. That is enough to say how many jobs ran and how
long they waited on average, and it is the ceiling of what the API can answer.
It has no record of an individual job, and in particular no record of what a
job *asked for* -- the ``--nodes`` and ``--time`` in the batch script. Those are
the two numbers that decide where a job lands in the queue, so without them
"why did this wait?" has no answer.

``sacct`` has them. The cost is that this module, alone in the package, only
works while sitting on the cluster: it shells out to a local binary rather than
reading the API. So it is kept deliberately separate -- a distinct command
writing a distinct file -- and everything that consumes it treats it as
optional. A report built without it loses the job-shape figures and keeps the
rest, exactly as it did before this module existed.

**Visibility.** ``sacct -a`` returns jobs for every account, not just the
caller's. That is the default on this deployment and is what makes an
organisation-wide report possible from an ordinary user account; there is no
coordinator role involved. If a site disabled it, :func:`capture` would return
only the caller's own jobs and would not be able to tell that it had.
"""

from __future__ import annotations

import subprocess
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: The ``sacct`` fields to request, in order. ``-P`` makes the output
#: pipe-separated and ``-X`` collapses job steps into the allocation, so one
#: line is one submitted job rather than one line per ``srun`` inside it.
#:
#: The ``Raw`` variants are not cosmetic. ``Timelimit`` and ``Elapsed`` format
#: as ``DD-HH:MM:SS``, whose day part is present only when it is non-zero;
#: ``TimelimitRaw`` is plain minutes and ``ElapsedRaw`` plain seconds, so
#: neither needs a parser that can get the ambiguous cases wrong.
FIELDS: tuple[str, ...] = (
    "JobIDRaw",
    "User",
    "Account",
    "Partition",
    "State",
    "Submit",
    "Start",
    "TimelimitRaw",
    "ElapsedRaw",
    "ReqNodes",
    "ReqCPUS",
    "AllocNodes",
)

#: Values ``sacct`` prints in a timestamp column for a job that never reached
#: that point -- one still pending, or cancelled before it started.
NOT_A_TIME: frozenset[str] = frozenset({"", "Unknown", "None", "N/A"})

#: Non-numeric ``TimelimitRaw`` values, for jobs that inherited the partition's
#: limit or asked for none. Left null rather than guessed at: substituting the
#: partition limit would invent a request the user did not make.
NOT_A_LIMIT: frozenset[str] = frozenset({"", "Partition_Limit", "UNLIMITED", "INVALID"})


#: The cluster to account against, pinned rather than inherited.
#:
#: Isambard runs two clusters in one slurmdbd -- ``i3`` and the much smaller
#: ``i3macs`` -- and ``sacct`` defaults to whichever one the login node you are
#: sitting on belongs to. Every other figure in this package describes ``i3``
#: alone, because that is all the portal meters: no account on ``i3macs``
#: carries a ``GrpTRESMins``, its marketplace component usages all read ``0.0``,
#: and none of it is invoiced. So leaving the cluster to the ambient environment
#: would let the job figures quietly describe a different machine from the
#: node-hour figures, depending on where the capture was taken. Pass
#: ``cluster="all"`` to override, and ``sacctmgr show cluster`` to see what
#: exists.
DEFAULT_CLUSTER = "i3"

#: Where ``waldur-tools slurm-jobs`` writes, and where ``viz`` looks. It sits in
#: the cache root beside the snapshot directories rather than inside one,
#: because it is not part of any snapshot: a snapshot is an immutable record of
#: what the portal said at one instant, and this is a re-derivable local capture
#: of something the portal never said at all. Re-running overwrites it, which is
#: safe -- ``sacct`` keeps the history, so a later capture is a superset.
JOBS_FILENAME = "slurm-jobs.parquet"


class SlurmError(RuntimeError):
    """``sacct`` could not be run, or answered with an error."""


def accounts(codes: Sequence[str]) -> list[str]:
    """SLURM account names for a list of Waldur project codes.

    Isambard names an account for a project ``abc1`` as ``brics.abc1``, which is
    the same ``brics.<code>`` that arrives as an allocation's ``groupname``
    (see :func:`waldur_tools.reports.project_code`). The legacy internal
    projects are the exception -- ``legacy-internal-1`` is its own account with no
    prefix -- so a code that already contains a ``-`` is passed through.
    """
    return [code if "-" in code else f"brics.{code}" for code in codes]


def capture(
    codes: Sequence[str],
    *,
    start: date | str = "2024-01-01",
    end: date | str = "now",
    cluster: str = DEFAULT_CLUSTER,
    timeout: float = 300.0,
) -> pl.DataFrame:
    """Run ``sacct`` for these projects and return one row per submitted job.

    ``start`` defaults to well before Isambard 3 opened to users, so the default
    call is "everything there is". ``sacct`` needs an explicit ``-S`` -- without
    one it reports only jobs still running or ended today -- and widening it
    costs little, since the database is indexed on time and the result set is
    tens of thousands of rows rather than millions.

    ``cluster`` is pinned to :data:`DEFAULT_CLUSTER` rather than left to the
    login node's own; see that constant for why.
    """
    if not codes:
        return _empty()

    command = [
        "sacct",
        "--allusers",
        "--allocations",
        "--noheader",
        "--parsable2",
        f"--clusters={cluster}",
        f"--starttime={start}",
        f"--endtime={end}",
        f"--accounts={','.join(accounts(codes))}",
        f"--format={','.join(FIELDS)}",
    ]
    try:
        # Fixed argv and no shell: nothing here is interpolated from anything
        # the portal returned except the account names, which are passed as one
        # argument rather than spliced into a command line.
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as error:
        raise SlurmError(
            "sacct is not on PATH. Job-shape figures need to be captured while "
            "logged in to the cluster; the rest of the report does not."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SlurmError(f"sacct did not finish within {timeout:.0f}s") from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise SlurmError(f"sacct failed: {detail}")
    return parse(completed.stdout)


def parse(text: str) -> pl.DataFrame:
    """Turn ``sacct --parsable2`` output in :data:`FIELDS` order into a frame.

    Split from :func:`capture` so the reshaping can be tested against fixed
    text, which is the part that has edge cases in it; running the binary does
    not.

    Four columns are derived here rather than by a caller, because each one
    needs a decision about missing data that should be made once:

    ``wait_seconds``
        ``Start - Submit``. Null for a job that never started, not zero -- a
        cancelled job did not wait no time at all, it has no wait to report.
    ``requested_node_hours``
        ``ReqNodes * TimelimitRaw``, the size of the reservation the scheduler
        was asked to find. This is the job as the queue sees it, and it is
        deliberately *not* the same as what the job went on to consume: a job
        that asks for 24 hours and exits in one still had to wait for a 24-hour
        hole to open.
    ``node_hours``
        ``AllocNodes * ElapsedRaw``, what the job actually cost. Present for
        comparison with the requested figure; the ratio between them is how
        much of the machine's time is being reserved and not used.
    ``state``
        Truncated at the first space, so the twenty-odd distinct
        ``CANCELLED by <uid>`` values collapse to one ``CANCELLED``. The uid is
        who cancelled it, which is not a property of the job.
    """
    rows = [line.split("|") for line in text.splitlines() if line.strip()]
    width = len(FIELDS)
    rows = [row for row in rows if len(row) == width]
    if not rows:
        return _empty()

    frame = pl.DataFrame(rows, schema=list(FIELDS), orient="row")

    def timestamp(column: str) -> pl.Expr:
        return (
            pl.when(pl.col(column).is_in(list(NOT_A_TIME)))
            .then(None)
            .otherwise(pl.col(column))
            .str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False)
        )

    limit_minutes = (
        pl.when(pl.col("TimelimitRaw").is_in(list(NOT_A_LIMIT)))
        .then(None)
        .otherwise(pl.col("TimelimitRaw"))
        .cast(pl.Int64, strict=False)
    )

    frame = frame.with_columns(
        job_id=pl.col("JobIDRaw").cast(pl.Int64, strict=False),
        # `User` is `<unix name>.<project code>`, the same shape as the
        # association usernames the rest of the package parses.
        unix_username=pl.col("User").str.split(".").list.first(),
        project_code=pl.col("User").str.split(".").list.get(1, null_on_oob=True),
        account=pl.col("Account"),
        partition=pl.col("Partition"),
        state=pl.col("State").str.split(" ").list.first(),
        submit=timestamp("Submit"),
        start=timestamp("Start"),
        requested_minutes=limit_minutes,
        elapsed_seconds=pl.col("ElapsedRaw").cast(pl.Int64, strict=False),
        requested_nodes=pl.col("ReqNodes").cast(pl.Int64, strict=False),
        requested_cpus=pl.col("ReqCPUS").cast(pl.Int64, strict=False),
        allocated_nodes=pl.col("AllocNodes").cast(pl.Int64, strict=False),
    )
    return (
        frame.with_columns(
            month=pl.col("submit").dt.date().dt.truncate("1mo"),
            wait_seconds=(pl.col("start") - pl.col("submit")).dt.total_seconds(),
            requested_node_hours=pl.col("requested_nodes") * pl.col("requested_minutes") / 60,
            node_hours=pl.col("allocated_nodes") * pl.col("elapsed_seconds") / 3600,
        )
        .select(
            "job_id",
            "unix_username",
            "project_code",
            "account",
            "partition",
            "state",
            "month",
            "submit",
            "start",
            "wait_seconds",
            "requested_nodes",
            "requested_minutes",
            "requested_node_hours",
            "requested_cpus",
            "allocated_nodes",
            "elapsed_seconds",
            "node_hours",
        )
        .sort("submit", "job_id", nulls_last=True)
    )


def load_jobs(path: Path | None, *, root: Path | None = None) -> pl.DataFrame:
    """Read a captured jobs table, or return an empty frame if there is none.

    Empty rather than raising, because every consumer is optional by design.
    ``path`` names a file explicitly; otherwise :data:`JOBS_FILENAME` in the
    cache ``root`` is used if it happens to be there.
    """
    if path is None and root is not None:
        path = root / JOBS_FILENAME
    if path is None or not path.exists():
        return _empty()
    return pl.read_parquet(path)


def _empty() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "job_id": pl.Int64,
            "unix_username": pl.String,
            "project_code": pl.String,
            "account": pl.String,
            "partition": pl.String,
            "state": pl.String,
            "month": pl.Date,
            "submit": pl.Datetime,
            "start": pl.Datetime,
            "wait_seconds": pl.Int64,
            "requested_nodes": pl.Int64,
            "requested_minutes": pl.Int64,
            "requested_node_hours": pl.Float64,
            "requested_cpus": pl.Int64,
            "allocated_nodes": pl.Int64,
            "elapsed_seconds": pl.Int64,
            "node_hours": pl.Float64,
        }
    )
