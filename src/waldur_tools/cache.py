"""Local parquet snapshots of API endpoints.

Some endpoints are large (``openportal-allocation-user-usage`` is tens of thousands of rows)
and change slowly. Pulling them once into parquet keeps analysis fast, keeps
load off the portal, and lets you diff the estate over time by keeping more
than one snapshot around.

**The cache is append-only, and a snapshot is immutable.** :func:`pull` always
writes a whole new timestamped directory; nothing ever edits one in place.
Refreshing therefore means taking another full snapshot -- roughly nine minutes
and tens of thousands of rows against the Isambard portal -- and old directories stay put until
you delete them.

That is a deliberate limit rather than a missing feature. An incremental update
needs the server to answer "what changed since?", and this deployment cannot:
the list endpoints accept no delta parameter, and Waldur's DRF filters ignore
query parameters they do not recognise, so a made-up ``modified_after`` returns
the full result set with an unchanged ``X-Result-Count`` and no error. Merging
by hand would mean re-fetching everything to find the differences -- the same
cost as a fresh snapshot, with the added risk of a half-stale table -- and
deletions would never be noticed at all. So there is no ``--update``: use
``snapshot`` for a new one, or ``report --live`` to skip the cache.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from .client import WaldurClient
from .frames import to_frame

#: Endpoints pulled by ``waldur-tools snapshot`` when none are named.
#: These are the ones the reports in :mod:`waldur_tools.reports` build on.
DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "customers",
    "projects",
    "users",
    "openportal-allocations",
    "openportal-associations",
    "openportal-accounting-summary",
    "openportal-allocation-user-usage",
    "openportal-project-usage-reports",
    "openportal-project-storage-reports",
    "marketplace-resources",
    "marketplace-component-usages",
    "invoices",
)

META_FILENAME = "meta.json"


class SnapshotError(RuntimeError):
    """A snapshot could not be read or written."""


def _filename(endpoint: str) -> str:
    return f"{endpoint.strip('/').replace('/', '_')}.parquet"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A directory of parquet files, one per endpoint."""

    path: Path

    @classmethod
    def create(cls, root: Path, name: str | None = None) -> Snapshot:
        """Open (creating if needed) a snapshot directory under ``root``."""
        name = name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @classmethod
    def latest(cls, root: Path) -> Snapshot:
        """Return the most recent snapshot under ``root``.

        Ordered by the ``created`` timestamp in ``meta.json``, not by directory
        name -- default names are UTC timestamps and would sort correctly, but
        ``snapshot --name`` lets you call one anything at all.
        """
        candidates = sorted(available(root), key=lambda snap: str(snap.meta.get("created", "")))
        if not candidates:
            raise SnapshotError(f"No snapshots under {root}. Run 'waldur-tools snapshot' first.")
        return candidates[-1]

    @classmethod
    def named(cls, root: Path, name: str) -> Snapshot:
        """Return a specific snapshot by directory name."""
        path = root / name
        if not (path / META_FILENAME).exists():
            known = ", ".join(sorted(snap.path.name for snap in available(root))) or "none"
            raise SnapshotError(f"No snapshot {name!r} under {root}. Available: {known}")
        return cls(path)

    # -- io ----------------------------------------------------------------

    def write(self, endpoint: str, frame: pl.DataFrame) -> Path:
        target = self.path / _filename(endpoint)
        frame.write_parquet(target)
        return target

    def read(self, endpoint: str) -> pl.DataFrame:
        target = self.path / _filename(endpoint)
        if not target.exists():
            raise SnapshotError(
                f"{endpoint} is not in snapshot {self.path.name}; "
                f"run 'waldur-tools snapshot {endpoint}'"
            )
        return pl.read_parquet(target)

    @property
    def meta(self) -> dict[str, object]:
        target = self.path / META_FILENAME
        if not target.exists():
            return {}
        loaded: dict[str, object] = json.loads(target.read_text(encoding="utf-8"))
        return loaded

    def write_meta(self, endpoints: dict[str, int]) -> None:
        payload = {
            "created": datetime.now(UTC).isoformat(),
            "endpoints": endpoints,
        }
        (self.path / META_FILENAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )


def available(root: Path) -> list[Snapshot]:
    """Every complete snapshot under ``root``.

    A directory counts only once it has a ``meta.json``, which :func:`pull`
    writes last -- so a run interrupted half way is skipped rather than read as
    a snapshot with missing endpoints.
    """
    if not root.exists():
        return []
    return [Snapshot(path) for path in sorted(root.glob("*")) if (path / META_FILENAME).exists()]


def pull(
    client: WaldurClient,
    endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    *,
    root: Path | None = None,
    name: str | None = None,
) -> tuple[Snapshot, dict[str, int]]:
    """Fetch ``endpoints`` in full and write them to a new snapshot."""
    snapshot = Snapshot.create(root or client.settings.cache_dir, name)
    counts: dict[str, int] = {}
    for endpoint in endpoints:
        frame = to_frame(client.iter_list(endpoint))
        snapshot.write(endpoint, frame)
        counts[endpoint] = frame.height
    snapshot.write_meta(counts)
    return snapshot, counts


def load(source: Snapshot | WaldurClient, endpoints: Iterable[str]) -> dict[str, pl.DataFrame]:
    """Read endpoints from a snapshot, or straight from the API if given a client."""
    if isinstance(source, Snapshot):
        return {endpoint: source.read(endpoint) for endpoint in endpoints}
    return {endpoint: to_frame(source.iter_list(endpoint)) for endpoint in endpoints}
