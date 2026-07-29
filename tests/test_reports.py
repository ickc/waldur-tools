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
