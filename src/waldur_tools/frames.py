"""Turning Waldur's JSON into tidy polars frames."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from polars._typing import PolarsDataType

JsonDict = dict[str, Any]


def _scalarise(value: Any) -> Any:
    """Collapse nested containers to JSON text so the schema stays flat.

    Waldur mixes flat fields with free-form nested payloads (notably the daily
    ``report`` blobs). Inferring a struct schema across thousands of such rows
    is both slow and fragile, so nested values become strings and callers
    explode only the parts they need.
    """
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    return value


def to_frame(records: Iterable[JsonDict]) -> pl.DataFrame:
    """Build a flat DataFrame from API records, JSON-encoding nested fields."""
    rows = [{key: _scalarise(value) for key, value in record.items()} for record in records]
    if not rows:
        return pl.DataFrame()
    # Union the keys so rows with missing optional fields still line up.
    columns = list(dict.fromkeys(key for row in rows for key in row))
    normalised = [{column: row.get(column) for column in columns} for row in rows]
    return pl.DataFrame(normalised, infer_schema_length=None, strict=False)


def unpack_json(frame: pl.DataFrame, column: str) -> list[JsonDict]:
    """Decode a JSON-encoded column back into Python objects, row by row."""
    if column not in frame.columns:
        return []
    return [json.loads(value) if value else {} for value in frame[column].to_list()]


def numeric(frame: pl.DataFrame, *columns: str) -> pl.DataFrame:
    """Cast the named columns to Float64, tolerating nulls and strings.

    Waldur serialises money and usage as decimal strings.
    """
    return _cast(frame, columns, pl.Float64)


def integral(frame: pl.DataFrame, *columns: str) -> pl.DataFrame:
    """Cast the named columns to Int64, tolerating nulls and strings."""
    return _cast(frame, columns, pl.Int64)


def _cast(frame: pl.DataFrame, columns: tuple[str, ...], dtype: PolarsDataType) -> pl.DataFrame:
    present = [column for column in columns if column in frame.columns]
    if not present:
        return frame
    return frame.with_columns(
        pl.col(column).cast(pl.String).cast(dtype, strict=False) for column in present
    )
