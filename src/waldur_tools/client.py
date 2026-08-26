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

import time
from collections.abc import Iterator, Sequence
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

#: How many times a month is pulled before its inconsistency is reported. Every
#: fault the guards below catch is a race against a table being written to, and
#: a race is worth losing twice before it is worth failing a pull for: one month
#: is a handful of requests, where a report is the whole run.
MONTH_ATTEMPTS = 3

#: Waited after a failed attempt, multiplied by the attempt number.
RETRY_BACKOFF_SECONDS = 0.3


def _repeats(rows: Sequence[JsonDict], keys: Sequence[str]) -> int:
    """Rows repeating a key already seen in ``rows``.

    Duplicates and omissions cancel out in a row *count*, so this is the only
    thing that sees an unstable page. ``cache.check`` runs the same test over
    the finished frame; this one runs per month, because whether a month may be
    kept turns on it -- see :meth:`WaldurClient.iter_list_by_month`.
    """
    if not keys:
        return 0
    seen: set[tuple[Any, ...]] = set()
    repeats = 0
    for row in rows:
        key = tuple(row.get(field) for field in keys)
        if key in seen:
            repeats += 1
        else:
            seen.add(key)
    return repeats


def months_until(today: date, start: tuple[int, int] = EARLIEST_MONTH) -> Iterator[tuple[int, int]]:
    """Every ``(year, month)`` from ``start`` to the month containing ``today``."""
    year, month = start
    while (year, month) <= (today.year, today.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


class WaldurError(RuntimeError):
    """An API call failed.

    ``transient`` marks the ones whose only cure is to run the command again:
    the portal being written to while it is read. Everything the guards below
    can catch has already been re-pulled :const:`MONTH_ATTEMPTS` times by the
    time it is raised, so the flag means "and it still did not settle", not
    "this has not been tried". The CLI prints the command back for those and
    nothing extra for the rest -- telling someone to try again after a rejected
    token or a dropped filter only wastes another run.
    """

    transient = False

    def __init__(self, *args: object, transient: bool = False) -> None:
        super().__init__(*args)
        self.transient = transient


def _missing_count_header(endpoint: str) -> WaldurError:
    """Said when ``X-Result-Count`` is absent, because silence is the worst answer.

    Every guard on a by-month pull is arithmetic against that header, so a pull
    that cannot read it has no guards at all -- and the shape of that failure is
    the worst one available: :meth:`WaldurClient.count` answers ``None``, a
    caller that reads ``None`` as a zero takes the month for an empty one, and
    the snapshot is written holding no rows and no error. Nothing downstream can
    tell that table from a quiet month. So it is checked for, and said out loud.

    Not ``transient``: a header that is not being sent will not be sent on the
    next run either, and the fix is at the deployment rather than here.
    """
    return WaldurError(
        f"{endpoint} returned no readable X-Result-Count header, so none of the paging "
        "guards can run and no total built from it can be trusted."
    )


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

    def me(self) -> JsonDict:
        """The account the token belongs to.

        Exists so that ``whoami`` reads through :meth:`_get` like every other
        call here. A rejected token still answers with a JSON body -- a
        ``detail`` rather than a user -- so reading ``username`` off it yields
        ``None`` rather than an error, and the status code is the only thing
        that distinguishes a dead token from a live one.
        """
        payload: JsonDict = self._get(f"{self.settings.api_url}/api/users/me/").json()
        return payload

    def count(self, endpoint: str, **filters: Any) -> int | None:
        """Rows an endpoint reports, via the ``X-Result-Count`` header.

        ``filters`` are passed through, so this also counts a slice -- which is
        what :meth:`iter_list_by_month` checks each month's pull against.

        ``None`` when the header is absent, and every caller that guards a pull
        with it has to say so rather than read it as a zero -- see
        :func:`_missing_count_header`.
        """
        response = self._get(self._url(endpoint), params={"page_size": 1, **filters})
        header = response.headers.get("x-result-count")
        return int(header) if header is not None else None

    def _count_or_fail(self, endpoint: str, **filters: Any) -> int:
        """:meth:`count`, for the callers that cannot proceed without one."""
        total = self.count(endpoint, **filters)
        if total is None:
            raise _missing_count_header(endpoint)
        return total

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
        row_keys: Sequence[str] = (),
        attempts: int = MONTH_ATTEMPTS,
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

        **The month in progress is written to while it is read**, which is a
        different fault and takes a different answer. Usage rows land as jobs
        are accounted, and ``X-Result-Count`` is a count taken with the first
        page while every later page's ``OFFSET`` resolves against the table as
        it is by then. One insert mid-crawl lengthens the tail, and the pull
        ends holding *more* rows than the count promised -- which is the portal
        working, not failing, and used to end the whole run.

        So the checks are ranked rather than run in sequence. A repeated key
        fails always: it is the fault that costs rows, since where one came back
        twice another never came at all, and nothing about a live month excuses
        it. A short pull fails: fewer rows than a count taken *before* the read
        cannot be explained by rows arriving during it. An over-long pull is
        confirmed rather than assumed -- with no repeated key every row in hand
        is a distinct real row, so the pull holds at least what the opening
        count described, and a fresh count equal to the rows in hand settles
        that it holds exactly them. Anything else is unresolved and fails.

        ``row_keys`` is what makes that ruling possible, so a caller that does
        not pass them gets the old, stricter behaviour: without a key there is
        no way to tell a distinct row from a repeated one, and an over-long pull
        can only be refused. :func:`waldur_tools.cache.fetch` passes
        ``cache.ROW_KEYS``.

        Every one of those faults is a race, and a race is retried ``attempts``
        times before it is reported.

        None of it runs without ``X-Result-Count``, so an endpoint that does not
        send one ends the pull rather than being read as an empty table.
        """
        total = self._count_or_fail(endpoint)

        # 1900 predates every Waldur deployment: a non-zero answer means the
        # filter was dropped and we are looking at the unfiltered table.
        if self._count_or_fail(endpoint, year=1900, month=1):
            raise WaldurError(
                f"{endpoint} ignores the year/month filter, so it cannot be pulled "
                "a month at a time. Remove it from cache.BY_MONTH."
            )

        seen = 0
        for year, month in months_until(today or date.today()):
            rows = self._pull_month(
                endpoint,
                year,
                month,
                page_size=page_size,
                row_keys=row_keys,
                attempts=attempts,
            )
            seen += len(rows)
            yield from rows

        # The months must add up to the table as a whole, or the window in
        # months_until is too narrow and a year of usage is missing from every
        # figure without saying so. Ruled on the way one month is: fewer rows
        # than the table claimed is damage, more is the table having grown
        # between that count and the last page -- and every month is already
        # verified row by row, so a fresh count that agrees is all that is left.
        if seen < total:
            # Not a race, and not marked as one. Every month here was verified
            # row by row against its own count, so the rows are not missing from
            # the months -- there are months missing from the walk. Another run
            # covers exactly the same window and fails in exactly the same way.
            raise WaldurError(
                f"{endpoint}: {seen} rows across months but {total} in the table as a "
                "whole. Either the window in client.months_until is too narrow, or rows "
                "were deleted while it ran."
            )
        if seen > total:
            now = self._count_or_fail(endpoint)
            if now != seen:
                raise WaldurError(
                    f"{endpoint}: {seen} rows across months but {total} in the table as a "
                    f"whole, and {now} in it now. Rows changed under the pull.",
                    transient=True,
                )

    def _pull_month(
        self,
        endpoint: str,
        year: int,
        month: int,
        *,
        page_size: int,
        row_keys: Sequence[str],
        attempts: int,
    ) -> list[JsonDict]:
        """One month, re-pulled while it keeps coming back inconsistent."""
        failure: WaldurError | None = None
        for attempt in range(1, max(attempts, 1) + 1):
            rows, failure = self._pull_month_once(
                endpoint, year, month, page_size=page_size, row_keys=row_keys
            )
            if failure is None:
                return rows
            if attempt < attempts:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise cast(WaldurError, failure)

    def _pull_month_once(
        self,
        endpoint: str,
        year: int,
        month: int,
        *,
        page_size: int,
        row_keys: Sequence[str],
    ) -> tuple[list[JsonDict], WaldurError | None]:
        """One attempt: the rows, and the reason they cannot be trusted."""
        name = f"{endpoint} {year}-{month:02d}"
        # Raised rather than returned as a failure: an absent header is not a
        # race, and re-pulling the month cannot make one appear.
        expected = self._count_or_fail(endpoint, year=year, month=month)
        if not expected:
            return [], None
        rows = list(self.iter_list(endpoint, page_size=page_size, year=year, month=month))

        repeats = _repeats(rows, row_keys)
        if repeats:
            return rows, WaldurError(
                f"{name}: {repeats} of {len(rows)} rows repeat a key already seen, so as "
                "many are missing. The portal paged the month inconsistently.",
                transient=True,
            )
        if len(rows) == expected:
            return rows, None
        if len(rows) < expected:
            return rows, WaldurError(
                f"{name}: fetched {len(rows)} rows but the server reported {expected}. "
                "Pagination is unstable.",
                transient=True,
            )
        # More rows than the count promised and none of them a repeat: the month
        # grew while it was read. Only a count that agrees with what is in hand
        # settles that nothing was lost along the way.
        if not row_keys:
            return rows, WaldurError(
                f"{name}: fetched {len(rows)} rows but the server reported {expected}, and "
                "no row keys were given to tell a new row from a repeated one."
            )
        now = self._count_or_fail(endpoint, year=year, month=month)
        if now == len(rows):
            return rows, None
        return rows, WaldurError(
            f"{name}: fetched {len(rows)} distinct rows against a count of {expected} that "
            f"has since moved to {now}. The month is being written to faster than it reads.",
            transient=True,
        )

    def list(self, endpoint: str, **filters: Any) -> list[JsonDict]:
        """Eagerly collect every record from a list endpoint."""
        return list(self.iter_list(endpoint, **filters))

    # -- helpers -----------------------------------------------------------

    def _url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.settings.api_url}/api/{endpoint.strip('/')}/"
