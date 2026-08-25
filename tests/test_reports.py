from __future__ import annotations

import math
from datetime import date

import polars as pl
import pytest

from conftest import API_URL, invoice
from waldur_tools import reports
from waldur_tools.cache import Snapshot
from waldur_tools.frames import to_frame


@pytest.fixture
def snapshot(
    tmp_path,
    allocations,
    associations,
    accounting_summary,
    invoices,
    usage_reports,
    user_usage_rows,
    users,
):
    snap = Snapshot.create(tmp_path, "test")
    snap.write("users", to_frame(users))
    snap.write("invoices", to_frame(invoices))
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-associations", to_frame(associations))
    snap.write("openportal-accounting-summary", to_frame(accounting_summary))
    snap.write("openportal-project-usage-reports", to_frame(usage_reports))
    snap.write("openportal-allocation-user-usage", to_frame(user_usage_rows))
    snap.write_meta({})
    return snap


# -- scope -------------------------------------------------------------------


def test_in_scope_deduplicates_projects_on_several_services(snapshot):
    """Project A has two allocations but is one project code."""
    frame = reports.in_scope(snapshot)
    assert sorted(frame["project_code"]) == ["abc1", "abc2"]


def test_project_code_parses_slurm_names(snapshot):
    frame = reports.membership(snapshot)
    assert frame.filter(frame["unix_username"] == "alice").row(0, named=True)["project_code"] == (
        "abc1"
    )


# -- credits -----------------------------------------------------------------


def test_credits_computes_runway(snapshot):
    frame = reports.credits(snapshot).sort("project_name")
    row = frame.row(0, named=True)
    assert row["remaining"] == pytest.approx(20000.0)
    assert row["used_pct"] == pytest.approx(100 / 3)
    assert row["months_remaining"] == pytest.approx(10.0)
    assert row["overspent"] is False


def test_credits_runway_is_null_without_spend(snapshot):
    frame = reports.credits(snapshot).sort("project_name")
    assert frame.row(1, named=True)["months_remaining"] is None


def test_credits_flags_overspend_with_negative_runway(tmp_path, accounting_summary):
    """A project past its credits reports a negative runway, not a null one."""
    snap = Snapshot.create(tmp_path, "over")
    over = {**accounting_summary[0], "total_spend": "42000.00", "current_month_spend": "2000.00"}
    snap.write("openportal-accounting-summary", to_frame([over]))
    snap.write_meta({})

    row = reports.credits(snap).row(0, named=True)
    assert row["overspent"] is True
    assert row["remaining"] == pytest.approx(-12000.00)
    assert row["months_remaining"] < 0


# -- membership --------------------------------------------------------------


def test_membership_joins_via_project_code_not_allocation_url(snapshot):
    """bob's own allocation is invisible; his project code still resolves it."""
    frame = reports.membership(snapshot)
    bob = frame.filter(frame["unix_username"] == "bob").row(0, named=True)
    assert bob["project_name"] == "Project B"
    assert bob["customer_name"] == "UKRI"


def test_membership_scopes_out_other_organisations(snapshot):
    frame = reports.membership(snapshot)
    assert sorted(frame["unix_username"]) == ["alice", "bob"]


def test_membership_collapses_one_row_per_service_into_one_pairing(snapshot):
    """alice holds two associations on one project; that is one pairing."""
    frame = reports.membership(snapshot)
    alice = frame.filter(frame["unix_username"] == "alice")
    assert alice.height == 1
    assert alice.row(0, named=True)["associations"] == 2
    assert alice.row(0, named=True)["full_name"] == "Alice A"


def test_membership_all_keeps_blanked_and_foreign_rows(snapshot):
    frame = reports.membership(snapshot, scope=False)
    assert "carol" in frame["unix_username"].to_list()
    # The blanked row survives with nothing but its identity.
    assert frame["unix_username"].null_count() == 1


def test_empty_snapshot_returns_empty_frame(tmp_path):
    snap = Snapshot.create(tmp_path, "empty")
    snap.write("users", to_frame([]))
    snap.write("openportal-allocations", to_frame([]))
    snap.write("openportal-associations", to_frame([]))
    snap.write_meta({})
    assert reports.membership(snap).is_empty()


# -- user usage --------------------------------------------------------------


def test_user_usage_sums_across_months_and_keeps_years_integral(snapshot):
    row = reports.user_usage(snapshot).row(0, named=True)
    assert row["unix_username"] == "alice"
    assert row["first_year"] == 2025
    assert row["last_year"] == 2026
    assert row["total_node_usage"] == pytest.approx(4.0)
    assert row["projects"] == "abc1"


def test_user_usage_scopes_out_other_organisations(snapshot):
    """Carol has the heaviest usage but is not ours; scope must drop her."""
    assert reports.user_usage(snapshot)["unix_username"].to_list() == ["alice", "bob"]
    assert "carol" in reports.user_usage(snapshot, scope=False)["unix_username"].to_list()


# -- utilisation -------------------------------------------------------------


def test_utilisation_puts_idle_allocations_first(snapshot):
    frame = reports.utilisation(snapshot)
    assert frame.row(0, named=True)["month_vs_limit_pct"] == pytest.approx(0.0)
    assert frame["month_vs_limit_pct"].to_list()[-1] == pytest.approx(50.0)


def test_utilisation_names_the_column_for_the_month_it_measures(snapshot):
    """node_usage is the current month only; the name must not imply otherwise."""
    columns = reports.utilisation(snapshot).columns
    assert "node_usage_this_month" in columns
    assert "node_usage" not in columns


def test_no_nan_leaks_into_percentages(snapshot):
    values = reports.utilisation(snapshot)["month_vs_limit_pct"].to_list()
    assert not any(v is not None and math.isnan(v) for v in values)


# -- queue -------------------------------------------------------------------


def test_queue_flattens_daily_reports(snapshot):
    frame = reports.queue(snapshot).sort("date")
    assert frame.height == 2
    busy = frame.row(0, named=True)
    assert busy["num_jobs"] == 10
    assert busy["mean_wait_seconds"] == pytest.approx(10.0)
    assert busy["distinct_users"] == 1
    # A day with no jobs must not divide by zero.
    assert frame.row(1, named=True)["mean_wait_seconds"] is None


def test_reports_registry_matches_module(snapshot):
    for name, fn in reports.REPORTS.items():
        assert callable(fn), name
    assert set(reports.REPORTS) >= reports.SCOPED


# -- monthly -----------------------------------------------------------------


def test_monthly_totals_entitlement_follows_month_length(snapshot):
    """Our share is nodes x fraction held for every hour, so February is smaller."""
    frame = reports.monthly_totals(snapshot, nodes=10, share=0.5, customer="UKRI")
    by_month = {row["month"]: row for row in frame.iter_rows(named=True)}
    january = by_month[date(2025, 1, 1)]
    february = by_month[date(2026, 2, 1)]
    assert january["entitlement_node_hours"] == pytest.approx(5 * 24 * 31)
    assert february["entitlement_node_hours"] == pytest.approx(5 * 24 * 28)
    assert january["node_hours"] == pytest.approx(1.5)
    assert january["pct_of_entitlement"] == pytest.approx(100 * 1.5 / 3720)
    assert january["mean_nodes"] == pytest.approx(1.5 / (24 * 31))


def test_monthly_totals_counts_only_people_who_ran(snapshot):
    frame = reports.monthly_totals(snapshot, customer="UKRI")
    row = frame.filter(pl.col("month") == date(2025, 1, 1)).row(0, named=True)
    assert row["active_users"] == 1
    assert row["active_projects"] == 1


def test_monthly_excludes_other_organisations(snapshot):
    """Carol's project is on the machine but not ours; UKRI's own rows survive."""
    ours = reports.monthly(snapshot, customer="UKRI")
    assert "zzz" not in ours["project_code"].to_list()
    assert "abc1" in ours["project_code"].to_list()


def test_monthly_customer_filter_can_exclude_everything(snapshot):
    assert reports.monthly(snapshot, customer="No Such Organisation").is_empty()


def test_monthly_marks_only_the_snapshot_month_as_partial(snapshot, monkeypatch):
    """A snapshot taken mid-month holds a partial month; nothing else is."""
    snapshot.write_meta({})  # written now, so "today" is the partial month
    frame = reports.monthly_totals(snapshot, customer="UKRI")
    assert frame["is_partial"].sum() <= 1
    today = reports.as_of(snapshot)
    assert today == date.today()


# -- reconcile ---------------------------------------------------------------
#
# The second opinion on the usage endpoint. `cache.check` knows one way a pull
# goes wrong -- a repeated row key -- and only for endpoints it holds a key for.
# This compares the summed usage against what the portal billed for the same
# month, which is arrived at by a different route and so catches anything that
# leaves the totals wrong, whatever did it.


def reconciling(tmp_path, name, allocations, usage_rows, invoice_rows):
    """A snapshot holding just the three endpoints `reconcile` reads."""
    snap = Snapshot.create(tmp_path, name)
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-allocation-user-usage", to_frame(usage_rows))
    snap.write("invoices", to_frame(invoice_rows))
    snap.write_meta({})
    return snap


def test_reconcile_agrees_when_the_pull_is_clean(snapshot):
    frame = reports.reconcile(snapshot, customer="UKRI")
    by_month = {row["month"]: row for row in frame.iter_rows(named=True)}
    january = by_month[date(2025, 1, 1)]
    assert january["node_hours"] == pytest.approx(1.5)
    assert january["incurred_costs"] == pytest.approx(1.5)
    assert january["difference"] == pytest.approx(0.0)
    assert january["status"] == "ok"
    assert by_month[date(2026, 2, 1)]["status"] == "ok"
    assert by_month[date(2026, 2, 1)]["invoice_state"] == "pending"


def test_reconcile_reads_incurred_costs_not_the_zeroed_total(snapshot):
    """`price` and `total` are net of a credit line; only `incurred_costs` bills."""
    assert reports.invoiced(snapshot, customer="UKRI")["incurred_costs"].sum() == pytest.approx(5.0)


def test_reconcile_leaves_out_another_organisations_invoice(snapshot):
    """Carol's 99.0 belongs to an invoice that is not ours, and must not net off."""
    frame = reports.reconcile(snapshot, customer="UKRI")
    february = frame.filter(pl.col("month") == date(2026, 2, 1)).row(0, named=True)
    assert february["incurred_costs"] == pytest.approx(3.5)


def test_reconcile_catches_a_month_counted_twice(tmp_path, allocations, invoices):
    """The bug this report exists for: usage summed high against the invoice.

    The rows here pass `cache.check` -- Project A holds two allocations, so a
    row on each is legitimate -- and still double the month. A guard keyed on
    the row identity cannot see that; the invoice can.
    """
    doubled = [
        {
            "allocation": f"{API_URL}/api/openportal-allocations/{alloc}/",
            "user": f"{API_URL}/api/users/alice/",
            "username": "alice.abc1.brics",
            "node_usage": "50.0",
            "year": 2025,
            "month": 1,
        }
        for alloc in ("aaa", "aaa2")
    ]
    snap = reconciling(tmp_path, "doubled", allocations, doubled, [invoice(2025, 1, 50.0)])

    row = reports.reconcile(snap, customer="UKRI").row(0, named=True)
    assert row["status"] == "usage high"
    assert row["difference"] == pytest.approx(50.0)
    assert row["pct_difference"] == pytest.approx(100.0)


def test_reconcile_catches_a_month_the_pull_dropped(tmp_path, allocations, invoices):
    """The other half of the same failure: rows that never came back at all."""
    thin = [
        {
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
            "user": f"{API_URL}/api/users/alice/",
            "username": "alice.abc1.brics",
            "node_usage": "10.0",
            "year": 2025,
            "month": 1,
        }
    ]
    snap = reconciling(tmp_path, "thin", allocations, thin, [invoice(2025, 1, 100.0)])

    row = reports.reconcile(snap, customer="UKRI").row(0, named=True)
    assert row["status"] == "usage low"
    assert row["pct_difference"] == pytest.approx(-90.0)


def test_reconcile_tolerates_the_rounding_between_the_two_sides(tmp_path, allocations):
    """Two decimal places per user-month against the invoice's ten: not a finding."""
    rows = [
        {
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
            "user": f"{API_URL}/api/users/alice/",
            "username": "alice.abc1.brics",
            "node_usage": "16694.73",
            "year": 2026,
            "month": 4,
        }
    ]
    snap = reconciling(tmp_path, "rounded", allocations, rows, [invoice(2026, 4, 16695.077778)])
    assert reports.reconcile(snap, customer="UKRI").row(0, named=True)["status"] == "ok"

    tight = reports.reconcile(snap, customer="UKRI", tolerance=0.0)
    assert tight.row(0, named=True)["status"] == "ok"  # still inside the floor


def test_reconcile_marks_a_month_with_no_usage_behind_the_invoice(tmp_path, allocations):
    snap = reconciling(tmp_path, "unused", allocations, [], [invoice(2026, 4, 500.0)])
    row = reports.reconcile(snap, customer="UKRI").row(0, named=True)
    assert row["status"] == "no usage"
    assert row["node_hours"] is None
    assert row["difference"] is None


def test_reconcile_says_nothing_about_a_month_with_no_invoice(tmp_path, allocations):
    rows = [
        {
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
            "user": f"{API_URL}/api/users/alice/",
            "username": "alice.abc1.brics",
            "node_usage": "500.0",
            "year": 2026,
            "month": 4,
        }
    ]
    snap = reconciling(tmp_path, "uninvoiced", allocations, rows, [])
    row = reports.reconcile(snap, customer="UKRI").row(0, named=True)
    assert row["status"] == "no invoice"
    assert row["incurred_costs"] is None


def test_reconcile_calls_an_empty_month_ok(tmp_path, allocations):
    """Nothing used and nothing billed is agreement, not a gap."""
    snap = reconciling(tmp_path, "quiet", allocations, [], [invoice(2025, 3, 0.0)])
    assert reports.reconcile(snap, customer="UKRI").row(0, named=True)["status"] == "ok"


def test_reconcile_all_widens_both_sides_and_stops_corresponding(snapshot):
    """`--all` drops the customer filter from the usage *and* the invoice side.

    Which is why it cannot be read as a check. Here it pulls in an invoice
    belonging to another organisation; against the portal it pulls in UKRI and
    other organisations' usage whose invoices go somewhere this token cannot see. Either way
    the two sides are no longer measuring the same estate.
    """
    frame = reports.reconcile(snapshot, scope=False)
    february = frame.filter(pl.col("month") == date(2026, 2, 1)).row(0, named=True)
    assert february["incurred_costs"] == pytest.approx(102.5)  # both invoices now
    assert february["node_hours"] == pytest.approx(3.5)


def test_reconcile_is_empty_without_either_side(tmp_path, allocations):
    snap = reconciling(tmp_path, "nothing", allocations, [], [])
    frame = reports.reconcile(snap, customer="UKRI")
    assert frame.is_empty()
    assert "status" in frame.columns


def test_as_of_reads_the_snapshot_date(tmp_path):
    from waldur_tools.cache import Snapshot

    snap = Snapshot.create(tmp_path, "dated")
    (snap.path / "meta.json").write_text('{"created": "2026-03-15T09:00:00+00:00"}')
    assert reports.as_of(snap) == date(2026, 3, 15)


# --------------------------------------------------------------------------
# allocations
# --------------------------------------------------------------------------


def test_allocations_spreads_the_award_over_its_months(snapshot):
    """Nothing in the portal states a monthly figure, so this constructs one."""
    frame = reports.allocations(snapshot, customer="UKRI")
    a = frame.filter(pl.col("project_name") == "Project A").row(0, named=True)
    # 2026-01-01 to 2026-08-01 is eight calendar months, inclusive of both.
    assert a["award_months"] == 8
    assert a["mean_monthly_allocation"] == pytest.approx(30000 / 8)


def test_allocations_measures_an_open_ended_project_to_the_snapshot_date(snapshot):
    """The internal projects have no end date; the span is "so far"."""
    frame = reports.allocations(snapshot, customer="UKRI")
    b = frame.filter(pl.col("project_name") == "Project B").row(0, named=True)
    assert b["end_date"] is None
    assert b["award_months"] >= 1
    assert b["mean_monthly_allocation"] == pytest.approx(1000 / b["award_months"])


def test_allocations_leaves_a_creditless_project_undefined(tmp_path, allocations, user_usage_rows):
    """Null, not zero: a project with no award has no rate to be measured against.

    Zero would make every one of its months read as infinitely over budget, and
    the workshop and internal projects genuinely hold nothing.
    """
    from waldur_tools.cache import Snapshot
    from waldur_tools.frames import to_frame

    snap = Snapshot.create(tmp_path, "creditless")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-allocation-user-usage", to_frame(user_usage_rows))
    snap.write(
        "openportal-accounting-summary",
        to_frame([{"project_uuid": "pa", "total_credits": "0.00", "start_date": "2026-01-01"}]),
    )
    snap.write_meta({})
    frame = reports.allocations(snap, customer="UKRI")
    assert frame.filter(pl.col("project_name") == "Project A")["mean_monthly_allocation"][0] is None


def test_allocations_is_empty_for_an_unknown_customer(snapshot):
    frame = reports.allocations(snapshot, customer="No Such Organisation")
    assert frame.is_empty()
    assert "mean_monthly_allocation" in frame.columns


# -- storage quotas ---------------------------------------------------------


@pytest.fixture
def storage_snapshot(tmp_path, allocations, storage_reports):
    snap = Snapshot.create(tmp_path, "storage")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-project-storage-reports", to_frame(storage_reports))
    snap.write_meta({})
    return snap


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("100.00 GB", 100 * 1024**3),
        ("5.00 TB", 5 * 1024**4),
        ("4.00 KB", 4096),
        ("512 B", 512),
        ("1.5 PB", 1.5 * 1024**5),
        # The collector is not the only thing that can write this field.
        ("unlimited", None),
        ("", None),
        (None, None),
        (17, None),
    ],
)
def test_sizes_are_read_as_binary_multiples(text, expected):
    """The portal's "GB" is a GiB -- it calls the 100G home quota 100.00 GB."""
    assert reports._size_bytes(text) == expected


def test_a_size_survives_the_round_trip():
    assert reports.humanise_bytes(reports._size_bytes("46.79 GB")) == "46.79 GB"


def test_a_petabyte_is_written_as_a_petabyte():
    """The unit the parser accepts is the unit the renderer has to reach.

    A project quota measured in petabytes is not hypothetical on a machine this
    size, and rendering it as four figures of terabytes would be exactly the
    fourteen digits of noise this function exists to avoid.
    """
    assert reports.humanise_bytes(1024**5) == "1.00 PB"
    assert reports.humanise_bytes(2.5 * 1024**5) == "2.50 PB"


def test_the_last_day_of_a_month_comes_from_the_top_level_snapshot(storage_snapshot):
    """The daily dictionary stops a day short; the snapshot beside it does not.

    January's `daily_reports` carry the 29th and the 30th, and the reading for
    the 31st exists only as the top-level snapshot. Reading one and not the
    other silently loses the last day of every finished month.
    """
    samples = reports.storage_samples(storage_snapshot)
    january = samples.filter(
        (pl.col("month") == date(2025, 1, 1))
        & (pl.col("kind") == "project")
        & (pl.col("project_code") == "abc1")
    )
    assert sorted({str(day) for day in january["date"].to_list()}) == [
        "2025-01-29",
        "2025-01-30",
        "2025-01-31",
    ]


def test_the_month_in_progress_is_a_single_reading(storage_snapshot):
    """February has no `daily_reports` at all, only the snapshot.

    So every statistic collapses onto one number, and a figure that assumed a
    series would be drawing one point as though it were thirty.
    """
    monthly = reports.storage_monthly(storage_snapshot)
    february = monthly.filter((pl.col("month") == date(2026, 2, 1)) & (pl.col("kind") == "project"))
    assert february.height == 1
    row = february.row(0, named=True)
    assert row["days_observed"] == 1
    assert row["peak_fill_pct"] == row["end_fill_pct"] == row["median_fill_pct"]


def test_both_collectors_are_kept_as_separate_samples(storage_snapshot):
    """Two resources report the same filesystem minutes apart, and disagree.

    Deduplicating them would throw away half the evidence for the month;
    treating them as separate readings of one quantity is what makes the peak
    the real peak rather than one collector's view of it.
    """
    monthly = reports.storage_monthly(storage_snapshot)
    row = monthly.filter(
        (pl.col("month") == date(2025, 1, 1))
        & (pl.col("kind") == "project")
        & (pl.col("project_code") == "abc1")
    ).row(0, named=True)
    # Three days, two collectors each.
    assert row["days_observed"] == 3
    assert row["samples"] == 6
    # Peak is the highest of the six, not the highest daily mean.
    assert row["peak_fill_pct"] == pytest.approx(100 * 3.10 / 20)
    # The median sits on the middle pair, so it is nothing like the peak --
    # which is the whole reason both are offered.
    assert row["median_fill_pct"] == pytest.approx(100 * 2.05 / 20)


def test_a_month_short_of_readings_is_marked_partial(storage_snapshot):
    """Partial here means "fewer daily readings than days", not "this month".

    Different from `monthly_totals`, where it marks the month the snapshot was
    taken in. Storage lags its own collector, so the snapshot date says nothing
    about whether a storage month is complete.
    """
    monthly = reports.storage_monthly(storage_snapshot)
    assert monthly["is_partial"].all()
    assert monthly["days_observed"].max() < 31


def test_a_reading_outside_its_month_is_dropped(storage_snapshot):
    """A February reading filed in a January row belongs to neither column."""
    samples = reports.storage_samples(storage_snapshot, scope=False)
    abc2 = samples.filter(pl.col("project_code") == "abc2")
    assert abc2.height == 1
    assert str(abc2["date"][0]) == "2025-01-31"


def test_the_end_of_a_month_is_the_last_sample_and_not_the_last_readable_one(
    storage_snapshot,
):
    """`end` answers "what was it on the last reading", nulls included.

    Bob's home was read three days running and only the last of them came back
    unparseable. Reporting the 30th as the end would put a stale level beside a
    limit taken from the 31st -- a row internally inconsistent with itself, and
    silently so, since nothing on the figure says which day each half came from.
    """
    monthly = reports.storage_monthly(storage_snapshot)
    row = monthly.filter(
        (pl.col("month") == date(2025, 1, 1))
        & (pl.col("username") == "bob")
        & (pl.col("filesystem") == "home")
    ).row(0, named=True)
    assert row["end_fill_pct"] is None
    assert row["end_bytes"] is None
    assert row["limit_bytes"] is None
    # The readable days still carry the peak and the median.
    assert row["peak_bytes"] == pytest.approx(95 * 1024**3)


def test_a_limit_that_is_not_a_size_blanks_the_percentage(storage_snapshot):
    """Null, not zero and not a division by it: the quota is simply unknown."""
    current = reports.storage(storage_snapshot)
    abc2 = current.filter(pl.col("project_code") == "abc2").row(0, named=True)
    assert abc2["limit_bytes"] is None
    assert abc2["fill_pct"] is None
    assert abc2["usage_bytes"] == pytest.approx(1024**4)


def test_storage_is_scoped_like_every_other_report(storage_snapshot):
    """The endpoint reports the whole machine; the page is about our projects."""
    ours = reports.storage(storage_snapshot)
    everyone = reports.storage(storage_snapshot, scope=False)
    assert "zzz9" not in ours["project_code"].to_list()
    assert "zzz9" in everyone["project_code"].to_list()


def test_the_current_view_is_fullest_first_and_most_recent(storage_snapshot):
    """The table is read top down, and each row is the newest reading of it."""
    current = reports.storage(storage_snapshot)
    fills = [value for value in current["fill_pct"].to_list() if value is not None]
    assert fills == sorted(fills, reverse=True)
    # February's reading wins over January's for the same quota.
    alice = current.filter((pl.col("username") == "alice") & (pl.col("filesystem") == "home")).row(
        0, named=True
    )
    assert str(alice["date"]) == "2026-02-18"


def test_storage_can_be_narrowed_to_one_organisation(tmp_path, allocations, storage_reports):
    """Scope keeps every project the *token* administers, across customers.

    On a multi-tenant portal that is more than one organisation, and a report
    headed by one customer's name must not draw another's disks beside them.
    """
    elsewhere = {
        **allocations[0],
        "url": f"{API_URL}/api/openportal-allocations/zzz/",
        "uuid": "zzz",
        "project_name": "Elsewhere",
        "project_uuid": "pz",
        "customer_name": "Other Uni",
        "groupname": "brics.zzz9",
        "backend_id": "zzz9.brics",
    }
    snap = Snapshot.create(tmp_path, "two-customers")
    snap.write("openportal-allocations", to_frame([*allocations, elsewhere]))
    snap.write("openportal-project-storage-reports", to_frame(storage_reports))
    snap.write_meta({})

    administered = set(reports.storage_samples(snap)["project_code"].to_list())
    assert administered == {"abc1", "abc2", "zzz9"}
    ours = set(reports.storage_samples(snap, customer="UKRI")["project_code"].to_list())
    assert ours == {"abc1", "abc2"}
    # `scope=False` is the whole machine by definition, so naming a customer
    # inside it would be two filters contradicting each other.
    everyone = reports.storage_samples(snap, customer="UKRI", scope=False)
    assert set(everyone["project_code"].to_list()) == {"abc1", "abc2", "zzz9"}


def test_storage_survives_a_snapshot_that_never_pulled_it(tmp_path, allocations):
    """Older snapshots predate the endpoint, and must not fail the whole report."""
    snap = Snapshot.create(tmp_path, "old")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write_meta({})
    assert reports.storage(snap).is_empty()
    assert reports.storage_monthly(snap).is_empty()
