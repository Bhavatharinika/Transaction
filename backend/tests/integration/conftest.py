"""Integration fixtures. Require PostgreSQL + Redis (TEST_DATABASE_URL / TEST_REDIS_URL); skipped otherwise.
Run them with:  docker compose --profile test run --rm tests"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import make_url, select, text

from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.models.transaction import Transaction
from app.services import repository
from app.services.circuit_breaker import BreakerConfig, CircuitBreaker
from app.services.mock_client import MockPostingServiceClient
from app.workers.processor import TransactionProcessor
from app.workers.runtime import WorkerRuntime
from tests.integration.fake_posting_service import FakePostingTransport

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://tps:tps@localhost:5432/tps_test")
TEST_REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
BACKEND_DIR = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items):
    for item in items:
        if "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def _infrastructure():
    async def prepare() -> None:
        url = make_url(TEST_DB)
        conn = await asyncpg.connect(user=url.username, password=url.password, host=url.host, port=url.port or 5432, database="postgres", timeout=3)
        try:
            if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database):
                await conn.execute(f'CREATE DATABASE "{url.database}"')
        finally:
            await conn.close()
        redis = Redis.from_url(TEST_REDIS)
        try:
            await redis.ping()
        finally:
            await redis.aclose()

    try:
        asyncio.run(prepare())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL/Redis not available for integration tests: {exc}")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, env={**os.environ, "DATABASE_URL": TEST_DB}, check=True)


def make_settings(**overrides) -> Settings:
    base = dict(
        database_url=TEST_DB, redis_url=TEST_REDIS, verification_settle_seconds=0.4, retry_max_retries=3, retry_initial_delay_seconds=0.05,
        retry_max_delay_seconds=0.2, retry_jitter_ratio=0.0, lease_ttl_seconds=1.0, lease_heartbeat_seconds=0.25, reconcile_interval_seconds=0.1,
        reconcile_batch_size=100, requeue_after_seconds=2.0, reaper_interval_seconds=0.2, autoclaim_interval_seconds=0.3, autoclaim_min_idle_ms=500,
        shutdown_grace_seconds=2.0, worker_concurrency=20, sample_interval_seconds=1, db_pool_size=10, db_max_overflow=10,
        load_shed_refresh_seconds=0.2, redis_down_backoff_seconds=1.0, log_level="WARNING", circuit_min_calls=1000,
    )
    return Settings(**{**base, **overrides})


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest_asyncio.fixture
async def db(settings):
    database = Database(settings.database_url, pool_size=10, max_overflow=10)
    async with database.transaction() as session:
        await session.execute(text("TRUNCATE transactions, transaction_events, metric_samples CASCADE"))
    yield database
    await database.dispose()


@pytest_asyncio.fixture
async def redis():
    client = Redis.from_url(TEST_REDIS, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def fake() -> FakePostingTransport:
    return FakePostingTransport()


@pytest_asyncio.fixture
async def posting_client(fake):
    http = httpx.AsyncClient(transport=fake, base_url="http://fake")
    yield MockPostingServiceClient("http://fake", http=http)
    await http.aclose()


@pytest.fixture
def make_processor(db, posting_client, settings):
    def factory(worker_id: str = "worker-1", breaker: CircuitBreaker | None = None, **overrides) -> TransactionProcessor:
        s = make_settings(**overrides) if overrides else settings
        return TransactionProcessor(db, posting_client, breaker or CircuitBreaker(BreakerConfig(min_calls=1000)), s, worker_id)
    return factory


@pytest_asyncio.fixture
async def api(settings, db, redis):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.app = app
            yield client


@pytest_asyncio.fixture
async def make_runtime(db, redis, posting_client):
    runtimes: list[WorkerRuntime] = []

    async def factory(worker_id: str | None = None, **overrides) -> WorkerRuntime:
        runtime = WorkerRuntime(make_settings(**overrides), db, redis, posting_client, worker_id=worker_id)
        await runtime.start()
        runtimes.append(runtime)
        return runtime

    yield factory
    for runtime in runtimes:
        await runtime.stop()


# ----------------------------------------------------------------------------- helpers

def tx_payload(**overrides) -> dict:
    return {"id": str(uuid4()), "amount": "100.50", "currency": "USD", "description": "integration test", "timestamp": "2025-01-15T10:30:00Z", **overrides}


async def insert_row(db: Database, **overrides) -> Transaction:
    from datetime import datetime
    from decimal import Decimal
    data = {"tx_id": uuid4(), "amount": Decimal("100.50"), "currency": "USD", "description": "row", "timestamp": datetime.fromisoformat("2025-01-15T10:30:00+00:00"),
            "metadata": {}, "payload_hash": "h" * 64, **overrides}
    async with db.transaction() as session:
        await repository.insert_if_absent(session, **data)
        await repository.clear_enqueued(session, data["tx_id"])
    return await fetch_row(db, data["tx_id"])


async def fetch_row(db: Database, tx_id) -> Transaction:
    async with db.transaction() as session:
        return (await session.execute(select(Transaction).where(Transaction.id == tx_id))).scalar_one()


async def events_of(db: Database, tx_id) -> list[str]:
    async with db.transaction() as session:
        return [e.event_type for e in await repository.get_events(session, tx_id)]


async def drive(processor: TransactionProcessor, tx_id, timeout: float = 20.0) -> Transaction:
    """Redeliver the transaction to `processor` (as the queue/dispatcher would) until it is terminal."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        await processor.process(tx_id)
        row = await fetch_row(processor.db, tx_id)
        if row.status in ("completed", "failed"):
            return row
        if loop.time() > deadline:
            raise AssertionError(f"transaction stuck in {row.status}")
        await asyncio.sleep(0.05)


async def wait_until(predicate, timeout: float = 20.0, interval: float = 0.05, message: str = "condition not met"):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        result = await predicate()
        if result:
            return result
        if loop.time() > deadline:
            raise AssertionError(message)
        await asyncio.sleep(interval)
