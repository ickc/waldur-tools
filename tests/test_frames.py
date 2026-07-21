from __future__ import annotations

import json

import polars as pl

from waldur_tools.frames import numeric, to_frame, unpack_json


def test_nested_values_become_json_text():
    frame = to_frame([{"a": 1, "report": {"x": [1, 2]}}])
    assert frame["report"][0] == json.dumps({"x": [1, 2]}, sort_keys=True)


def test_ragged_records_are_unioned():
    frame = to_frame([{"a": 1}, {"b": 2}])
    assert set(frame.columns) == {"a", "b"}
    assert frame["a"].to_list() == [1, None]


def test_empty_input_gives_empty_frame():
    assert to_frame([]).is_empty()


def test_unpack_json_roundtrips():
    frame = to_frame([{"report": {"n": 1}}, {"report": None}])
    assert unpack_json(frame, "report") == [{"n": 1}, {}]
    assert unpack_json(frame, "absent") == []


def test_numeric_casts_decimal_strings():
    frame = numeric(pl.DataFrame({"spend": ["1.50", None]}), "spend", "missing")
    assert frame["spend"].to_list() == [1.5, None]
