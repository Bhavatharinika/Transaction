"""Durability when Redis is unavailable, and the dispatcher / consumer-group mechanics."""
import asyncio
import time

import httpx
from sqlalchemy import text

from app.core.config import Settings
from app.main import create_app
from app.services import repository
from app.services.queue import TransactionQueue
from tests.integration.conftest import TEST_REDIS, fetch_row, insert_row, make_settings, tx_payload


async def test_redis_outage_never_loses_or_slows_accepted_transactions(db, redis, settings):
    broken = make_settings(redis_url="redis://127.0.0.1:1/0")  # nothing listens here
    app = create_app(broken)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payloads = [tx_payload() for _ in range(5)]
            started = time.perf_counter()
            responses = [await client.post("/api/transactions", json=p) for p in payloads]
            elapsed = time.perf_counter() - started
            assert all(r.status_code == 202 for r in responses)
            assert elapsed < 2.0  # backoff prevents every request paying the connect timeout
            health = (await client.get("/api/health")).json()
            assert health["components"]["redis"]["status"] == "down" and health["status"] == "degraded"
    rows = [await fetch_row(db, p["id"]) for p in payloads]
    assert all(r.enqueued_at is None and r.status == "pending" for r in rows)  # durable, flagged for the dispatcher

    queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)  # Redis is "back"
    async with db.transaction() as session:
        sent = await repository.dispatch_batch(session, queue.enqueue_many, batch_size=100, requeue_after=60)
    assert sent == 5 and await queue.depth() == 5
    assert all((await fetch_row(db, p["id"])).enqueued_at is not None for p in payloads)


async def test_dispatcher_requeues_messages_lost_from_redis(db, redis, settings):
    row = await insert_row(db)
    async with db.transaction() as session:  # looks "queued" an hour ago, but the stream is empty (Redis was flushed)
        await session.execute(text("UPDATE transactions SET enqueued_at = now() - interval '1 hour' WHERE id = :i"), {"i": row.id})
    queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)
    async with db.transaction() as session:
        assert await repository.dispatch_batch(session, queue.enqueue_many, batch_size=10, requeue_after=20) == 1
    async with db.transaction() as session:  # recently queued rows are NOT queued again
        assert await repository.dispatch_batch(session, queue.enqueue_many, batch_size=10, requeue_after=20) == 0


async def test_dispatcher_skips_rows_not_yet_due(db, redis, settings):
    row = await insert_row(db)
    async with db.transaction() as session:
        await session.execute(text("UPDATE transactions SET next_attempt_at = now() + interval '1 hour' WHERE id = :i"), {"i": row.id})
    queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)
    async with db.transaction() as session:
        assert await repository.dispatch_batch(session, queue.enqueue_many, batch_size=10, requeue_after=20) == 0


async def test_concurrent_dispatchers_do_not_duplicate_messages(db, redis, settings):
    for _ in range(50):
        await insert_row(db)
    queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)

    async def dispatcher() -> None:
        while True:
            async with db.transaction() as session:
                if await repository.dispatch_batch(session, queue.enqueue_many, batch_size=7, requeue_after=60) == 0:
                    return

    await asyncio.gather(dispatcher(), dispatcher(), dispatcher())
    assert await queue.depth() == 50  # SKIP LOCKED: each row relayed exactly once


async def test_consumer_group_redelivers_unacked_messages(redis, settings):
    queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)
    await queue.ensure_group()
    await queue.ensure_group()  # idempotent
    await queue.enqueue("11111111-1111-1111-1111-111111111111")
    [(msg_id, tx_id)] = await queue.read("consumer-A", count=10, block_ms=100)  # A reads, then "crashes" (no ack)
    assert await queue.read("consumer-B", count=10, block_ms=50) == []  # not delivered twice normally
    await asyncio.sleep(0.12)
    [(claimed_id, claimed_tx)] = await queue.autoclaim("consumer-B", min_idle_ms=100, count=10)
    assert (claimed_id, claimed_tx) == (msg_id, tx_id)
    await queue.ack(claimed_id)
    assert await queue.depth() == 0 and await queue.pending_count() == 0
