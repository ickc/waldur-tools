from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from waldur_tools import slurm


def test_accounts_prefixes_project_codes():
    assert slurm.accounts(["abc1", "abc2"]) == ["brics.abc1", "brics.abc2"]


def test_accounts_passes_through_legacy_names():
    """The internal projects are their own account, with no ``brics.`` on it."""
    assert slurm.accounts(["legacy-internal-1"]) == ["legacy-internal-1"]


def test_parse_splits_the_username_into_person_and_project(job_records):
    row = job_records.filter(pl.col("job_id") == 101).row(0, named=True)
    assert row["unix_username"] == "alice"
    assert row["project_code"] == "abc1"


def test_parse_reduces_cancelled_by_uid_to_one_state(job_records):
    """``CANCELLED by 1234`` names who cancelled it, not what happened to it."""
    assert set(job_records["state"].to_list()) == {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
    }


def test_a_job_that_never_started_has_no_wait(job_records):
    """Null, not zero: it did not wait no time at all, it has no wait to report."""
    row = job_records.filter(pl.col("job_id") == 104).row(0, named=True)
    assert row["start"] is None
    assert row["wait_seconds"] is None


def test_wait_is_start_minus_submit(job_records):
    row = job_records.filter(pl.col("job_id") == 103).row(0, named=True)
    assert row["wait_seconds"] == 2 * 24 * 3600


def test_requested_node_hours_price_the_reservation_not_the_run(job_records):
    """A job that books 24 h and exits in 10 still needed a 24 h hole."""
    row = job_records.filter(pl.col("job_id") == 102).row(0, named=True)
    assert row["requested_node_hours"] == pytest.approx(4 * 1440 / 60)
    assert row["node_hours"] == pytest.approx(4 * 36000 / 3600)


def test_an_inherited_time_limit_is_not_guessed_at(job_records):
    """``Partition_Limit`` is the absence of a request, not a request."""
    row = job_records.filter(pl.col("job_id") == 105).row(0, named=True)
    assert row["requested_minutes"] is None
    assert row["requested_node_hours"] is None


def test_month_comes_from_submission(job_records):
    assert set(job_records["month"].to_list()) == {date(2026, 3, 1), date(2026, 4, 1)}


def test_parse_tolerates_empty_and_ragged_input():
    """A short line is a truncated read, and is dropped rather than misaligned."""
    assert slurm.parse("").is_empty()
    assert slurm.parse("\n  \n").is_empty()
    assert slurm.parse("101|alice.abc1|brics.abc1").is_empty()


def test_capture_without_projects_does_not_shell_out():
    assert slurm.capture([]).is_empty()


def test_load_jobs_is_empty_when_there_is_no_capture(tmp_path):
    """Every consumer of this file is optional, so absence cannot raise."""
    assert slurm.load_jobs(None, root=tmp_path).is_empty()
    assert slurm.load_jobs(tmp_path / "nope.parquet").is_empty()


def test_load_jobs_round_trips(tmp_path, job_records):
    job_records.write_parquet(tmp_path / slurm.JOBS_FILENAME)
    assert slurm.load_jobs(None, root=tmp_path).height == job_records.height


def test_capture_explains_a_missing_sacct(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(slurm.subprocess, "run", missing)
    with pytest.raises(slurm.SlurmError, match="not on PATH"):
        slurm.capture(["abc1"])


def test_capture_surfaces_an_sacct_error(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "sacct: error: no such account"

    monkeypatch.setattr(slurm.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(slurm.SlurmError, match="no such account"):
        slurm.capture(["abc1"])


def test_capture_pins_the_cluster_rather_than_inheriting_it(monkeypatch):
    """`i3macs` is unmetered, so a capture taken there would describe a different
    machine from every other figure in the report."""
    seen = {}

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    def record(command, **kwargs):
        seen["command"] = command
        return Ok()

    monkeypatch.setattr(slurm.subprocess, "run", record)
    slurm.capture(["abc1"])
    assert f"--clusters={slurm.DEFAULT_CLUSTER}" in seen["command"]
    assert slurm.DEFAULT_CLUSTER == "i3"

    slurm.capture(["abc1"], cluster="all")
    assert "--clusters=all" in seen["command"]
