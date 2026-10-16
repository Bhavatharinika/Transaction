"""Deterministic stand-in for the unreliable posting service, implemented as an httpx transport so tests can inject
connection errors, timeouts and post-write failures that a real HTTP server cannot reproduce on demand.

It records every call and every WRITE per transaction id. `writes[id] > 1` means a duplicate record would have
been created in the real service, which is exactly what the system must prevent.
"""
import asyncio
import json
from collections import defaultdict, deque

import httpx


class FakePostingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.writes: dict[str, int] = defaultdict(int)
        self.post_calls: dict[str, int] = defaultdict(int)
        self.calls: list[tuple[str, str]] = []
        self.plans: dict[str, deque[str]] = defaultdict(deque)
        self.default_script: deque[str] = deque()
        self.post_latency = 0.0
        self.late_write_delay = 0.1
        self.down = False

    def plan(self, tx_id, *behaviours: str) -> None:
        self.plans[str(tx_id)].extend(behaviours)

    def script(self, *behaviours: str) -> None:
        self.default_script.extend(behaviours)

    def preload(self, tx_id) -> None:
        self._write(str(tx_id), {"id": str(tx_id)})

    @property
    def duplicate_ids(self) -> list[str]:
        return [i for i, n in self.writes.items() if n > 1]

    def _write(self, tx_id: str, body: dict) -> None:
        self.writes[tx_id] += 1
        self.store[tx_id] = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("posting service down", request=request)
        path = request.url.path
        if request.method == "GET" and path.startswith("/transactions/"):
            tx_id = path.rsplit("/", 1)[1]
            self.calls.append(("GET", tx_id))
            return httpx.Response(200, json=self.store[tx_id]) if tx_id in self.store else httpx.Response(404, json={"error": "not found"})
        if request.method == "POST" and path == "/transactions":
            body = json.loads(request.content)
            return await self._post(request, body["id"], body)
        return httpx.Response(404)

    async def _post(self, request: httpx.Request, tx_id: str, body: dict) -> httpx.Response:
        self.calls.append(("POST", tx_id))
        self.post_calls[tx_id] += 1
        plan = self.plans[tx_id]
        behaviour = plan.popleft() if plan else (self.default_script.popleft() if self.default_script else "ok")
        if self.post_latency:
            await asyncio.sleep(self.post_latency)
        match behaviour:
            case "ok":
                self._write(tx_id, body)
                return httpx.Response(201, json={"id": tx_id})
            case "fail_before_write":
                return httpx.Response(500, json={"error": "boom"})
            case "fail_after_write":  # post-write failure: row saved, error returned
                self._write(tx_id, body)
                return httpx.Response(500, json={"error": "boom"})
            case "timeout_before_write":
                raise httpx.ReadTimeout("timeout", request=request)
            case "timeout_after_write":  # slow service: client times out, row lands a moment later
                asyncio.get_running_loop().call_later(self.late_write_delay, self._write, tx_id, body)
                raise httpx.ReadTimeout("timeout", request=request)
            case "reset_after_write":
                self._write(tx_id, body)
                raise httpx.RemoteProtocolError("connection reset", request=request)
            case "write_then_hang":  # used to simulate a worker crashing right after the service wrote the row
                self._write(tx_id, body)
                await asyncio.sleep(3600)
            case "http_400":
                return httpx.Response(400, json={"error": "invalid"})
        raise AssertionError(f"unknown behaviour {behaviour}")
