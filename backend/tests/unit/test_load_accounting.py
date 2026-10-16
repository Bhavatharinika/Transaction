"""Unit test for load_test accounting and invariants."""
import pytest
import httpx
from load_test import wait_for_completion, query_test_transactions_status


class DummyArgs:
    concurrency = 10
    wait_timeout = 5


@pytest.mark.asyncio
async def test_load_test_exact_uuid_accounting_and_invariants():
    test_ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(5)]
    
    # 2 completed, 1 failed, 1 processing, 1 pending
    status_map = {
        test_ids[0]: "completed",
        test_ids[1]: "completed",
        test_ids[2]: "failed",
        test_ids[3]: "processing",
        test_ids[4]: "pending",
    }

    def handler(request: httpx.Request):
        tx_id = request.url.path.rsplit("/", 1)[-1]
        st = status_map.get(tx_id, "pending")
        return httpx.Response(200, json={"status": st, "transactionId": tx_id})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        res = await wait_for_completion(client, DummyArgs(), 0.0, [], test_ids)
        assert res["submitted"] == 5
        assert res["completed"] == 2
        assert res["failed"] == 1
        assert res["unfinished"] == 2
        assert res["completed"] + res["failed"] + res["unfinished"] == res["submitted"]
        assert res["invariantHolds"] is True
        assert res["completed"] <= res["submitted"]
        assert res["failed"] <= res["submitted"]
        assert res["unfinished"] <= res["submitted"]


@pytest.mark.asyncio
async def test_regression_old_stats_baseline_bug_simulation():
    """Section 23 Required Regression Test:
    Simulate 1000 unique transaction IDs.
    300 have already completed before polling begins.
    700 complete afterward (in this simulation, all 1000 are completed).
    The benchmark MUST report:
      unique = 1000
      completed = 1000
      failed = 0
      unfinished = 0
    The old broken implementation using stats delta (current - baseline) would report 700.
    With exact UUID tracking, it correctly reports 1000.
    """
    total_tx = 1000
    test_ids = [f"10000000-0000-0000-0000-{i:012d}" for i in range(total_tx)]
    
    # All 1000 are in completed status (300 before + 700 during/after)
    def handler(request: httpx.Request):
        tx_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"status": "completed", "transactionId": tx_id})

    class FastArgs:
        concurrency = 50
        wait_timeout = 5

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        res = await wait_for_completion(client, FastArgs(), 0.0, [], test_ids)
        assert res["submitted"] == 1000
        assert res["completed"] == 1000
        assert res["failed"] == 0
        assert res["unfinished"] == 0
        assert res["completed"] + res["failed"] + res["unfinished"] == 1000
        assert res["invariantHolds"] is True
        assert res["completed"] <= 1000
        assert res["failed"] <= 1000
        assert res["unfinished"] <= 1000


@pytest.mark.asyncio
async def test_partial_completion_and_unfinished_accounting():
    """Section 23 Required Test:
    950 completed, 30 failed, 20 unfinished.
    Expected:
      completed = 950
      failed = 30
      unfinished = 20
      950 + 30 + 20 = 1000
    """
    total_tx = 1000
    test_ids = [f"20000000-0000-0000-0000-{i:012d}" for i in range(total_tx)]
    
    # 950 completed, 30 failed, 15 processing, 5 pending (total unfinished = 20)
    status_map = {}
    for i in range(950):
        status_map[test_ids[i]] = "completed"
    for i in range(950, 980):
        status_map[test_ids[i]] = "failed"
    for i in range(980, 995):
        status_map[test_ids[i]] = "processing"
    for i in range(995, 1000):
        status_map[test_ids[i]] = "pending"

    def handler(request: httpx.Request):
        tx_id = request.url.path.rsplit("/", 1)[-1]
        st = status_map.get(tx_id, "pending")
        return httpx.Response(200, json={"status": st, "transactionId": tx_id})

    class TimeoutArgs:
        concurrency = 50
        wait_timeout = 0.01  # Immediate return without endless loop

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        res = await wait_for_completion(client, TimeoutArgs(), 0.0, [], test_ids)
        assert res["submitted"] == 1000
        assert res["completed"] == 950
        assert res["failed"] == 30
        assert res["unfinished"] == 20
        assert res["completed"] + res["failed"] + res["unfinished"] == 1000
        assert res["invariantHolds"] is True
        assert res["completed"] <= 1000
        assert res["failed"] <= 1000
        assert res["unfinished"] <= 1000

