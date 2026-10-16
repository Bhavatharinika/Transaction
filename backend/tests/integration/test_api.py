import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.transaction import Transaction
from tests.integration.conftest import tx_payload


async def count_rows(db) -> int:
    async with db.transaction() as s:
        return (await s.execute(select(func.count()).select_from(Transaction))).scalar_one()


async def test_accepts_with_202_and_responds_fast(api):
    await api.post("/api/transactions", json=tx_payload())  # warm connection pools
    p = tx_payload()
    r = await api.post("/api/transactions", json=p)
    assert r.status_code == 202
    body = r.json()
    assert body["transactionId"] == p["id"] and body["status"] == "pending" and body["submittedAt"]
    assert float(r.headers["x-response-time-ms"]) < 100  # assignment SLO on the submission path


async def test_status_endpoint_shape(api):
    p = tx_payload(metadata={"k": "v"})
    await api.post("/api/transactions", json=p)
    body = (await api.get(f"/api/transactions/{p['id']}")).json()
    assert body["status"] == "pending" and body["completedAt"] is None and body["error"] is None
    assert Decimal(body["amount"]) == Decimal("100.50") and body["currency"] == "USD" and body["metadata"] == {"k": "v"} and body["retryCount"] == 0


async def test_unknown_transaction_404_uses_error_envelope(api):
    r = await api.get(f"/api/transactions/{uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TRANSACTION_NOT_FOUND" and r.json()["error"]["requestId"]


@pytest.mark.parametrize("override", [{"amount": 0}, {"amount": -1}, {"currency": "ZZZ"}, {"id": "nope"}, {"timestamp": "bad"}])
async def test_validation_errors(api, override):
    r = await api.post("/api/transactions", json=tx_payload(**override))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["requestId"] == r.headers["x-request-id"] and err["details"]


async def test_invalid_path_uuid_is_422(api):
    assert (await api.get("/api/transactions/not-a-uuid")).status_code == 422


async def test_request_id_is_propagated(api):
    r = await api.get("/api/stats", headers={"X-Request-ID": "trace-123"})
    assert r.headers["x-request-id"] == "trace-123"


async def test_duplicate_same_payload_is_idempotent(api, db, redis, settings):
    p = tx_payload()
    first = await api.post("/api/transactions", json=p)
    second = await api.post("/api/transactions", json=p)
    assert (first.status_code, second.status_code) == (202, 200)
    assert second.headers["idempotent-replay"] == "true" and second.json()["transactionId"] == p["id"]
    assert await count_rows(db) == 1
    assert await redis.xlen(settings.stream_name) == 1  # enqueued once


async def test_equivalent_numeric_representation_is_still_a_duplicate(api, db):
    p = tx_payload(amount="100.5")
    await api.post("/api/transactions", json=p)
    assert (await api.post("/api/transactions", json={**p, "amount": "100.5000"})).status_code == 200


async def test_same_id_different_payload_conflicts(api, db):
    p = tx_payload()
    await api.post("/api/transactions", json=p)
    r = await api.post("/api/transactions", json={**p, "amount": "999.00"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await count_rows(db) == 1


async def test_concurrent_identical_submissions_create_one_row(api, db, redis, settings):
    p = tx_payload()
    results = await asyncio.gather(*(api.post("/api/transactions", json=p) for _ in range(25)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200] * 24 + [202]
    assert await count_rows(db) == 1 and await redis.xlen(settings.stream_name) == 1


async def test_list_filter_search_pagination(api):
    for i in range(5):
        await api.post("/api/transactions", json=tx_payload(description="coffee" if i == 0 else f"item {i}"))
    page = (await api.get("/api/transactions", params={"limit": 2})).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    assert (await api.get("/api/transactions", params={"q": "coffee"})).json()["total"] == 1
    assert (await api.get("/api/transactions", params={"status": "pending"})).json()["total"] == 5
    assert (await api.get("/api/transactions", params={"status": "processing"})).json()["total"] == 0
    assert (await api.get("/api/transactions", params={"status": "bogus"})).status_code == 422


async def test_health_reports_components_and_queue(api):
    r = await api.get("/api/health")
    body = r.json()
    assert r.status_code == 200 and body["components"]["database"]["status"] == "up" and body["components"]["redis"]["status"] == "up"
    assert isinstance(body["queue"]["depth"], int) and body["workers"]["alive"] == 0 and body["status"] == "degraded"  # no worker running


async def test_backpressure_returns_503_with_retry_after_and_keeps_accepted_work(api):
    accepted = tx_payload()
    await api.post("/api/transactions", json=accepted)
    shedder = api.app.state.transactions._shedder

    async def freeze() -> None:
        return None

    shedder.refresh = freeze
    shedder.queue_depth = shedder._s.queue_max_depth
    r = await api.post("/api/transactions", json=tx_payload())
    assert r.status_code == 503 and r.headers["retry-after"] and r.json()["error"]["code"] == "OVERLOADED"
    assert (await api.get(f"/api/transactions/{accepted['id']}")).status_code == 200  # already-accepted work untouched
    shedder.queue_depth = 0
    assert (await api.post("/api/transactions", json=tx_payload())).status_code == 202


async def test_metrics_endpoint_is_prometheus_text(api):
    await api.get("/api/stats")
    text = (await api.get("/metrics")).text
    assert "tps_http_requests_total" in text and "tps_transactions_accepted_total" in text
