"""The snapshot layer, and the guard that stands between it and a bad pull."""

from __future__ import annotations

import httpx
import pytest
import respx

from conftest import API_URL
from waldur_tools.cache import BY_MONTH, SnapshotError, check, fetch, pull
from waldur_tools.client import WaldurClient
from waldur_tools.frames import to_frame

USAGE = "openportal-allocation-user-usage"
USAGE_URL = f"{API_URL}/api/{USAGE}/"


def usage_row(allocation="aaa", user="alice", year=2026, month=3, node_usage="10.0"):
    return {
        "allocation": f"{API_URL}/api/openportal-allocations/{allocation}/",
        "user": f"{API_URL}/api/users/{user}/",
        "username": f"{user}.abc1.brics",
        "node_usage": node_usage,
        "year": year,
        "month": month,
    }


# -- the duplicate guard ---------------------------------------------------
#
# A repeated (allocation, user, year, month) is how the portal's unstable
# paging shows up. It has to be caught: duplicates and the omissions that
# accompany them cancel out in a row count, so X-Result-Count sees nothing.


def test_check_passes_a_clean_frame():
    frame = to_frame([usage_row(user="alice"), usage_row(user="bob")])
    assert check(USAGE, frame).height == 2


def test_check_rejects_a_repeated_row():
    frame = to_frame([usage_row(), usage_row(node_usage="0.00")])
    with pytest.raises(SnapshotError, match="1 of 2 rows repeat"):
        check(USAGE, frame)


def test_check_counts_a_row_returned_twice_as_usage_twice():
    """The damage the guard exists to prevent, stated as a number."""
    doubled = to_frame([usage_row(), usage_row()])
    assert doubled["node_usage"].cast(float).sum() == 20.0
    with pytest.raises(SnapshotError):
        check(USAGE, doubled)


def test_check_ignores_endpoints_it_has_no_key_for():
    frame = to_frame([{"username": "alice"}, {"username": "alice"}])
    assert check("openportal-associations", frame).height == 2


def test_check_skips_when_the_key_is_not_all_there():
    """Two thirds of a compound key is not a key -- do not judge on it."""
    frame = to_frame([{"year": 2026, "month": 3}, {"year": 2026, "month": 3}])
    assert check(USAGE, frame).height == 2


# -- routing ---------------------------------------------------------------


def test_the_usage_endpoint_is_pulled_a_month_at_a_time():
    assert USAGE in BY_MONTH


@respx.mock
def test_fetch_routes_the_usage_endpoint_through_the_monthly_walk(settings):
    """One request per month with a filter, never a naive walk of the table."""
    seen = []

    def handler(request):
        params = request.url.params
        seen.append(dict(params))
        if "year" not in params:
            return httpx.Response(200, json=[], headers={"X-Result-Count": "1"})
        if params["year"] == "1900":
            return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})
        if (params["year"], params["month"]) != ("2026", "3"):
            return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})
        body = [] if params.get("page_size") == "1" else [usage_row()]
        return httpx.Response(200, json=body, headers={"X-Result-Count": "1"})

    respx.get(USAGE_URL).mock(side_effect=handler)
    with WaldurClient(settings) as client:
        frame = fetch(client, USAGE)

    assert frame.height == 1
    assert all("year" in params or params.get("page_size") == "1" for params in seen)


@respx.mock
def test_fetch_leaves_other_endpoints_on_the_plain_walk(settings):
    url = f"{API_URL}/api/openportal-associations/"
    respx.get(url).mock(return_value=httpx.Response(200, json=[{"username": "alice.abc1"}]))
    with WaldurClient(settings) as client:
        assert fetch(client, "openportal-associations").height == 1


@respx.mock
def test_pull_refuses_to_write_a_snapshot_with_repeated_rows(settings, tmp_path):
    """Better no snapshot than one that quietly doubles a month's usage.

    The month here returns the row count the server promised -- two rows, two
    reported -- and both rows are the same one. That is the real failure, and
    it is invisible to every check except the key.
    """

    def handler(request):
        params = request.url.params
        if "year" not in params:
            return httpx.Response(200, json=[], headers={"X-Result-Count": "2"})
        if params["year"] == "1900" or (params["year"], params["month"]) != ("2026", "3"):
            return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})
        body = [] if params.get("page_size") == "1" else [usage_row(), usage_row()]
        return httpx.Response(200, json=body, headers={"X-Result-Count": "2"})

    respx.get(USAGE_URL).mock(side_effect=handler)
    with WaldurClient(settings) as client, pytest.raises(SnapshotError, match="retry the snapshot"):
        pull(client, [USAGE], root=tmp_path, name="bad")

    assert not (tmp_path / "bad" / "meta.json").exists()


@respx.mock
def test_pull_writes_a_snapshot_and_its_counts(settings, tmp_path):
    respx.get(f"{API_URL}/api/users/").mock(
        return_value=httpx.Response(200, json=[{"unix_username": "alice"}])
    )
    with WaldurClient(settings) as client:
        snapshot, counts = pull(client, ["users"], root=tmp_path, name="one")
    assert counts == {"users": 1}
    assert snapshot.read("users").height == 1
