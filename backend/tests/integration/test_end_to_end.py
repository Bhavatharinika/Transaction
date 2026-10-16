"""Full system: API -> PostgreSQL -> Redis Streams -> WorkerRuntime -> (fake) posting service."""
import asyncio

from sqlalchemy import func, select

from app.models.transaction import TransactionEvent
from tests.integration.conftest import fetch_row, tx_payload, wait_until


async def all_terminal(db, ids, status="completed"):
    return all((await fetch_row(db, i)).status == status for i in ids)


async def test_full_lifecycle_with_every_failure_mode(api, db, fake, make_runtime):
    await make_runtime("worker-1")
    await make_runtime("worker-2")  # two competing workers on one stream
    ok, post_write, pre_write, timeout = (tx_payload() for _ in range(4))
    fake.plan(post_write["id"], "fail_after_write")
    fake.plan(pre_write["id"], "fail_before_write", "ok")
    fake.plan(timeout["id"], "timeout_after_write")
    for p in (ok, post_write, pre_write, timeout):
        assert (await api.post("/api/transactions", json=p)).status_code == 202
    ids = [p["id"] for p in (ok, post_write, pre_write, timeout)]
    await wait_until(lambda: all_terminal(db, [__import__("uuid").UUID(i) for i in ids]), timeout=30, message="not all completed")
    assert not fake.duplicate_ids and all(fake.writes[i] == 1 for i in ids)
    assert [fake.post_calls[i] for i in ids] == [1, 1, 2, 1]
    api_view = (await api.get(f"/api/transactions/{pre_write['id']}")).json()
    assert api_view["status"] == "completed" and api_view["completedAt"] and api_view["error"] is None and api_view["retryCount"] == 1
    assert [e["type"] for e in api_view["events"]][-1] == "completed"
    stats = (await api.get("/api/stats")).json()
    assert stats["total"] == 4 and stats["completed"] == 4 and stats["failed"] == 0


async def test_burst_of_duplicates_results_in_single_posting(api, db, fake, make_runtime):
    await make_runtime("worker-1")
    p = tx_payload()
    results = await asyncio.gather(*(api.post("/api/transactions", json=p) for _ in range(10)))
    assert sorted(r.status_code for r in results) == [200] * 9 + [202]
    import uuid
    await wait_until(lambda: all_terminal(db, [uuid.UUID(p["id"])]), message="not completed")
    assert fake.post_calls[p["id"]] == 1 and fake.writes[p["id"]] == 1


async def test_worker_crash_after_write_is_recovered_by_another_worker(api, db, fake, make_runtime):
    import uuid
    p = tx_payload()
    fake.plan(p["id"], "write_then_hang")  # service wrote the row; the worker then dies waiting for the response
    worker_a = await make_runtime("worker-A", shutdown_grace_seconds=0.05)
    await api.post("/api/transactions", json=p)
    await wait_until(lambda: asyncio.sleep(0, result=fake.writes[p["id"]] == 1), message="write never happened")
    await worker_a.stop()  # abandons the in-flight POST; its lease stops being renewed
    assert (await fetch_row(db, uuid.UUID(p["id"]))).status == "processing"
    await make_runtime("worker-B")
    await wait_until(lambda: all_terminal(db, [uuid.UUID(p["id"])]), timeout=30, message="not recovered")
    assert fake.writes[p["id"]] == 1 and fake.post_calls[p["id"]] == 1  # recovered by verification, never re-POSTed


async def test_outage_trips_breaker_without_burning_retries_then_recovers(api, db, fake, make_runtime):
    import uuid
    await make_runtime("worker-1", circuit_min_calls=4, circuit_window_size=4, circuit_open_seconds=1.0, circuit_half_open_max_calls=2, retry_max_retries=5)
    fake.down = True
    payloads = [tx_payload() for _ in range(6)]
    for p in payloads:
        await api.post("/api/transactions", json=p)
    ids = [uuid.UUID(p["id"]) for p in payloads]

    async def deferred() -> bool:
        async with db.transaction() as s:
            n = (await s.execute(select(func.count()).select_from(TransactionEvent).where(TransactionEvent.event_type == "deferred"))).scalar_one()
        return n > 0

    await wait_until(deferred, timeout=15, message="circuit breaker never deferred work")
    assert all((await fetch_row(db, i)).status != "failed" for i in ids)  # open circuit never fails transactions
    fake.down = False
    await wait_until(lambda: all_terminal(db, ids), timeout=40, message="did not recover after outage")
    assert not fake.duplicate_ids
    assert max((await fetch_row(db, i)).retry_count for i in ids) <= 2


async def test_graceful_shutdown_drains_inflight_work(api, db, fake, make_runtime):
    import uuid
    fake.post_latency = 0.5
    worker = await make_runtime("worker-1", shutdown_grace_seconds=5)
    p = tx_payload()
    await api.post("/api/transactions", json=p)
    await wait_until(lambda: asyncio.sleep(0, result=fake.post_calls[p["id"]] == 1), message="POST never started")
    await worker.stop()
    assert (await fetch_row(db, uuid.UUID(p["id"]))).status == "completed"  # in-flight POST finished before exit
