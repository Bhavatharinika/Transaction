"""Optional: exercises the REAL provided mock container (slow: 2-10s latency, ~5% failures).
  docker compose up -d mock-posting-service && MOCK_SERVICE_URL=http://localhost:8080 pytest -m mock_service
Uses POST /cleanup before and after, as the assignment requires."""
import os
import uuid

import httpx
import pytest
import pytest_asyncio

from app.services.mock_client import MockPostingServiceClient
from tests.integration.conftest import fetch_row, tx_payload, wait_until

pytestmark = pytest.mark.mock_service
MOCK_URL = os.environ.get("MOCK_SERVICE_URL")


@pytest_asyncio.fixture
async def live_client():
    if not MOCK_URL:
        pytest.skip("MOCK_SERVICE_URL not set")
    client = MockPostingServiceClient(MOCK_URL)
    if not await client.ping():
        await client.aclose()
        pytest.skip("mock posting service unreachable")
    await client.cleanup()
    yield client
    await client.cleanup()
    await client.aclose()


def _ids_in(body) -> list[str]:
    items = body if isinstance(body, list) else next((v for v in body.values() if isinstance(v, list)), [])
    return [str(i.get("id")) for i in items if isinstance(i, dict)]


async def test_real_mock_end_to_end_no_duplicates(api, db, redis, live_client):
    from tests.integration.conftest import make_settings
    from app.workers.runtime import WorkerRuntime
    settings = make_settings(verification_settle_seconds=12, mock_post_timeout_seconds=15, retry_initial_delay_seconds=1, retry_max_retries=5, shutdown_grace_seconds=20)
    runtime = WorkerRuntime(settings, db, redis, live_client, worker_id="live-1")
    await runtime.start()
    try:
        payloads = [tx_payload() for _ in range(8)]
        for p in payloads:
            assert (await api.post("/api/transactions", json=p)).status_code == 202
        ids = [uuid.UUID(p["id"]) for p in payloads]

        async def done() -> bool:
            return all((await fetch_row(db, i)).status in ("completed", "failed") for i in ids)

        await wait_until(done, timeout=240, interval=1, message="transactions did not finish")
        async with httpx.AsyncClient(base_url=MOCK_URL) as http:
            listing = _ids_in((await http.get("/transactions", params={"limit": 100})).json())
        for p, i in zip(payloads, ids):
            row = await fetch_row(db, i)
            assert listing.count(p["id"]) <= 1, "duplicate record in posting service"
            assert (row.status == "completed") == (listing.count(p["id"]) == 1)
    finally:
        await runtime.stop()
