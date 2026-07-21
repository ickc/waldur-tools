"""Command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from .cache import DEFAULT_ENDPOINTS, Snapshot, pull
from .client import WaldurClient
from .config import MissingTokenError, Settings
from .reports import REPORTS

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

    shown = frame.head(limit)
    table = Table(title=title, header_style="bold")
    for column in shown.columns:
        numeric_column = shown.schema[column].is_numeric()
        table.add_column(column, justify="right" if numeric_column else "left")
    for row in shown.iter_rows():
        table.add_row(*("" if value is None else _fmt(value) for value in row))
    console.print(table)
    if frame.height > limit:
        console.print(f"[dim]... {frame.height - limit} more rows (use --limit)[/]")


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
    """Pull endpoints in full and store them as parquet."""
    with WaldurClient() as client:
        target, counts = pull(client, which or list(DEFAULT_ENDPOINTS), root=root, name=name)
    table = Table(title=f"Snapshot {target.path}", header_style="bold")
    table.add_column("endpoint")
    table.add_column("rows", justify="right")
    for endpoint, total in counts.items():
        table.add_row(endpoint, f"{total:,}")
    console.print(table)


@app.command()
def report(
    which: Annotated[str, typer.Argument(help=f"One of: {', '.join(REPORTS)}")],
    live: Annotated[bool, typer.Option(help="Query the API instead of a snapshot")] = False,
    limit: Annotated[int, typer.Option(help="Rows to display")] = 25,
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Write to .csv/.json/.parquet")
    ] = None,
    root: Annotated[Path | None, typer.Option(help="Override the cache directory")] = None,
) -> None:
    """Run an analysis over snapshotted (or live) data."""
    if which not in REPORTS:
        error_console.print(f"[red]Unknown report {which!r}. Choose from: {', '.join(REPORTS)}[/]")
        raise typer.Exit(2)

    if live:
        with WaldurClient() as client:
            frame = REPORTS[which](client)
    else:
        settings = Settings.from_env()
        frame = REPORTS[which](Snapshot.latest(root or settings.cache_dir))

    _render(frame, which, limit)
    if output is not None:
        _write(frame, output)


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
    """Entry point that turns expected failures into clean messages."""
    try:
        app()
    except MissingTokenError as error:
        error_console.print(f"[red]{error}[/]")
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
