from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from conftest import API_URL
from waldur_tools import client as client_module
from waldur_tools.client import MONTH_ATTEMPTS, WaldurClient, WaldurError, months_until

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


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Every retry in here is against a stub that answers instantly."""
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


def live_month_endpoint(rows, *, month=(2026, 1), grow_after=(), short_pages=0):
    """Mock the usage endpoint for a month that changes while it is read.

    ``grow_after`` names the requests *for that month* -- its count requests
    included -- after which one new row appears, which is what a live month does
    all day as jobs are accounted. ``short_pages`` drops a row from that many
    page responses instead, which is what unstable paging looks like.
    """
    table = list(rows)
    served = {"n": 0}

    def handler(request):
        params = request.url.params
        # Asked once at the start, and again at the end if the months outgrew
        # it -- so it has to answer from the table as it stands, not as it was.
        if "year" not in params:
            return httpx.Response(200, json=[], headers={"X-Result-Count": str(len(table))})
        key = (int(params["year"]), int(params["month"]))
        if key != month:
            return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})

        served["n"] += 1
        # One count request and one page request per attempt, so the attempt
        # number is the request number halved.
        attempt = (served["n"] + 1) // 2
        body: list[dict] = []
        if params.get("page_size") != "1":
            body = list(table[:-1]) if attempt <= short_pages else list(table)
        response = httpx.Response(200, json=body, headers={"X-Result-Count": str(len(table))})
        if served["n"] in grow_after:
            table.append({"allocation": "a", "user": f"late{len(table)}", "year": 2026, "month": 1})
        return response

    return respx.get(USAGE_URL).mock(side_effect=handler)


USAGE_KEYS = ("allocation", "user", "year", "month")


def usage_rows(count):
    return [
        {"allocation": "a", "user": f"u{index}", "year": 2026, "month": 1} for index in range(count)
    ]


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


@respx.mock
def test_by_month_re_pulls_an_inconsistent_month_and_takes_the_clean_attempt(settings):
    """Every fault these guards catch is a race, and a race is worth re-running.

    One month is a handful of requests against a pull that is otherwise done;
    failing the run for a page that would come back clean on the next attempt
    costs far more than asking again.
    """
    live_month_endpoint(usage_rows(2), short_pages=1)
    with WaldurClient(settings) as client:
        rows = list(
            client.iter_list_by_month(
                "openportal-allocation-user-usage", today=date(2026, 1, 5), row_keys=USAGE_KEYS
            )
        )
    assert len(rows) == 2


@respx.mock
def test_by_month_gives_up_once_the_attempts_are_spent(settings):
    """A fault that survives every attempt is not a race, and has to be said."""
    live_month_endpoint(usage_rows(2), short_pages=MONTH_ATTEMPTS)
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="fetched 1 rows but the server reported 2"),
    ):
        list(
            client.iter_list_by_month(
                "openportal-allocation-user-usage", today=date(2026, 1, 5), row_keys=USAGE_KEYS
            )
        )


@respx.mock
def test_by_month_keeps_a_month_that_grew_while_it_was_read(settings):
    """The live month: a row lands between the count and the last page.

    No key repeats, so every row in hand is a distinct real row and the pull
    holds at least what the opening count described; a fresh count equal to what
    is in hand settles that it holds exactly them. This used to end the run.
    """
    live_month_endpoint(usage_rows(2), grow_after=(1,))
    with WaldurClient(settings) as client:
        rows = list(
            client.iter_list_by_month(
                "openportal-allocation-user-usage", today=date(2026, 1, 5), row_keys=USAGE_KEYS
            )
        )
    assert len(rows) == 3


@respx.mock
def test_by_month_refuses_an_over_long_month_the_count_cannot_confirm(settings):
    """Growing again before the confirming count leaves the numbers apart."""
    live_month_endpoint(usage_rows(2), grow_after=(1, 2))
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="written to faster than it reads"),
    ):
        list(
            client.iter_list_by_month(
                "openportal-allocation-user-usage",
                today=date(2026, 1, 5),
                row_keys=USAGE_KEYS,
                attempts=1,
            )
        )


@respx.mock
def test_by_month_refuses_an_over_long_month_without_keys_to_judge_it(settings):
    """No key, no way to tell a new row from one handed back twice."""
    live_month_endpoint(usage_rows(2), grow_after=(1,))
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="no row keys were given"),
    ):
        list(
            client.iter_list_by_month(
                "openportal-allocation-user-usage", today=date(2026, 1, 5), attempts=1
            )
        )


@respx.mock
def test_by_month_rejects_an_endpoint_that_sends_no_count_header(settings):
    """No count is not a count of nothing.

    Every guard on this walk is arithmetic against ``X-Result-Count``, so a
    header that goes missing takes all of them with it -- and it does so in the
    quietest way there is: each month reads as empty, the walk succeeds, and a
    snapshot of no rows is written with nothing anywhere saying why.
    """

    def handler(request):
        return httpx.Response(200, json=[])

    respx.get(USAGE_URL).mock(side_effect=handler)
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="no readable X-Result-Count header"),
    ):
        list(client.iter_list_by_month("openportal-allocation-user-usage", today=date(2026, 1, 5)))


@respx.mock
def test_by_month_rejects_a_month_whose_count_header_goes_missing(settings):
    """The header can also disappear part-way through, one month at a time."""

    def handler(request):
        params = request.url.params
        if "year" not in params:
            return httpx.Response(200, json=[], headers={"X-Result-Count": "1"})
        if params["year"] == "1900":
            return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})
        if (int(params["year"]), int(params["month"])) == (2026, 1):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[], headers={"X-Result-Count": "0"})

    respx.get(USAGE_URL).mock(side_effect=handler)
    with (
        WaldurClient(settings) as client,
        pytest.raises(WaldurError, match="no readable X-Result-Count header"),
    ):
        list(client.iter_list_by_month("openportal-allocation-user-usage", today=date(2026, 1, 5)))
