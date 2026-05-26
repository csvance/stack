"""httpx-based Azure DevOps REST API client.

Uses PAT auth via HTTP Basic (empty username, PAT as password — ADO REST API
convention). Retries on 5xx with exponential backoff.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_INITIAL_DELAY = 1.0


class AdoApiError(Exception):
    """Raised when an ADO API call fails after retries."""

    def __init__(self, status_code: int, body: str, url: str) -> None:
        super().__init__(f"ADO API error {status_code} for {url}: {body[:500]}")
        self.status_code = status_code
        self.body = body
        self.url = url


class AdoClient:
    """Authenticated client for one ADO organization.

    Wraps ``httpx.Client`` with PAT auth, sensible defaults, and 5xx retry logic.
    Path arguments are joined onto the organization URL. The ``api-version`` query
    parameter must be supplied by the caller per ADO convention.
    """

    def __init__(
        self,
        organization_url: str,
        pat: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_initial_delay: float = DEFAULT_RETRY_INITIAL_DELAY,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=organization_url.rstrip("/"),
            auth=("", pat),
            timeout=timeout,
            transport=transport,
        )
        self._retry_attempts = retry_attempts
        self._retry_initial_delay = retry_initial_delay

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AdoClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get(self, path: str, **params: Any) -> httpx.Response:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict[str, Any], **params: Any) -> httpx.Response:
        return self._request("POST", path, params=params, json=json)

    def patch(self, path: str, json: dict[str, Any], **params: Any) -> httpx.Response:
        return self._request("PATCH", path, params=params, json=json)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_response: httpx.Response | None = None
        delay = self._retry_initial_delay
        for attempt in range(1, self._retry_attempts + 1):
            response = self._client.request(method, path, params=params, json=json)
            last_response = response
            if response.status_code < 500:
                if response.is_success:
                    return response
                raise AdoApiError(response.status_code, response.text, str(response.url))
            if attempt < self._retry_attempts:
                time.sleep(delay)
                delay *= 2
        assert last_response is not None
        raise AdoApiError(last_response.status_code, last_response.text, str(last_response.url))
