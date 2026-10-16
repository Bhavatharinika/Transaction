"""WorkerRuntime: consumer loop + the background loops that make the system self-healing.

Loops
  consume    XREADGROUP -> bounded-concurrency processing -> XACK after the DB transition committed
  autoclaim  XAUTOCLAIM messages abandoned by dead consumers
  dispatch   outbox relay: DB rows that are due but not in the stream -> XADD (handles Redis outage/flush, retries)
  reaper     expired leases -> `verifying` (crashed worker recovery)
  leases     batched lease heartbeat for all in-flight work of this worker
  heartbeat  TTL'd worker registration in Redis (health endpoint)
  sampler    periodic gauge snapshots for the dashboard charts
"""
import asyncio
import contextlib
import logging
import math
import os
import socket
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import bind_context
from app.core.metrics import BREAKER_STATE, BREAKER_TRIPS, DISPATCHED, LEASES_REAPED, WORKER_INFLIGHT
from app.db.session import Database
from app.services import repository
from app.services.circuit_breaker import BreakerConfig, BreakerState, CircuitBreaker
from app.services.mock_client import MockPostingServiceClient
from app.services.queue import TransactionQueue
from app.services.worker_registry import WorkerRegistry
from app.workers.processor import Outcome, TransactionProcessor

logger = logging.getLogger(__name__)
_BREAKER_GAUGE = {BreakerState.CLOSED: 0, BreakerState.HALF_OPEN: 1, BreakerState.OPEN: 2}


class WorkerRuntime:
    def __init__(self, settings: Settings, db: Database, redis: Redis, client: MockPostingServiceClient, *,
                 worker_id: str | None = None, breaker: CircuitBreaker | None = None) -> None:
        self.s, self.db = settings, db
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:6]}"
        self.queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)
        self.registry = WorkerRegistry(redis, settings.worker_registry_prefix, settings.worker_registry_ttl_seconds)
        self.breaker = breaker or CircuitBreaker(
            BreakerConfig(settings.circuit_window_size, settings.circuit_min_calls, settings.circuit_failure_ratio,
                          settings.circuit_open_seconds, settings.circuit_half_open_max_calls),
            on_transition=self._on_breaker_transition,
        )
        self.processor = TransactionProcessor(db, client, self.breaker, settings, self.worker_id)
        self.started_at = datetime.now(timezone.utc)
        self.processed = 0
        self._inflight: set[asyncio.Task] = set()
        self._stopping = asyncio.Event()
        self._intake: list[asyncio.Task] = []
        self._background: list[asyncio.Task] = []
        self._group_ready = False
        self._last_prune = 0.0

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        bind_context(worker_id=self.worker_id)
        self._intake = [
            asyncio.create_task(self._consume_loop(), name="consume"),
            asyncio.create_task(self._periodic("autoclaim", self.s.autoclaim_interval_seconds, self._autoclaim_step), name="autoclaim"),
            asyncio.create_task(self._periodic("dispatch", self.s.reconcile_interval_seconds, self._dispatch_step), name="dispatch"),
        ]
        self._background = [
            asyncio.create_task(self._periodic("leases", self.s.lease_heartbeat_seconds, self._lease_step), name="leases"),
            asyncio.create_task(self._periodic("reaper", self.s.reaper_interval_seconds, self._reap_step), name="reaper"),
            asyncio.create_task(self._periodic("heartbeat", 3.0, self._heartbeat_step), name="heartbeat"),
            asyncio.create_task(self._periodic("sampler", float(self.s.sample_interval_seconds), self._sample_step), name="sampler"),
        ]
        logger.info("worker started", extra={"concurrency": self.s.worker_concurrency})

    async def stop(self) -> None:
        """Graceful shutdown: stop intake, drain in-flight work, then release. Anything unfinished is recovered by
        lease expiry -> `verifying` (never re-POSTed blindly)."""
        self._stopping.set()
        await self._cancel(self._intake)
        if self._inflight:
            logger.info("draining in-flight work", extra={"count": len(self._inflight)})
            _, pending = await asyncio.wait(self._inflight, timeout=self.s.shutdown_grace_seconds)
            if pending:
                logger.warning("grace period elapsed; abandoning in-flight work to lease recovery", extra={"count": len(pending)})
                await self._cancel(list(pending))
        await self._cancel(self._background)  # lease keeper ran until the end of draining
        with contextlib.suppress(Exception):
            await self.registry.remove(self.worker_id)
        logger.info("worker stopped", extra={"processed": self.processed})

    @staticmethod
    async def _cancel(tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ loops
    async def _periodic(self, name: str, interval: float, step: Callable[[], Awaitable[None]]) -> None:
        while not self._stopping.is_set() or name in ("leases", "heartbeat", "reaper", "sampler"):
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  (Redis/DB outages must never kill a loop)
                logger.warning("background step failed", extra={"loop": name, "error": type(exc).__name__, "detail": str(exc)[:200]})
            await asyncio.sleep(interval)

    async def _consume_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                if not self._group_ready:
                    await self.queue.ensure_group()
                    self._group_ready = True
                free = self.s.worker_concurrency - len(self._inflight)
                if free <= 0:
                    await asyncio.sleep(0.02)
                    continue
                for msg_id, tx_id in await self.queue.read(self.worker_id, count=min(free, 100), block_ms=1000):
                    self._spawn(msg_id, tx_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._group_ready = False  # e.g. Redis restarted/flushed: re-create the group
                logger.warning("consume failed; retrying", extra={"error": type(exc).__name__})
                await asyncio.sleep(1.0)

    async def _autoclaim_step(self) -> None:
        free = self.s.worker_concurrency - len(self._inflight)
        if free <= 0 or not self._group_ready:
            return
        for msg_id, tx_id in await self.queue.autoclaim(self.worker_id, min_idle_ms=self.s.autoclaim_min_idle_ms, count=min(free, 100)):
            self._spawn(msg_id, tx_id)

    async def _dispatch_step(self) -> None:
        for _ in range(20):  # bounded burst per tick
            async with self.db.transaction() as session:
                sent = await repository.dispatch_batch(session, self.queue.enqueue_many, batch_size=self.s.reconcile_batch_size, requeue_after=self.s.requeue_after_seconds)
            if sent:
                DISPATCHED.inc(sent)
            if sent < self.s.reconcile_batch_size:
                return

    async def _reap_step(self) -> None:
        async with self.db.transaction() as session:
            reaped = await repository.reap_expired_leases(session, self.s.verification_settle_seconds)
        for kind, ids in reaped.items():
            if ids:
                LEASES_REAPED.labels(kind).inc(len(ids))
                logger.warning("recovered expired leases", extra={"from_status": kind, "count": len(ids)})

    async def _lease_step(self) -> None:
        async with self.db.transaction() as session:
            await repository.renew_leases(session, self.worker_id, self.s.lease_ttl_seconds)

    async def _heartbeat_step(self) -> None:
        await self.registry.publish(self.worker_id, {
            "workerId": self.worker_id, "startedAt": self.started_at.isoformat(), "concurrency": self.s.worker_concurrency,
            "inflight": len(self._inflight), "processed": self.processed, "circuitBreaker": self.breaker.state.value,
            "draining": self._stopping.is_set(), "lastSeen": datetime.now(timezone.utc).isoformat(),
        })
        BREAKER_STATE.set(_BREAKER_GAUGE[self.breaker.state])

    async def _sample_step(self) -> None:
        interval = self.s.sample_interval_seconds
        bucket = datetime.fromtimestamp(math.floor(time.time() / interval) * interval, tz=timezone.utc)
        depth = await self.queue.depth()
        async with self.db.transaction() as session:
            await repository.record_sample(session, bucket, depth)
            if time.monotonic() - self._last_prune > 600:
                await repository.prune_samples(session, self.s.sample_retention_hours)
                self._last_prune = time.monotonic()

    # ------------------------------------------------------------------ message handling
    def _spawn(self, msg_id: str, tx_id: str) -> None:
        task = asyncio.create_task(self._handle(msg_id, tx_id))
        self._inflight.add(task)
        WORKER_INFLIGHT.set(len(self._inflight))
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        self._inflight.discard(task)
        WORKER_INFLIGHT.set(len(self._inflight))

    async def _handle(self, msg_id: str, tx_id: str) -> None:
        try:
            parsed = UUID(tx_id)
        except ValueError:
            logger.error("dropping malformed queue message", extra={"message_id": msg_id})
            await self._ack(msg_id)
            return
        outcome = await self.processor.process(parsed)
        self.processed += 1
        if outcome is not Outcome.ERROR:
            await self._ack(msg_id)  # ack only after the DB state is durable

    async def _ack(self, msg_id: str) -> None:
        try:
            await self.queue.ack(msg_id)
        except Exception as exc:  # noqa: BLE001  (unacked => redelivered => claim step makes it a harmless no-op)
            logger.warning("ack failed", extra={"error": type(exc).__name__})

    def _on_breaker_transition(self, old: BreakerState, new: BreakerState) -> None:
        logger.warning("circuit breaker state change", extra={"from_state": old.value, "to_state": new.value})
        BREAKER_STATE.set(_BREAKER_GAUGE[new])
        if new is BreakerState.OPEN:
            BREAKER_TRIPS.inc()
