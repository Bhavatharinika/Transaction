"""Stats and health aggregation (real data only: DB, Redis, worker heartbeats)."""
import asyncio
import time
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.db.session import Database
from app.services import repository
from app.services.load_shedder import LoadShedder
from app.services.queue import TransactionQueue
from app.services.worker_registry import WorkerRegistry

RECENT_WINDOW_SECONDS = 300
_BREAKER_RANK = {"closed": 0, "half_open": 1, "open": 2}


class Observability:
    def __init__(self, db: Database, redis: Redis, queue: TransactionQueue, registry: WorkerRegistry, shedder: LoadShedder, settings: Settings) -> None:
        self._db, self._redis, self._queue, self._registry, self._shedder, self._s = db, redis, queue, registry, shedder, settings
        self._started = time.monotonic()
        self._cache: tuple[float, dict[str, Any]] | None = None

    async def stats(self) -> dict[str, Any]:
        """Dashboard numbers; cached ~1s so many dashboards polling cannot hammer PostgreSQL."""
        if self._cache and time.monotonic() - self._cache[0] < 1.0:
            return self._cache[1]
        async with self._db.transaction() as session:
            counts = await repository.status_counts(session)
            timings = await repository.recent_timings(session, RECENT_WINDOW_SECONDS)
            outcomes = await repository.recent_outcomes(session, RECENT_WINDOW_SECONDS)
        pending, completed, failed = counts.get("pending", 0), counts.get("completed", 0), counts.get("failed", 0)
        processing = counts.get("processing", 0) + counts.get("verifying", 0)
        finished = completed + failed
        post_total = outcomes["postOk"] + outcomes["postFailed"]
        result = {
            "total": pending + processing + completed + failed, "pending": pending, "processing": processing, "completed": completed, "failed": failed,
            "successRate": (completed / finished) if finished else None,
            "errorRate": (failed / finished) if finished else None,
            "avgProcessingSeconds": timings["avgProcessingSeconds"], "avgEndToEndSeconds": timings["avgEndToEndSeconds"],
            "postingErrorRate": (outcomes["postFailed"] / post_total) if post_total else None,
            "completedLast5m": timings["completed"],
            "queueDepth": self._shedder.queue_depth,
        }
        self._cache = (time.monotonic(), result)
        return result

    async def timeseries(self, minutes: int) -> list[dict[str, Any]]:
        async with self._db.transaction() as session:
            return await repository.timeseries(session, minutes)

    async def health(self) -> tuple[dict[str, Any], bool]:
        db_check, redis_check = await asyncio.gather(self._check_db(), self._check_redis())
        workers = await self._workers() if redis_check["status"] == "up" else []
        queue = {"depth": None, "inFlight": None, "maxDepth": self._s.queue_max_depth, "status": "unknown"}
        if redis_check["status"] == "up":
            try:
                depth, in_flight = await asyncio.gather(self._queue.depth(), self._queue.pending_count())
                queue.update(depth=depth, inFlight=in_flight, status="saturated" if depth >= self._s.queue_max_depth else "ok")
            except (RedisError, OSError, asyncio.TimeoutError):
                pass
        counts: dict[str, Any] = {}
        error_rates: dict[str, Any] = {"failedRatio5m": None, "postingErrorRate5m": None}
        if db_check["status"] == "up":
            snapshot = await self.stats()
            counts = {k: snapshot[k] for k in ("total", "pending", "processing", "completed", "failed")}
            error_rates = {"failedRatio5m": snapshot["errorRate"], "postingErrorRate5m": snapshot["postingErrorRate"]}
        breaker = max((w.get("circuitBreaker", "closed") for w in workers), key=lambda s: _BREAKER_RANK.get(s, 0), default=None)
        components_ok = db_check["status"] == "up"
        degraded = redis_check["status"] != "up" or not workers or breaker == "open" or queue["status"] == "saturated"
        status = "unhealthy" if not components_ok else ("degraded" if degraded else "healthy")
        body = {
            "status": status, "uptimeSeconds": round(time.monotonic() - self._started, 1),
            "components": {"api": {"status": "up"}, "database": db_check, "redis": redis_check},
            "queue": queue,
            "workers": {"alive": len(workers), "inFlight": sum(w.get("inflight", 0) for w in workers), "capacity": sum(w.get("concurrency", 0) for w in workers), "instances": workers},
            "circuitBreaker": {"state": breaker},
            "transactions": counts, "errorRates": error_rates,
        }
        return body, components_ok

    async def _check_db(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._db.ping(), 2)
            return {"status": "up", "latencyMs": round((time.perf_counter() - started) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "error": type(exc).__name__}

    async def _check_redis(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._redis.ping(), 2)
            return {"status": "up", "latencyMs": round((time.perf_counter() - started) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "error": type(exc).__name__}

    async def _workers(self) -> list[dict[str, Any]]:
        try:
            return await asyncio.wait_for(self._registry.list_workers(), 2)
        except Exception:  # noqa: BLE001
            return []
