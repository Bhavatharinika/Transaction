"""Race conditions: concurrent claims, double processing, crashed workers, stale writers."""
import asyncio

from sqlalchemy import text

from app.services import repository
from app.services.state_machine import TxStatus
from app.workers.processor import Outcome
from tests.integration.conftest import drive, fetch_row, insert_row


async def test_claim_is_exclusive_among_many_contenders(db):
    row = await insert_row(db)

    async def contender(i: int):
        async with db.transaction() as session:
            return await repository.claim(session, row.id, f"worker-{i}", 5.0)

    winners = [r for r in await asyncio.gather(*(contender(i) for i in range(25))) if r is not None]
    assert len(winners) == 1
    claimed = await fetch_row(db, row.id)
    assert claimed.status == "processing" and claimed.lease_owner == winners[0].lease_owner and claimed.version == 2


async def test_three_workers_same_transaction_post_exactly_once(db, fake, make_processor):
    fake.post_latency = 0.3  # wide race window
    row = await insert_row(db)
    outcomes = await asyncio.gather(*(make_processor(f"w{i}").process(row.id) for i in range(3)))
    assert sorted(o.value for o in outcomes) == ["completed", "skipped", "skipped"]
    assert fake.post_calls[str(row.id)] == 1 and fake.writes[str(row.id)] == 1


async def test_many_transactions_many_workers_no_duplicates(db, fake, make_processor):
    fake.post_latency = 0.02
    rows = [await insert_row(db) for _ in range(30)]
    workers = [make_processor(f"w{i}") for i in range(4)]
    # every worker is handed every transaction (worst-case at-least-once redelivery)
    await asyncio.gather(*(drive(w, r.id) for r in rows for w in workers))
    assert not fake.duplicate_ids and all(fake.writes[str(r.id)] == 1 for r in rows)
    assert all((await fetch_row(db, r.id)).status == "completed" for r in rows)


async def test_live_lease_is_not_reaped(db):
    row = await insert_row(db)
    async with db.transaction() as session:
        await repository.claim(session, row.id, "w1", 30.0)
        reaped = await repository.reap_expired_leases(session, 0.1)
    assert reaped == {"processing": [], "verifying": []}
    assert (await fetch_row(db, row.id)).status == "processing"


async def _expire_lease(db, tx_id):
    async with db.transaction() as session:
        await session.execute(text("UPDATE transactions SET lease_expires_at = now() - interval '1 second' WHERE id = :i"), {"i": tx_id})


async def test_crash_after_write_recovers_via_verification_without_reposting(db, fake, make_processor):
    row = await insert_row(db)
    async with db.transaction() as session:  # worker A claims, POSTs successfully, then dies before recording it
        await repository.claim(session, row.id, "worker-A", 5.0)
    fake.preload(row.id)
    await _expire_lease(db, row.id)
    async with db.transaction() as session:
        reaped = await repository.reap_expired_leases(session, 0.2)
    assert reaped["processing"] == [row.id]
    recovering = await fetch_row(db, row.id)
    assert recovering.status == "verifying" and recovering.last_error["code"] == "WORKER_LOST"
    done = await drive(make_processor("worker-B"), row.id)
    assert done.status == "completed" and fake.post_calls[str(row.id)] == 0  # B only verified, never POSTed


async def test_crash_before_write_recovers_and_posts_once(db, fake, make_processor):
    row = await insert_row(db)
    async with db.transaction() as session:
        await repository.claim(session, row.id, "worker-A", 5.0)
    await _expire_lease(db, row.id)
    async with db.transaction() as session:
        await repository.reap_expired_leases(session, 0.2)
    done = await drive(make_processor("worker-B"), row.id)
    assert done.status == "completed" and fake.post_calls[str(row.id)] == 1 and fake.writes[str(row.id)] == 1


async def test_stale_worker_cannot_overwrite_newer_state(db, fake, make_processor):
    row = await insert_row(db)
    async with db.transaction() as session:
        stale = await repository.claim(session, row.id, "worker-A", 5.0)  # A holds version 2
    await _expire_lease(db, row.id)
    async with db.transaction() as session:
        await repository.reap_expired_leases(session, 0.1)
    fake.preload(row.id)
    final = await drive(make_processor("worker-B"), row.id)
    assert final.status == "completed"
    async with db.transaction() as session:  # A wakes up and tries to record its own (now obsolete) outcome
        applied = await repository.transition(session, stale, "worker-A", TxStatus.FAILED, event="failed")
    assert applied is False
    after = await fetch_row(db, row.id)
    assert after.status == "completed" and after.version == final.version


async def test_lease_heartbeat_extends_all_owned_leases(db):
    rows = [await insert_row(db) for _ in range(3)]
    async with db.transaction() as session:
        for r in rows:
            await repository.claim(session, r.id, "w1", 1.0)
        await repository.claim(session, (await insert_row(db)).id, "other", 1.0)
    async with db.transaction() as session:
        assert await repository.renew_leases(session, "w1", 60.0) == 3
