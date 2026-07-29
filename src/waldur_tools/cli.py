"""Command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from . import viz as viz_report
from .cache import DEFAULT_ENDPOINTS, Snapshot, SnapshotError, available, pull
from .client import WaldurClient, WaldurError
from .config import MissingTokenError, Settings
from .reports import DEFAULT_CUSTOMER, DEFAULT_SHARE, REPORTS, SCOPED, TOTAL_NODES

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Snapshot, analyse and report on Waldur / Isambard portal data.",
)
console = Console()
error_console = Console(stderr=True)


def _render(frame: pl.DataFrame, title: str, limit: int) -> None:
    if frame.is_empty():
        console.print(f"[yellow]{title}: no rows[/]")
        return

    # --limit 0 means "all of it".
    shown = frame if limit <= 0 else frame.head(limit)
    table = Table(title=title, header_style="bold")
    for column in shown.columns:
        numeric_column = shown.schema[column].is_numeric()
        table.add_column(column, justify="right" if numeric_column else "left")
    for row in shown.iter_rows():
        table.add_row(*("" if value is None else _fmt(value) for value in row))
    console.print(table)
    if shown.height < frame.height:
        console.print(
            f"[dim]... {frame.height - shown.height} more rows (--limit N, or --limit 0 for all)[/]"
        )


def _sorted(frame: pl.DataFrame, columns: list[str], descending: bool) -> pl.DataFrame:
    """Re-sort a report, replacing whatever order it chose for itself."""
    unknown = [column for column in columns if column not in frame.columns]
    if unknown:
        error_console.print(
            f"[red]No such column(s): {', '.join(unknown)}. "
            f"Available: {', '.join(frame.columns)}[/]"
        )
        raise typer.Exit(2)
    return frame.sort(columns, descending=descending, nulls_last=True)


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _write(frame: pl.DataFrame, output: Path) -> None:
    if output.suffix == ".parquet":
        frame.write_parquet(output)
    elif output.suffix == ".json":
        frame.write_json(output)
    else:
        frame.write_csv(output)
    console.print(f"[green]Wrote {frame.height} rows to {output}[/]")


@app.command()
def endpoints(
    filter_: Annotated[str, typer.Option("--filter", help="Substring to match")] = "",
    counts: Annotated[bool, typer.Option(help="Also fetch row counts (slower)")] = False,
) -> None:
    """List the endpoints this deployment exposes."""
    with WaldurClient() as client:
        names = sorted(name for name in client.endpoints() if filter_ in name)
        if not counts:
            console.print("\n".join(names))
            return
        table = Table(header_style="bold")
        table.add_column("endpoint")
        table.add_column("rows", justify="right")
        for name in names:
            try:
                total = client.count(name)
            except Exception as error:
                table.add_row(name, f"[red]{type(error).__name__}[/]")
            else:
                table.add_row(name, "-" if total is None else f"{total:,}")
        console.print(table)


@app.command()
def snapshot(
    which: Annotated[
        list[str] | None, typer.Argument(help="Endpoints to pull (default: the report set)")
    ] = None,
    name: Annotated[str | None, typer.Option(help="Snapshot directory name")] = None,
    root: Annotated[Path | None, typer.Option(help="Override the cache directory")] = None,
) -> None:
    """Refresh the cache: pull endpoints in full and store them as parquet.

    This is the only way the cache is ever written. Each run creates a new
    timestamped directory holding a complete re-fetch; nothing is merged into
    an existing snapshot. `report` then reads the newest one.
    """
    with WaldurClient() as client:
        target, counts = pull(client, which or list(DEFAULT_ENDPOINTS), root=root, name=name)
    table = Table(title=f"Snapshot {target.path}", header_style="bold")
    table.add_column("endpoint")
    table.add_column("rows", justify="right")
    for endpoint, total in counts.items():
        table.add_row(endpoint, f"{total:,}")
    console.print(table)


@app.command()
def snapshots(
    root: Annotated[Path | None, typer.Option(help="Override the cache directory")] = None,
) -> None:
    """List the snapshots on disk, newest last."""
    directory = root or Settings.from_env().cache_dir
    found = available(directory)
    if not found:
        console.print(f"[yellow]No snapshots under {directory}. Run 'waldur-tools snapshot'.[/]")
        return
    table = Table(title=f"Snapshots in {directory}", header_style="bold")
    table.add_column("name")
    table.add_column("created")
    table.add_column("rows", justify="right")
    for snap in found:
        meta = snap.meta
        endpoints_meta = meta.get("endpoints")
        total = sum(endpoints_meta.values()) if isinstance(endpoints_meta, dict) else 0
        table.add_row(snap.path.name, str(meta.get("created", "")), f"{total:,}")
    console.print(table)


@app.command()
def report(
    which: Annotated[str, typer.Argument(help=f"One of: {', '.join(REPORTS)}")],
    live: Annotated[
        bool, typer.Option(help="Query the API directly; does not touch the cache")
    ] = False,
    use: Annotated[
        str | None, typer.Option(help="Snapshot name to read (default: the newest)")
    ] = None,
    all_: Annotated[
        bool,
        typer.Option(
            "--all",
            help=(
                f"Include projects outside your administrative scope ({', '.join(sorted(SCOPED))})"
            ),
        ),
    ] = False,
    sort: Annotated[list[str] | None, typer.Option(help="Sort by these columns, in order")] = None,
    desc: Annotated[bool, typer.Option("--desc", help="Sort descending")] = False,
    limit: Annotated[int, typer.Option(help="Rows to display; 0 for all")] = 25,
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Write to .csv/.json/.parquet")
    ] = None,
    root: Annotated[Path | None, typer.Option(help="Override the cache directory")] = None,
) -> None:
    """Run an analysis over cached (or live) data.

    Reads the newest snapshot by default. Use `snapshot` to refresh it, `--use`
    to pin an older one, or `--live` to bypass the cache entirely.
    """
    if which not in REPORTS:
        error_console.print(f"[red]Unknown report {which!r}. Choose from: {', '.join(REPORTS)}[/]")
        raise typer.Exit(2)
    if all_ and which not in SCOPED:
        error_console.print(f"[red]--all applies only to: {', '.join(sorted(SCOPED))}[/]")
        raise typer.Exit(2)

    kwargs = {"scope": False} if all_ else {}
    if live:
        with WaldurClient() as client:
            frame = REPORTS[which](client, **kwargs)
    else:
        settings = Settings.from_env()
        directory = root or settings.cache_dir
        source = Snapshot.named(directory, use) if use else Snapshot.latest(directory)
        frame = REPORTS[which](source, **kwargs)

    if sort:
        frame = _sorted(frame, sort, desc)
    _render(frame, which, limit)
    if output is not None:
        _write(frame, output)


@app.command()
def viz(
    output: Annotated[
        Path, typer.Option("-o", "--output", help="Where to write the HTML report")
    ] = Path("isambard-utilisation.html"),
    nodes: Annotated[int, typer.Option(help="Compute nodes in the machine")] = TOTAL_NODES,
    share: Annotated[float, typer.Option(help="Our fraction of it, e.g. 0.10")] = DEFAULT_SHARE,
    customer: Annotated[
        str, typer.Option(help="Organisation to report on; '' for every visible project")
    ] = DEFAULT_CUSTOMER,
    live: Annotated[bool, typer.Option(help="Query the API directly instead of the cache")] = False,
    use: Annotated[
        str | None, typer.Option(help="Snapshot name to read (default: the newest)")
    ] = None,
    root: Annotated[Path | None, typer.Option(help="Override the cache directory")] = None,
) -> None:
    """Write a self-contained HTML report on how much of our share we are using.

    One file, plotly.js inlined: it opens offline, needs no server, and can be
    sent to someone who will never run this tool. The numbers are the same ones
    `report monthly` and `report monthly-totals` print.
    """
    if live:
        with WaldurClient() as client:
            page = viz_report.render(client, nodes=nodes, share=share, customer=customer or None)
    else:
        directory = root or Settings.from_env().cache_dir
        source = Snapshot.named(directory, use) if use else Snapshot.latest(directory)
        page = viz_report.render(source, nodes=nodes, share=share, customer=customer or None)

    output.write_text(page, encoding="utf-8")
    console.print(
        f"[green]Wrote {output}[/] ({len(page) / 1_048_576:.1f} MB) — open it in a browser"
    )


@app.command()
def whoami() -> None:
    """Show the resolved configuration and confirm the token works."""
    settings = Settings.from_env()
    console.print(repr(settings))
    with WaldurClient(settings) as client:
        me = client.http.get(f"{settings.api_url}/api/users/me/").json()
    console.print(
        f"[green]Authenticated as[/] {me.get('username')} ({me.get('full_name') or 'no name'})"
    )


def main() -> None:
    """Entry point that turns expected failures into clean messages.

    A missing token, an absent snapshot and a rejected request are all things
    the user can fix; a traceback for any of them is noise.
    """
    try:
        app()
    except (MissingTokenError, SnapshotError, WaldurError) as error:
        error_console.print(f"[red]{error}[/]")
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
