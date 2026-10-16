"""Redis Streams queue: consumer group, at-least-once delivery.

Redis is a DELIVERY mechanism, not the source of truth: PostgreSQL holds every accepted transaction and the
dispatcher re-queues anything that is missing from the stream. Messages are XACKed (and XDELed, so XLEN equals
backlog + in-flight) only after the worker has finished with them.
"""
import asyncio
import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.core.metrics import ENQUEUE_FAILURES

logger = logging.getLogger(__name__)
FIELD = "tx"


class TransactionQueue:
    def __init__(self, redis: Redis, stream: str, group: str) -> None:
        self.redis = redis
        self.stream = stream
        self.group = group

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, tx_id: str) -> str:
        return await self.redis.xadd(self.stream, {FIELD: tx_id})

    async def enqueue_many(self, tx_ids: list[str]) -> None:
        async with self.redis.pipeline(transaction=False) as pipe:
            for tx_id in tx_ids:
                pipe.xadd(self.stream, {FIELD: tx_id})
            await pipe.execute()

    async def read(self, consumer: str, *, count: int, block_ms: int) -> list[tuple[str, str]]:
        response = await self.redis.xreadgroup(self.group, consumer, {self.stream: ">"}, count=count, block=block_ms)
        return self._flatten(response)

    async def autoclaim(self, consumer: str, *, min_idle_ms: int, count: int) -> list[tuple[str, str]]:
        """Take over messages whose consumer died (or stalled) without acknowledging them."""
        result = await self.redis.xautoclaim(self.stream, self.group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count)
        entries = result[1] if len(result) > 1 else []
        return [(msg_id, fields[FIELD]) for msg_id, fields in entries if fields and FIELD in fields]

    async def ack(self, msg_id: str) -> None:
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xack(self.stream, self.group, msg_id)
            pipe.xdel(self.stream, msg_id)
            await pipe.execute()

    async def depth(self) -> int:
        return await self.redis.xlen(self.stream)

    async def pending_count(self) -> int:
        summary = await self.redis.xpending(self.stream, self.group)
        return int(summary["pending"]) if summary else 0

    @staticmethod
    def _flatten(response) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        for _stream, entries in response or []:
            messages.extend((msg_id, fields[FIELD]) for msg_id, fields in entries if fields and FIELD in fields)
        return messages


class SafeEnqueuer:
    """API-side publish that never fails the request: if Redis is down the row is already durable in PostgreSQL
    and the dispatcher will deliver it later. After a failure we skip Redis for a short backoff so a Redis outage
    cannot add latency to every request."""

    def __init__(self, queue: TransactionQueue, backoff_seconds: float) -> None:
        self._queue = queue
        self._backoff = backoff_seconds
        self._down_until = 0.0

    async def try_enqueue(self, tx_id: str) -> bool:
        if time.monotonic() < self._down_until:
            return False
        try:
            await self._queue.enqueue(tx_id)
            return True
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            self._down_until = time.monotonic() + self._backoff
            ENQUEUE_FAILURES.inc()
            logger.warning("redis enqueue failed; relying on dispatcher", extra={"transaction_id": tx_id, "error": type(exc).__name__})
            return False
