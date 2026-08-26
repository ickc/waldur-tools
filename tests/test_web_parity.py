"""The contract between the Python reports and the browser extension.

`web/` reimplements a subset of :mod:`waldur_tools.reports` in JavaScript,
because a browser cannot run this package. Two implementations of the same
arithmetic drift, and the drift is silent: nothing about a wrong
``mean_monthly_allocation`` looks wrong on a chart.

So the numbers are pinned by a golden file. This test runs the Python reports
over the ordinary test fixtures and writes two artefacts:

``web/tests/fixture.json``
    The inputs, in the shape the API returns them.
``web/tests/expected.json``
    What the Python implementation makes of those inputs.

Both are committed, and `web/tests/parity.test.mjs` runs the JavaScript over the
same fixture and asserts it lands on the same expected values. The Python side
is therefore the definition and the JavaScript side has to follow: change a
formula here and this test rewrites ``expected.json`` and fails until you commit
it, and the node test then fails until the JavaScript agrees again.

**Only formulas are pinned, not presentation.** The figure builders in
:mod:`waldur_tools.viz` and ``web/src/figures.js`` are two renderings of the
same series and are allowed to differ; what may not differ is any number either
of them draws.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from waldur_tools import reports, viz
from waldur_tools.cache import Snapshot
from waldur_tools.frames import to_frame

WEB_TESTS = Path(__file__).resolve().parents[1] / "web" / "tests"

#: The date the fixture is read as having been taken on. Fixed, because
#: ``is_partial`` and the open-ended award horizon both depend on it, and a test
#: whose expectations change at midnight is not a test. February 2026 is chosen
#: so that one of the fixture's two months is the month in progress.
AS_OF = date(2026, 2, 20)

#: The fixture's organisation. The real default is elsewhere; what matters here
#: is that the filter is exercised, and the fixture carries a second customer
#: for it to exclude.
CUSTOMER = "UKRI"


@pytest.fixture
def snapshot(
    tmp_path,
    allocations,
    associations,
    accounting_summary,
    invoices,
    usage_reports,
    storage_reports,
    user_usage_rows,
    users,
    projects,
    customers,
):
    snap = Snapshot.create(tmp_path, "parity")
    snap.write("users", to_frame(users))
    snap.write("customers", to_frame(customers))
    snap.write("projects", to_frame(projects))
    snap.write("invoices", to_frame(invoices))
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-associations", to_frame(associations))
    snap.write("openportal-accounting-summary", to_frame(accounting_summary))
    snap.write("openportal-project-usage-reports", to_frame(usage_reports))
    snap.write("openportal-project-storage-reports", to_frame(storage_reports))
    snap.write("openportal-allocation-user-usage", to_frame(user_usage_rows))
    snap.write_meta({})
    # `reports.as_of` reads this, and everything date-dependent below follows
    # from it -- so it is pinned rather than left at "whenever the suite ran".
    (snap.path / "meta.json").write_text(
        json.dumps({"created": f"{AS_OF.isoformat()}T00:00:00+00:00", "endpoints": {}}),
        encoding="utf-8",
    )
    return snap


def _plain(value: object, column: str) -> object:
    """A polars value as something JSON and JavaScript both understand.

    Months are ``YYYY-MM`` on both sides -- strings sort, compare and key a Map
    correctly, which a date object does none of in JavaScript.
    """
    if isinstance(value, date):
        return value.strftime("%Y-%m") if column == "month" else value.isoformat()
    return value


def records(frame: pl.DataFrame) -> list[dict[str, object]]:
    """A frame as JSON records, in an order that does not change between runs.

    Sorted on the row's own serialisation, because several of the frames below
    do not have a defined order and would otherwise rewrite the golden file
    every other run: ``in_scope`` ends in ``unique()`` and the rest end in a
    ``group_by``, neither of which polars promises to emit in any particular
    order, and both of which are free to differ from one execution to the next.

    Nothing is lost by sorting. The node side compares these as multisets for
    the same reason, and the three orderings that *are* part of the contract --
    months ascending, awards by rate, project totals ascending -- are asserted
    directly at the bottom of ``parity.test.mjs``, on the JavaScript's own
    output rather than on this file.
    """
    rows = [
        {column: _plain(value, column) for column, value in row.items()}
        for row in frame.iter_rows(named=True)
    ]
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def build_expected(snapshot: Snapshot) -> dict[str, object]:
    """Every formula the browser reimplements, evaluated by the Python."""
    scope = reports.in_scope(snapshot)
    codes = scope["project_code"].to_list()
    per_project = reports.monthly(snapshot, customer=CUSTOMER)
    totals = reports.monthly_totals(snapshot, customer=CUSTOMER)
    months = totals["month"].to_list()
    allocation = reports.allocations(snapshot, customer=CUSTOMER)
    held, spare = viz.credit_position(snapshot, CUSTOMER)

    return {
        "as_of": AS_OF.isoformat(),
        "customer": CUSTOMER,
        "nodes": reports.TOTAL_NODES,
        "share": reports.DEFAULT_SHARE,
        "in_scope": records(scope),
        "monthly": records(per_project),
        "monthly_totals": records(totals),
        "allocations": records(allocation),
        "committed": viz.committed(allocation, months, AS_OF),
        "projects_existing": records(viz.projects_existing(snapshot, months, CUSTOMER)),
        "credit_position": [held, spare],
        "invoiced": records(reports.invoiced(snapshot, customer=CUSTOMER)),
        "reconcile": records(reports.reconcile(snapshot, customer=CUSTOMER)),
        "queue_monthly": records(viz.queue_monthly(snapshot, codes)),
        "storage_current": records(reports.storage(snapshot)),
        "storage_monthly": records(reports.storage_monthly(snapshot)),
        "people_with_access": viz.people_with_access(snapshot, codes),
        "ranked_bands": records(
            viz._ranked(per_project)
            .select("month", "project_code", "band")
            .sort("month", "project_code")
        ),
    }


def build_fixture(
    allocations,
    associations,
    accounting_summary,
    invoices,
    usage_reports,
    storage_reports,
    user_usage_rows,
    users,
    projects,
    customers,
) -> dict[str, object]:
    """The inputs, exactly as the API hands them over.

    Straight from the shared fixtures rather than a second set written for the
    browser: the traps they encode -- one allocation per service, blanked
    association rows, decimal strings, an invoice whose ``total`` is zero while
    it billed thousands -- are the ones the JavaScript has to survive too.
    """
    return {
        "openportal-allocations": allocations,
        "openportal-associations": associations,
        "openportal-accounting-summary": accounting_summary,
        "openportal-allocation-user-usage": user_usage_rows,
        "openportal-project-usage-reports": usage_reports,
        "openportal-project-storage-reports": storage_reports,
        "invoices": invoices,
        "customers": customers,
        "projects": projects,
        "users": users,
    }


def _golden(name: str, payload: object) -> None:
    """Assert a generated artefact matches the committed one, rewriting if not.

    Rewriting *and* failing is deliberate. A formula change should leave the
    working tree holding the new expectations, so the node test immediately
    tells you which parts of the JavaScript have not followed -- and should
    still fail here, so the regenerated file cannot reach main uncommitted.
    """
    target = WEB_TESTS / name
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == text:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    pytest.fail(
        f"web/tests/{name} was out of date and has been rewritten. Review the diff, "
        "make web/src/reports.js agree with it, and commit both."
    )


def test_fixture_is_current(
    allocations,
    associations,
    accounting_summary,
    invoices,
    usage_reports,
    storage_reports,
    user_usage_rows,
    users,
    projects,
    customers,
):
    _golden(
        "fixture.json",
        build_fixture(
            allocations,
            associations,
            accounting_summary,
            invoices,
            usage_reports,
            storage_reports,
            user_usage_rows,
            users,
            projects,
            customers,
        ),
    )


def test_expected_is_current(snapshot):
    _golden("expected.json", build_expected(snapshot))


def test_the_fixture_exercises_what_it_claims_to(snapshot):
    """Guard the guard: a golden file over trivial data proves nothing.

    Each assertion below names a behaviour the JavaScript could plausibly get
    wrong, and fails here if the fixture stops covering it.
    """
    expected = build_expected(snapshot)

    totals = expected["monthly_totals"]
    # Sorted, not indexed: `records` orders these canonically rather than by
    # month, and asserting on that order would be asserting on the serialiser.
    assert sorted(row["month"] for row in totals) == ["2025-01", "2026-02"]
    # A partial month, so `is_partial` is not vacuously false everywhere.
    assert {row["month"]: row["is_partial"] for row in totals} == {
        "2025-01": False,
        "2026-02": True,
    }
    # Two projects in one month, so the per-project split has something to split.
    assert sum(row["month"] == "2026-02" for row in expected["monthly"]) == 2
    # Another organisation's usage the scope has to drop: Carol's 99 node hours
    # must appear in neither total.
    assert all(row["node_hours"] < 99 for row in totals)
    # A project with an award and one without a usable one, so the heatmap's
    # relative view has both a denominator and a blank.
    rates = [row["mean_monthly_allocation"] for row in expected["allocations"]]
    assert any(rate is not None for rate in rates)
    # An invoice for a different customer, filtered out rather than netted off.
    assert all(row["status"] == "ok" for row in expected["reconcile"])
    # Associations that are out of scope and associations that are blanked.
    assert expected["people_with_access"] == 2

    # -- storage ------------------------------------------------------------
    monthly_storage = expected["storage_monthly"]
    # The out-of-scope project's disks stay off our page entirely.
    assert all(row["project_code"] != "zzz9" for row in monthly_storage)
    assert all(row["project_code"] != "zzz9" for row in expected["storage_current"])
    # A finished month and the month in progress, so the daily dictionary and
    # the bare snapshot are both exercised.
    assert sorted({row["month"] for row in monthly_storage}) == ["2025-01", "2026-02"]

    january = next(
        row
        for row in monthly_storage
        if row["month"] == "2025-01" and row["kind"] == "project" and row["project_code"] == "abc1"
    )
    # Three days from two dictionaries: the 29th and 30th out of `daily_reports`
    # and the 31st out of the top-level snapshot, which is the only place the
    # last day of a month is ever reported.
    assert january["days_observed"] == 3
    assert january["is_partial"] is True
    # Six samples over those three days, because two collectors reported each.
    assert january["samples"] == 6
    # Peak and median differ, so a figure defaulting to the wrong one is visible.
    assert january["peak_fill_pct"] != january["median_fill_pct"]

    # The end of a month is the last sample, not the last readable one: bob's
    # home was read three days running and only the last came back unparseable,
    # so `end` has to answer "unknown" rather than repeat the 30th beside a
    # limit taken from the 31st.
    bob = next(
        row
        for row in monthly_storage
        if row["month"] == "2025-01" and row["username"] == "bob" and row["filesystem"] == "home"
    )
    assert bob["end_fill_pct"] is None
    assert bob["end_bytes"] is None
    assert bob["peak_bytes"] is not None

    # A quota raised mid-month leaves the month without one: the fill statistics
    # and the size statistics are then taken against different limits, and
    # `limit_bytes` goes null rather than picking one and implying the rest
    # divide by it.
    scratch = next(
        row
        for row in monthly_storage
        if row["month"] == "2025-01" and row["username"] == "alice"
        and row["filesystem"] == "scratch"
    )
    assert scratch["limit_bytes"] is None
    assert scratch["median_fill_pct"] is not None

    # A limit that is not a size blanks the percentage rather than dividing.
    unlimited = [row for row in monthly_storage if row["project_code"] == "abc2"]
    assert unlimited and all(row["peak_fill_pct"] is None for row in unlimited)
    # The February reading inside that January row was dropped, not misfiled.
    assert all(row["month"] == "2025-01" for row in unlimited)

    # Both filesystems, so the user figure's buttons have something to switch.
    assert {row["filesystem"] for row in monthly_storage if row["kind"] == "user"} == {
        "home",
        "scratch",
    }
