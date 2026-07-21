from __future__ import annotations

import math

import pytest

from waldur_tools import reports
from waldur_tools.cache import Snapshot
from waldur_tools.frames import to_frame


@pytest.fixture
def snapshot(
    tmp_path,
    allocations,
    associations,
    accounting_summary,
    usage_reports,
    user_usage_rows,
    users,
):
    snap = Snapshot.create(tmp_path, "test")
    snap.write("users", to_frame(users))
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
    assert reports.user_usage(snapshot)["unix_username"].to_list() == ["alice"]
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
