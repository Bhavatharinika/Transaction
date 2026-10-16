"""Pure-ASGI middleware (cheaper than BaseHTTPMiddleware): request IDs, latency metrics, access log."""
import logging
import re
import time
from uuid import uuid4

from app.core.logging import bind_context
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger("app.access")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATHS = {"/api/health", "/metrics"}


class RequestContextMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid4().hex
        bind_context(request_id=request_id)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                elapsed_ms = (time.perf_counter() - started) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.append((b"x-response-time-ms", f"{elapsed_ms:.2f}".encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            route = getattr(scope.get("route"), "path", "unmatched")
            HTTP_REQUESTS.labels(scope["method"], route, str(status["code"])).inc()
            HTTP_LATENCY.labels(scope["method"], route).observe(elapsed)
            level = logging.DEBUG if scope["path"] in _QUIET_PATHS else logging.INFO
            logger.log(
                level,
                "request",
                extra={"method": scope["method"], "path": scope["path"], "status": status["code"], "duration_ms": round(elapsed * 1000, 2)},
            )
