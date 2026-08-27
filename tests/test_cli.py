"""What the CLI says when a run fails, and whether it is worth running again.

The interesting half of an error message is the sentence after it. A portal
being written to while it is read defeats the paging guards through nobody's
mistake, and the only cure is another run -- so those failures hand the command
back. Everything else does not, because "try again" after a rejected token or a
dropped filter costs a run and fixes nothing.
"""

from __future__ import annotations

import stat
import sys

import polars as pl
import pytest
from rich.console import Console

from waldur_tools import cli
from waldur_tools.cache import SnapshotError
from waldur_tools.client import WaldurError
from waldur_tools.config import PRIVATE_DIR, PRIVATE_FILE, MissingTokenError


@pytest.fixture
def cli_run(monkeypatch, capsys):
    """Run ``cli.main`` against a command that fails, and return what it printed."""
    # Wide, so an assertion never turns on where rich decided to wrap.
    monkeypatch.setattr(cli, "error_console", Console(stderr=True, width=200))

    def run(error, argv=("/opt/venv/bin/waldur-tools", "snapshot", "--name", "today")):
        def boom():
            raise error

        monkeypatch.setattr(cli, "app", boom)
        monkeypatch.setattr("sys.argv", list(argv))
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        assert exit_info.value.code == 2
        return capsys.readouterr().err

    return run


def test_a_transient_failure_hands_the_whole_command_back(cli_run):
    """Down to the arguments: reconstructing it from scrollback is the friction."""
    err = cli_run(WaldurError("the month moved under the pull", transient=True))
    assert "the month moved under the pull" in err
    assert "Run it again" in err
    # The name it was invoked as, not the absolute path it was installed at.
    assert "waldur-tools snapshot --name today" in err
    assert "/opt/venv" not in err


def test_a_repeated_row_in_a_snapshot_counts_as_transient(cli_run):
    """The same fault seen one layer up, so it earns the same advice."""
    err = cli_run(SnapshotError("2 of 40 rows repeat a key already seen", transient=True))
    assert "Run it again" in err


def test_a_fixable_failure_is_not_dressed_up_as_bad_luck(cli_run):
    """A missing token is the user's to fix, and another run will not fix it.

    It carries its own instructions, which another run would only print again.
    """
    err = cli_run(MissingTokenError())
    assert "Run it again" not in err
    assert err.strip()


def test_a_guard_that_caught_a_real_fault_does_not_suggest_another_run(cli_run):
    """An endpoint that ignores the filter fails identically every time."""
    err = cli_run(WaldurError("openportal-x ignores the year/month filter"))
    assert "Run it again" not in err


def test_an_argument_with_a_space_comes_back_quoted(cli_run):
    """The command is printed to be pasted, so it has to survive being pasted."""
    err = cli_run(
        WaldurError("rows changed under the pull", transient=True),
        argv=("waldur-tools", "report", "--customer", "Some Organisation"),
    )
    assert "waldur-tools report --customer 'Some Organisation'" in err


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
@pytest.mark.parametrize("name", ["report.csv", "report.json", "report.parquet"])
def test_an_exported_report_is_readable_only_by_its_author(tmp_path, name):
    """`-o` carries the same figures the snapshot does, and often points somewhere shared."""
    target = tmp_path / name
    cli._write(pl.DataFrame({"project_code": ["abc1"], "node_hours": [10.0]}), target)
    assert stat.S_IMODE(target.stat().st_mode) == PRIVATE_FILE


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
def test_an_export_over_a_world_readable_file_narrows_it(tmp_path):
    """Writing into a path that already existed must not inherit its mode."""
    target = tmp_path / "report.csv"
    target.write_text("stale")
    target.chmod(0o644)
    cli._write(pl.DataFrame({"project_code": ["abc1"]}), target)
    assert stat.S_IMODE(target.stat().st_mode) == PRIVATE_FILE


@pytest.fixture
def slurm_run(monkeypatch):
    """Run `slurm-jobs` with the portal and `sacct` both stubbed out."""
    monkeypatch.setattr(cli.Snapshot, "latest", classmethod(lambda cls, root: None))
    monkeypatch.setattr(
        cli.report_module, "in_scope", lambda source: pl.DataFrame({"project_code": ["abc1"]})
    )
    monkeypatch.setattr(cli.slurm_module, "capture", lambda codes, start, cluster: pl.DataFrame({}))

    def run(root, output=None):
        cli.slurm_jobs(output=output, since="2024-01-01", cluster="i3", use=None, root=root)

    return run


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
def test_the_cache_root_this_writes_into_is_made_private(tmp_path, slurm_run):
    """It is ours, it holds a snapshot, and the directory is the boundary."""
    root = tmp_path / "cache"
    root.mkdir()
    root.chmod(0o755)
    slurm_run(root)
    assert stat.S_IMODE(root.stat().st_mode) == PRIVATE_DIR


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
def test_a_directory_the_caller_chose_is_left_exactly_as_they_set_it(tmp_path, slurm_run):
    """`-o` can name a group share, and none of what is already in it is ours."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    neighbour = shared / "someone-else.txt"
    neighbour.write_text("not ours")
    neighbour.chmod(0o644)

    slurm_run(tmp_path / "cache", output=shared / "jobs.parquet")

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE(neighbour.stat().st_mode) == 0o644
    # The file this command did write is still its own business.
    assert stat.S_IMODE((shared / "jobs.parquet").stat().st_mode) == PRIVATE_FILE
