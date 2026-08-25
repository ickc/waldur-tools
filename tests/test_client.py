from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from conftest import API_URL
from waldur_tools.client import WaldurClient, WaldurError, months_until

LIST_URL = f"{API_URL}/api/openportal-associations/"
USAGE_URL = f"{API_URL}/api/openportal-allocation-user-usage/"
ME_URL = f"{API_URL}/api/users/me/"


def usage_endpoint(months, *, total=None, unfiltered_1900=0):
    """Mock the usage endpoint as a table only readable a month at a time.

    ``months`` maps ``(year, month)`` to the rows that month holds. Any request
    carrying a ``year``/``month`` pair answers from it; anything else is the
    whole-table request, which only ever gets asked for its count.
    """
    rows = [row for month_rows in months.values() for row in month_rows]
    table_total = len(rows) if total is None else total

    def handler(request):
        params = request.url.params
        if "year" not in params:
            return httpx.Response(200, json=[], headers={"X-Result-Count": str(table_total)})
        key = (int(params["year"]), int(params["month"]))
        if key == (1900, 1):
            return httpx.Response(200, json=[], headers={"X-Result-Count": str(unfiltered_1900)})
        found = months.get(key, [])
        body = [] if params.get("page_size") == "1" else found
        return httpx.Response(200, json=body, headers={"X-Result-Count": str(len(found))})

    return respx.get(USAGE_URL).mock(side_effect=handler)


@respx.mock
def test_pagination_follows_link_header(settings):
    respx.get(LIST_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"username": "alice"}],
                headers={"Link": f'<{LIST_URL}?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"username": "bob"}]),
        ]
    )

    with WaldurClient(settings) as client:
        rows = client.list("openportal-associations")

    assert [row["username"] for row in rows] == ["alice", "bob"]


@respx.mock
def test_self_referential_next_link_does_not_spin(settings):
    """A page linking to itself must fail loudly rather than loop forever."""
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(
            200, json=[{"username": "alice"}], headers={"Link": f'<{LIST_URL}>; rel="next"'}
        )
    )
    with WaldurClient(settings) as client, pytest.raises(WaldurError, match="looped back"):
        client.list("openportal-associations")


@respx.mock
def test_sends_token_auth_header(settings):
    route = respx.get(LIST_URL).mock(return_value=httpx.Response(200, json=[]))
    with WaldurClient(settings) as client:
        client.list("openportal-associations")
    assert route.calls.last.request.headers["Authorization"] == "Token secret-token"


@respx.mock
def test_error_status_is_wrapped(settings):
    respx.get(LIST_URL).mock(return_value=httpx.Response(403, text="nope"))
    with WaldurClient(settings) as client, pytest.raises(WaldurError, match="403"):
        client.list("openportal-associations")


@respx.mock
def test_me_returns_the_account(settings):
    respx.get(ME_URL).mock(
        return_value=httpx.Response(200, json={"username": "alice", "full_name": "Alice Example"})
    )
    with WaldurClient(settings) as client:
        assert client.me()["username"] == "alice"


@respx.mock
def test_me_rejects_a_dead_token(settings):
    """A rejected token must raise here rather than read as an anonymous user.

    Tokens expire in hours, so this is the routine failure, and the body of the
    rejection is still perfectly good JSON -- it simply carries a ``detail``
    where the user should be. Reading ``username`` off it gives ``None``, which
    is why `whoami` once reported an expired token as a successful login.
    """
    respx.get(ME_URL).mock(return_value=httpx.Response(401, json={"detail": "Token has expired."}))
    with WaldurClient(settings) as client, pytest.raises(WaldurError, match="401"):
        client.me()


@respx.mock
def test_count_reads_result_header(settings):
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(200, json=[], headers={"X-Result-Count": "5246"})
    )
    with WaldurClient(settings) as client:
        assert client.count("openportal-associations") == 5246


@respx.mock
def test_non_list_payload_is_rejected(settings):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, json={"detail": "nope"}))
    with WaldurClient(settings) as client, pytest.raises(WaldurError, match="not a list"):
        client.list("openportal-associations")


# -- month-at-a-time paging ------------------------------------------------
#
# The endpoint this exists for is ordered by (year, month) alone, so paging it
# end to end returns some rows twice and drops others. These check the walk and
# both of the guards that stop that failing silently again.


def test_months_until_spans_the_year_boundary():
    assert list(months_until(date(2025, 2, 1), start=(2024, 11))) == [
        (2024, 11),
        (2024, 12),
        (2025, 1),
        (2025, 2),
    ]


@respx.mock
def test_by_month_walks_every_month_and_skips_the_empty_ones(settings):
    usage_endpoint({(2025, 12): [{"n": 1}], (2026, 2): [{"n": 2}, {"n": 3}]})
    with WaldurClient(settings) as client:
        rows = list(
            client.iter_list_by_month("openportal-allocation-user-usage", today=date(2026, 3, 15))
        )
    assert rows == [{"n": 1}, {"n": 2}, {"n": 3}]


@respx.mock
def test_by_month_rejects_an_endpoint_that_ignores_the_filter(settings):
    """Waldur's filters drop parameters they do not know, silently."""
    usage_endpoint({(2026, 1): [{"n": 1}]}, unfiltered_1900=1)
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="ignores the year/month filter"),
    ):
        list(client.iter_list_by_month("openportal-allocation-user-usage", today=date(2026, 1, 5)))


@respx.mock
def test_by_month_rejects_a_month_that_came_back_short(settings):
    """A page lost mid-pull must fail here, not turn up as a low total later."""

    def handler(request):
        params = request.url.params
        if "year" not in params:
            return httpx.Response(200, json=[], headers={"X-Result-Count": "2"})
        if params["year"] == "1900":
            return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})
        body = [] if params.get("page_size") == "1" else [{"n": 1}]
        return httpx.Response(200, json=body, headers={"X-Result-Count": "2"})

    respx.get(USAGE_URL).mock(side_effect=handler)
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="fetched 1 rows but the server reported 2"),
    ):
        list(client.iter_list_by_month("openportal-allocation-user-usage", today=date(2026, 1, 5)))


@respx.mock
def test_by_month_rejects_a_walk_that_misses_rows(settings):
    """Months that do not add up to the table mean the window is too narrow."""
    usage_endpoint({(2026, 1): [{"n": 1}]}, total=9)
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="1 rows across months but 9"),
    ):
        list(client.iter_list_by_month("openportal-allocation-user-usage", today=date(2026, 1, 5)))
