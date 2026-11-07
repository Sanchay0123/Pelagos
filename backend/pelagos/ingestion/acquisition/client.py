"""Pelagos Resilient HTTP Source Acquisition Client.

Provides bounded timeouts, exponential backoff retries, status code and content validation,
and auditable retrieval timestamps for external maritime data sources.
Traces to REQ-NFR-DIP-001, REQ-NFR-ARG-003, and Stage B Acquisition Mandates.
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Pelagos-Maritime-Platform/1.0 (+https://github.com/Pelagos/maritime-platform; data-acquisition)"


class AcquisitionError(Exception):
    """Base exception for external source acquisition failures."""
    pass


class AcquisitionTimeoutError(AcquisitionError):
    """Raised when an external request exceeds the configured timeout."""
    pass


class AcquisitionNetworkError(AcquisitionError):
    """Raised on connection, DNS, or transport layer errors."""
    pass


class AcquisitionHTTPError(AcquisitionError):
    """Raised on non-2xx HTTP response status codes."""

    def __init__(self, message: str, status_code: int, url: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


@dataclass
class AcquisitionResponse:
    """Auditable container for HTTP retrieval results."""
    url: str
    status_code: int
    text: str
    content: bytes
    retrieval_time_utc: datetime.datetime
    duration_seconds: float
    headers: Dict[str, str]

    def json(self) -> Any:
        import json
        return json.loads(self.text)


class ResilientHttpClient:
    """HTTP client with configurable timeout, exponential backoff retries, and retrieval auditing."""

    def __init__(
        self,
        timeout_seconds: float = 12.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.user_agent = user_agent

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> AcquisitionResponse:
        """Execute HTTP GET with bounded retries and exponential backoff.

        Args:
            url: Target URL.
            params: Optional query parameters.
            headers: Optional HTTP headers.

        Returns:
            AcquisitionResponse containing content, status, and UTC retrieval timestamp.

        Raises:
            AcquisitionTimeoutError: When all retries timeout.
            AcquisitionNetworkError: On connection/network failures after retries.
            AcquisitionHTTPError: On 4xx/5xx HTTP errors.
        """
        request_headers = {"User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)

        last_exception: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            start_wall = time.time()
            retrieval_time = datetime.datetime.now(datetime.timezone.utc)
            try:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    resp = client.get(url, params=params, headers=request_headers)
                    duration = time.time() - start_wall

                    if resp.status_code >= 400:
                        # 4xx client errors (except 429) usually should not be retried blindly
                        if resp.status_code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                            raise AcquisitionHTTPError(
                                f"HTTP {resp.status_code} from {url}: {resp.text[:200]}",
                                status_code=resp.status_code,
                                url=url,
                            )
                        logger.warning(
                            f"Transient HTTP {resp.status_code} for {url} on attempt {attempt}/{self.max_retries}. Retrying..."
                        )
                    else:
                        return AcquisitionResponse(
                            url=str(resp.url),
                            status_code=resp.status_code,
                            text=resp.text,
                            content=resp.content,
                            retrieval_time_utc=retrieval_time,
                            duration_seconds=duration,
                            headers=dict(resp.headers),
                        )

            except httpx.TimeoutException as exc:
                last_exception = AcquisitionTimeoutError(f"Request to {url} timed out after {self.timeout_seconds}s (attempt {attempt}/{self.max_retries})")
                logger.warning(f"Timeout on {url} (attempt {attempt}/{self.max_retries}): {exc}")
            except httpx.RequestError as exc:
                last_exception = AcquisitionNetworkError(f"Network error requesting {url}: {exc} (attempt {attempt}/{self.max_retries})")
                logger.warning(f"Network error on {url} (attempt {attempt}/{self.max_retries}): {exc}")
            except AcquisitionHTTPError as exc:
                last_exception = exc
                if exc.status_code in (400, 401, 403, 404):
                    # Do not retry fatal client errors
                    raise

            if attempt < self.max_retries:
                sleep_sec = self.backoff_factor * (2 ** (attempt - 1))
                time.sleep(sleep_sec)

        # Retries exhausted
        if isinstance(last_exception, AcquisitionTimeoutError):
            raise last_exception
        elif isinstance(last_exception, AcquisitionHTTPError):
            raise last_exception
        raise AcquisitionNetworkError(f"Failed to acquire {url} after {self.max_retries} attempts: {last_exception}")
