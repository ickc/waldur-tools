from __future__ import annotations

import math

import pytest

from waldur_tools import reports
from waldur_tools.cache import Snapshot
from waldur_tools.frames import to_frame


@pytest.fixture
def snapshot(tmp_path, allocations, associations, accounting_summary, usage_reports):
    snap = Snapshot.create(tmp_path, "test")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-associations", to_frame(associations))
    snap.write("openportal-accounting-summary", to_frame(accounting_summary))
    snap.write("openportal-project-usage-reports", to_frame(usage_reports))
    snap.write_meta({})
    return snap


def test_credits_computes_runway(snapshot):
    frame = reports.credits(snapshot).sort("project_name")
    row = frame.row(0, named=True)
    assert row["remaining"] == pytest.approx(20000.0)
    assert row["used_pct"] == pytest.approx(100 / 3)
    assert row["months_remaining"] == pytest.approx(10.0)


def test_credits_runway_is_null_without_spend(snapshot):
    frame = reports.credits(snapshot).sort("project_name")
    assert frame.row(1, named=True)["months_remaining"] is None


def test_membership_joins_allocation_metadata(snapshot):
    frame = reports.membership(snapshot)
    assert frame.height == 2
    alice = frame.filter(frame["username"] == "alice").row(0, named=True)
    assert alice["service_name"] == "Isambard 3"
    assert alice["project_name"] == "Project A"


def test_membership_sorts_missing_usernames_last(tmp_path, allocations, associations):
    """Real data has null usernames; they must not lead the report."""
    snap = Snapshot.create(tmp_path, "nulls")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write(
        "openportal-associations",
        to_frame([{**associations[0], "username": None}, *associations]),
    )
    snap.write_meta({})
    assert reports.membership(snap)["username"].to_list()[-1] is None


def test_membership_keeps_unresolvable_allocations(tmp_path, allocations, associations):
    """Most associations point at allocations the token cannot see; keep them."""
    snap = Snapshot.create(tmp_path, "unresolved")
    snap.write("openportal-allocations", to_frame(allocations[:1]))
    snap.write("openportal-associations", to_frame(associations))
    snap.write_meta({})

    frame = reports.membership(snap)
    assert frame.height == 2
    assert frame["resolved"].to_list() == [True, False]
    # The unresolved row keeps its identity even without allocation metadata.
    assert frame.row(1, named=True)["username"] == "bob"
    assert frame.row(1, named=True)["service_name"] is None


def test_user_usage_keeps_years_as_integers(tmp_path):
    snap = Snapshot.create(tmp_path, "usage")
    snap.write(
        "openportal-allocation-user-usage",
        to_frame(
            [
                {"username": "a", "full_name": "A", "node_usage": "1.5", "year": 2025, "month": 1},
                {"username": "a", "full_name": "A", "node_usage": "2.5", "year": 2026, "month": 2},
            ]
        ),
    )
    snap.write_meta({})
    row = reports.user_usage(snap).row(0, named=True)
    assert row["first_year"] == 2025
    assert row["last_year"] == 2026
    assert row["total_node_usage"] == pytest.approx(4.0)


def test_utilisation_puts_idle_allocations_first(snapshot):
    frame = reports.utilisation(snapshot)
    assert frame.row(0, named=True)["project_name"] == "Project B"
    assert frame.row(0, named=True)["used_pct"] == pytest.approx(0.0)
    assert frame.row(1, named=True)["used_pct"] == pytest.approx(50.0)


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


def test_empty_snapshot_returns_empty_frame(tmp_path):
    snap = Snapshot.create(tmp_path, "empty")
    snap.write("openportal-allocations", to_frame([]))
    snap.write("openportal-associations", to_frame([]))
    snap.write_meta({})
    assert reports.membership(snap).is_empty()


def test_no_nan_leaks_into_used_pct(snapshot):
    values = reports.utilisation(snapshot)["used_pct"].to_list()
    assert not any(v is not None and math.isnan(v) for v in values)
