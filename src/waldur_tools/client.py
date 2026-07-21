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
"""

from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Any, cast

import httpx
from waldur_api_client.client import AuthenticatedClient
from waldur_api_client.utils import parse_link_header

from .config import Settings

JsonDict = dict[str, Any]

DEFAULT_PAGE_SIZE = 200


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

    def count(self, endpoint: str) -> int | None:
        """Total rows an endpoint reports, via the ``X-Result-Count`` header."""
        response = self._get(self._url(endpoint), params={"page_size": 1})
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

    def list(self, endpoint: str, **filters: Any) -> list[JsonDict]:
        """Eagerly collect every record from a list endpoint."""
        return list(self.iter_list(endpoint, **filters))

    # -- helpers -----------------------------------------------------------

    def _url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.settings.api_url}/api/{endpoint.strip('/')}/"
