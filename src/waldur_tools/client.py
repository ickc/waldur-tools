"""A thin facade over the official ``waldur-api-client``.

The generated client owns authentication, the base URL and the ``httpx``
transport. On top of that this module adds the one thing the generated code
does not give us: a uniform, paginated *raw JSON* reader.

Why raw JSON rather than the generated per-endpoint functions? Two reasons:

* The Isambard endpoints we care about most (``openportal-project-usage-reports``
  and friends) carry deeply nested, free-form ``report`` payloads that the
  generated attrs models flatten into opaque objects.
* Iterating over ~200 endpoints by name is far simpler than importing ~200
  generated modules.

The typed API remains one attribute away: pass :attr:`WaldurClient.raw` to any
``waldur_api_client.api.*.sync`` function when you want models instead of dicts.

**Not every list endpoint can be paged straight through.** ``page``/``page_size``
is ``LIMIT``/``OFFSET`` underneath, and that is only well defined over a totally
ordered queryset. ``openportal-allocation-user-usage`` is not one, and paging it
end to end silently returns some rows twice and drops others --
:meth:`WaldurClient.iter_list_by_month` is the workaround. See
:const:`waldur_tools.cache.BY_MONTH` and DEVELOPER.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from types import TracebackType
from typing import Any, cast

import httpx
from waldur_api_client.client import AuthenticatedClient
from waldur_api_client.utils import parse_link_header

from .config import Settings

JsonDict = dict[str, Any]

DEFAULT_PAGE_SIZE = 200

#: The first month :meth:`WaldurClient.iter_list_by_month` looks in. Isambard 3
#: has no usage rows before 2025 and the loop has to start somewhere; a year of
#: slack costs twelve cheap count requests and covers a backfill.
EARLIEST_MONTH = (2024, 1)


def months_until(today: date, start: tuple[int, int] = EARLIEST_MONTH) -> Iterator[tuple[int, int]]:
    """Every ``(year, month)`` from ``start`` to the month containing ``today``."""
    year, month = start
    while (year, month) <= (today.year, today.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


class WaldurError(RuntimeError):
    """An API call failed."""


class WaldurClient:
    """Authenticated access to a Waldur deployment."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.raw = AuthenticatedClient(
            base_url=self.settings.api_url,
            token=self.settings.token,
            prefix="Token",
            timeout=httpx.Timeout(self.settings.timeout),
            follow_redirects=True,
        )

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> WaldurClient:
        self.raw.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.raw.__exit__(exc_type, exc, tb)

    @property
    def http(self) -> httpx.Client:
        # The generated client is not typed strictly enough to prove this.
        return cast("httpx.Client", self.raw.get_httpx_client())

    # -- reading -----------------------------------------------------------

    def _get(self, url: str, params: JsonDict | None = None) -> httpx.Response:
        try:
            response = self.http.get(url, params=params)
        except httpx.HTTPError as error:  # network-level
            raise WaldurError(f"GET {url} failed: {error}") from error
        if response.status_code != httpx.codes.OK:
            raise WaldurError(f"GET {url} returned {response.status_code}: {response.text[:300]}")
        return response

    def endpoints(self) -> dict[str, str]:
        """Return the API root: a mapping of endpoint name to absolute URL."""
        payload: dict[str, str] = self._get(f"{self.settings.api_url}/api/").json()
        return payload

    def count(self, endpoint: str, **filters: Any) -> int | None:
        """Rows an endpoint reports, via the ``X-Result-Count`` header.

        ``filters`` are passed through, so this also counts a slice -- which is
        what :meth:`iter_list_by_month` checks each month's pull against.
        """
        response = self._get(self._url(endpoint), params={"page_size": 1, **filters})
        header = response.headers.get("x-result-count")
        return int(header) if header is not None else None

    def iter_list(
        self,
        endpoint: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        **filters: Any,
    ) -> Iterator[JsonDict]:
        """Yield every record from a list endpoint, following ``Link`` headers."""
        url: str | None = self._url(endpoint)
        params: JsonDict | None = {"page_size": page_size, **filters}
        seen: set[str] = set()

        while url:
            response = self._get(url, params=params)
            payload = response.json()
            if not isinstance(payload, list):
                raise WaldurError(
                    f"{endpoint} is not a list endpoint (got {type(payload).__name__})"
                )
            yield from payload

            seen.add(str(response.url))
            # Subsequent URLs from the Link header already carry the query string.
            url = parse_link_header(response.headers.get("Link", "")).get("next")
            params = None
            if url in seen:
                # A page that links to itself would otherwise spin forever.
                raise WaldurError(f"{endpoint} pagination looped back to {url}")

    def iter_list_by_month(
        self,
        endpoint: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        today: date | None = None,
    ) -> Iterator[JsonDict]:
        """Yield every record, pulling one ``year``/``month`` slice at a time.

        For endpoints that cannot be paged end to end. ``page``/``page_size``
        becomes ``LIMIT``/``OFFSET``, which only enumerates a queryset once if
        that queryset is *totally* ordered.
        ``openportal-allocation-user-usage`` is ordered by ``(year, month)``
        alone, so within a month the database is free to return rows in any
        order it likes -- and does, differently per request. Paging the whole
        table therefore hands back some rows two or three times and never shows
        others: a full pull of tens of thousands of rows held thousands of
        duplicate ``(allocation, user, year, month)`` keys, most of them
        straddling two adjacent pages. Summed into a monthly total that
        inflated one month to well over 100% of the organisation's share,
        against a figure well under 100% from the portal's own dashboard --
        which is where the inflated headline came from.

        Filtering to one month shrinks the queryset to something the server
        enumerates consistently: the same pull, taken a month at a time, is
        duplicate-free and matches the portal to within rounding.

        Two guards, because the failure mode is silent. Waldur's DRF filters
        ignore query parameters they do not recognise, so an endpoint without
        ``year``/``month`` would otherwise be fetched once per month and yield
        the whole table over and over; the probe below catches that. And each
        month's row count is checked against ``X-Result-Count`` for that same
        filter, so a short page fails here rather than in a report.
        """
        total = self.count(endpoint)

        # 1900 predates every Waldur deployment: a non-zero answer means the
        # filter was dropped and we are looking at the unfiltered table.
        if self.count(endpoint, year=1900, month=1):
            raise WaldurError(
                f"{endpoint} ignores the year/month filter, so it cannot be pulled "
                "a month at a time. Remove it from cache.BY_MONTH."
            )

        seen = 0
        for year, month in months_until(today or date.today()):
            expected = self.count(endpoint, year=year, month=month)
            if not expected:
                continue
            rows = list(self.iter_list(endpoint, page_size=page_size, year=year, month=month))
            if len(rows) != expected:
                raise WaldurError(
                    f"{endpoint} {year}-{month:02d}: fetched {len(rows)} rows but the "
                    f"server reported {expected}. Pagination is unstable; retry."
                )
            seen += len(rows)
            yield from rows

        if total is not None and seen != total:
            raise WaldurError(
                f"{endpoint}: {seen} rows across months but {total} in the table as a "
                "whole. Either the window in client.months_until is too narrow, or "
                "rows changed under the pull; retry."
            )

    def list(self, endpoint: str, **filters: Any) -> list[JsonDict]:
        """Eagerly collect every record from a list endpoint."""
        return list(self.iter_list(endpoint, **filters))

    # -- helpers -----------------------------------------------------------

    def _url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.settings.api_url}/api/{endpoint.strip('/')}/"
