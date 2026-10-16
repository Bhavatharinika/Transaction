"""Backpressure: refuse NEW work (HTTP 503 + Retry-After) when the system is saturated.

Limits are checked against cheap cached gauges refreshed in the background, so the hot path does no extra I/O.
Accepted transactions are never dropped; only new submissions are turned away.
"""
import asyncio
import logging

from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.metrics import OPEN_TRANSACTIONS, QUEUE_DEPTH
from app.db.session import Database
from app.services import repository
from app.services.queue import TransactionQueue

logger = logging.getLogger(__name__)


class LoadShedder:
    def __init__(self, db: Database, queue: TransactionQueue, settings: Settings) -> None:
        self._db, self._queue, self._s = db, queue, settings
        self.queue_depth: int | None = None
        self.open_transactions: int | None = None
        self._accepted_since_refresh = 0

    def note_accepted(self) -> None:
        self._accepted_since_refresh += 1

    def check(self) -> None:
        s, extra = self._s, self._accepted_since_refresh
        depth_over = self.queue_depth is not None and self.queue_depth + extra >= s.queue_max_depth
        open_over = self.open_transactions is not None and self.open_transactions + extra >= s.max_open_transactions
        if depth_over or open_over:
            raise AppError(
                503, "OVERLOADED", "System is at capacity; retry after the indicated delay. Previously accepted transactions are safe.",
                headers={"Retry-After": str(s.retry_after_seconds)},
            )

    async def refresh(self) -> None:
        try:
            self.queue_depth = await self._queue.depth()
            QUEUE_DEPTH.set(self.queue_depth)
        except (RedisError, OSError, asyncio.TimeoutError):
            self.queue_depth = None  # unknown: don't shed on missing data; DB row count still bounds growth
        try:
            async with self._db.transaction() as session:
                self.open_transactions = await repository.open_count(session)
            OPEN_TRANSACTIONS.set(self.open_transactions)
        except Exception as exc:  # noqa: BLE001
            logger.warning("load shedder refresh failed", extra={"error": type(exc).__name__})
        self._accepted_since_refresh = 0

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self._s.load_shed_refresh_seconds)
