"""MockPostingServiceClient: the ONLY place that knows how the external posting service behaves.

Everything assumption-dependent is isolated here and marked ASSUMPTION so it can be adjusted in one spot if the
real container's responses differ:
  * GET /transactions/{id}: 404 => absent; 2xx with a transaction body => present.
  * POST /transactions: 2xx => written. 5xx / timeout / reset => OUTCOME UNKNOWN (may or may not have written).
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings
from app.core.metrics import MOCK_LATENCY, MOCK_REQUESTS

logger = logging.getLogger(__name__)


class FailureKind(str, Enum):
    NOT_WRITTEN = "not_written"  # provably never reached the service's write path (e.g. connection refused)
    UNCERTAIN = "uncertain"      # service may have written before failing: MUST verify with GET before any re-POST
    REJECTED = "rejected"        # definitive 4xx validation rejection: retrying cannot help


class PostingServiceError(Exception):
    def __init__(self, message: str, *, kind: FailureKind, code: str, status_code: int | None = None, counts_for_breaker: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.status_code = status_code
        self.counts_for_breaker = counts_for_breaker

    def as_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}


@dataclass(frozen=True)
class PostingPayload:
    id: UUID
    amount: Decimal
    currency: str
    description: str
    timestamp: datetime
    metadata: dict[str, Any] | None

    def to_json(self) -> dict[str, Any]:
        # ASSUMPTION: the mock accepts a JSON number for amount (as in the exercise's curl example).
        body: dict[str, Any] = {
            "id": str(self.id), "amount": float(self.amount), "currency": self.currency, "description": self.description,
            "timestamp": self.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }
        if self.metadata:
            body["metadata"] = self.metadata
        return body


_ID_KEYS = ("id", "transactionId", "transaction_id")


def response_indicates_exists(response: httpx.Response) -> bool:
    """ASSUMPTION (isolated): interpret a GET /transactions/{id} response. Tolerates several plausible shapes."""
    if response.status_code == 404:
        return False
    body: Any
    try:
        body = response.json()
    except ValueError:
        return bool(response.content.strip()) and response.status_code == 200
    if isinstance(body, dict):
        for wrapper in ("transaction", "data"):
            if wrapper in body:
                return bool(body[wrapper])
        if body.get("found") is False or body.get("exists") is False:
            return False
        return any(k in body for k in _ID_KEYS)
    if isinstance(body, list):
        return len(body) > 0
    return False


def classify_transport_error(exc: Exception, operation: str) -> PostingServiceError:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        # Connection never established / never left the pool: the request cannot have been processed.
        return PostingServiceError(f"{operation}: cannot reach posting service ({type(exc).__name__})", kind=FailureKind.NOT_WRITTEN, code="CONNECTION_ERROR")
    if isinstance(exc, httpx.TimeoutException):
        return PostingServiceError(f"{operation}: timed out waiting for posting service", kind=FailureKind.UNCERTAIN, code="TIMEOUT")
    return PostingServiceError(f"{operation}: transport failure ({type(exc).__name__})", kind=FailureKind.UNCERTAIN, code="TRANSPORT_ERROR")


def classify_status_error(status: int, operation: str) -> PostingServiceError:
    if status == 429:
        return PostingServiceError(f"{operation}: rate limited (HTTP 429)", kind=FailureKind.NOT_WRITTEN, code="HTTP_429", status_code=status)
    if status == 409:  # duplicate-id style conflict: the row probably exists; verification decides
        return PostingServiceError(f"{operation}: conflict (HTTP 409)", kind=FailureKind.UNCERTAIN, code="HTTP_409", status_code=status, counts_for_breaker=False)
    if 400 <= status < 500:
        return PostingServiceError(f"{operation}: rejected by posting service (HTTP {status})", kind=FailureKind.REJECTED, code=f"HTTP_{status}", status_code=status, counts_for_breaker=False)
    return PostingServiceError(f"{operation}: posting service error (HTTP {status})", kind=FailureKind.UNCERTAIN, code="HTTP_5XX" if status >= 500 else f"HTTP_{status}", status_code=status)


class MockPostingServiceClient:
    def __init__(self, base_url: str, *, http: httpx.AsyncClient | None = None, connect_timeout: float = 3.0, get_timeout: float = 5.0,
                 post_timeout: float = 15.0, max_connections: int = 200) -> None:
        self._get_timeout = get_timeout
        self._post_timeout = post_timeout
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=connect_timeout, read=post_timeout, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
        )

    @classmethod
    def from_settings(cls, s: Settings) -> "MockPostingServiceClient":
        return cls(s.mock_service_url, connect_timeout=s.mock_connect_timeout_seconds, get_timeout=s.mock_get_timeout_seconds,
                   post_timeout=s.mock_post_timeout_seconds, max_connections=s.mock_max_connections)

    async def exists(self, tx_id: UUID) -> bool:
        """GET /transactions/{id}. True/False are definitive; any failure raises PostingServiceError."""
        started = time.perf_counter()
        try:
            response = await self._http.get(f"/transactions/{tx_id}", timeout=self._get_timeout)
        except httpx.HTTPError as exc:
            self._observe("get", started, "error")
            raise classify_transport_error(exc, "GET") from exc
        if response.status_code == 404 or response.status_code < 300:
            found = response_indicates_exists(response)
            self._observe("get", started, "found" if found else "absent")
            return found
        self._observe("get", started, "error")
        error = classify_status_error(response.status_code, "GET")
        error.kind = FailureKind.UNCERTAIN  # a failed lookup is never proof of absence
        error.counts_for_breaker = response.status_code >= 500 or response.status_code == 429
        raise error

    async def post(self, payload: PostingPayload) -> None:
        """POST /transactions. Returns on 2xx; otherwise raises PostingServiceError classified by FailureKind."""
        started = time.perf_counter()
        try:
            response = await self._http.post("/transactions", json=payload.to_json(), timeout=self._post_timeout)
        except httpx.HTTPError as exc:
            self._observe("post", started, "error")
            raise classify_transport_error(exc, "POST") from exc
        if 200 <= response.status_code < 300:
            self._observe("post", started, "ok")
            return
        self._observe("post", started, "error")
        raise classify_status_error(response.status_code, "POST")

    async def cleanup(self) -> int | None:
        """POST /cleanup (testing only)."""
        response = await self._http.post("/cleanup", timeout=30)
        response.raise_for_status()
        try:
            return response.json().get("deleted")
        except (ValueError, AttributeError):
            return None

    async def ping(self) -> bool:
        try:
            await self._http.get("/transactions", params={"limit": 1}, timeout=2)
            return True
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    @staticmethod
    def _observe(operation: str, started: float, outcome: str) -> None:
        MOCK_REQUESTS.labels(operation, outcome).inc()
        MOCK_LATENCY.labels(operation).observe(time.perf_counter() - started)
