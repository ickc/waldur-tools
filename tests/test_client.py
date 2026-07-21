from __future__ import annotations

import httpx
import pytest
import respx

from conftest import API_URL
from waldur_tools.client import WaldurClient, WaldurError

LIST_URL = f"{API_URL}/api/openportal-associations/"


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
