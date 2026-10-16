"""All SQL lives here. Every state change is a compare-and-swap UPDATE; time comparisons use the DB clock
so worker clock skew cannot corrupt leases or schedules."""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Sequence
from uuid import UUID

from sqlalchemy import String, case, cast, delete, func, insert, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import MetricSample, Transaction, TransactionEvent
from app.services.state_machine import DISPATCHABLE_STATUSES, OPEN_STATUSES, TxStatus, assert_transition, internal_statuses_for

NO_SYNC = {"synchronize_session": False}


def db_in(seconds: float):
    """SQL expression: DB-clock now() + seconds."""
    return func.now() + timedelta(seconds=seconds)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ----------------------------------------------------------------------------- API side

async def insert_if_absent(
    session: AsyncSession, *, tx_id: UUID, amount, currency: str, description: str, timestamp: datetime,
    metadata: dict[str, Any] | None, payload_hash: str,
) -> datetime | None:
    """Atomic idempotent insert. Returns submitted_at if THIS call created the row, None if it already existed.

    Concurrent duplicate submissions serialise on the primary-key unique index: exactly one wins.
    The row is born with enqueued_at=now() (optimistic); if the Redis XADD then fails the API clears it so the
    dispatcher picks the row up (transactional-outbox pattern).
    """
    table = Transaction.__table__
    stmt = (
        pg_insert(table)
        .values({
            "id": tx_id, "amount": amount, "currency": currency, "description": description, "timestamp": timestamp,
            "metadata": metadata or {}, "payload_hash": payload_hash, "enqueued_at": func.now(),
        })
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(table.c.submitted_at)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def clear_enqueued(session: AsyncSession, tx_id: UUID) -> None:
    await session.execute(update(Transaction).where(Transaction.id == tx_id).values(enqueued_at=None).execution_options(**NO_SYNC))


async def get_transaction(session: AsyncSession, tx_id: UUID) -> Transaction | None:
    return await session.get(Transaction, tx_id)


async def get_events(session: AsyncSession, tx_id: UUID, limit: int = 200) -> Sequence[TransactionEvent]:
    stmt = select(TransactionEvent).where(TransactionEvent.transaction_id == tx_id).order_by(TransactionEvent.id).limit(limit)
    return (await session.execute(stmt)).scalars().all()


def _escape_like(text_: str) -> str:
    return text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


SORTABLE = {
    "submittedAt": Transaction.submitted_at, "completedAt": Transaction.completed_at, "amount": Transaction.amount,
    "retryCount": Transaction.retry_count, "status": Transaction.status,
}


async def list_transactions(
    session: AsyncSession, *, status: str | None, q: str | None, limit: int, offset: int, sort: str, descending: bool,
) -> tuple[Sequence[Transaction], int]:
    conditions = []
    if status:
        conditions.append(Transaction.status.in_(internal_statuses_for(status)))
    if q:
        term = _escape_like(q.strip())
        conditions.append(or_(
            cast(Transaction.id, String).ilike(f"{term}%", escape="\\"),
            Transaction.description.ilike(f"%{term}%", escape="\\"),
            Transaction.currency == q.strip().upper(),
        ))
    column = SORTABLE.get(sort, Transaction.submitted_at)
    order = (column.desc().nulls_last() if descending else column.asc().nulls_last(), Transaction.id)
    rows = (await session.execute(select(Transaction).where(*conditions).order_by(*order).limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(select(func.count()).select_from(Transaction).where(*conditions))).scalar_one()
    return rows, total


# ----------------------------------------------------------------------------- worker side

async def claim(session: AsyncSession, tx_id: UUID, worker_id: str, lease_ttl: float) -> Transaction | None:
    """Atomically take exclusive ownership. Exactly one concurrent caller gets a row back.

    pending   -> processing (fresh attempt: GET, then POST if absent)
    verifying -> stays verifying (verification pass: GET only, never POST)
    The caller infers the phase from the returned status. READ COMMITTED re-evaluates the WHERE clause after a
    competing UPDATE commits, so the loser sees a live lease and matches zero rows.
    """
    stmt = (
        update(Transaction)
        .where(
            Transaction.id == tx_id,
            Transaction.status.in_(DISPATCHABLE_STATUSES),
            Transaction.next_attempt_at <= func.now(),
            or_(Transaction.lease_expires_at.is_(None), Transaction.lease_expires_at < func.now()),
        )
        .values(
            status=case((Transaction.status == TxStatus.PENDING.value, TxStatus.PROCESSING.value), else_=Transaction.status),
            lease_owner=worker_id,
            lease_expires_at=db_in(lease_ttl),
            version=Transaction.version + 1,
            processing_started_at=func.coalesce(Transaction.processing_started_at, func.now()),
            updated_at=func.now(),
        )
        .returning(Transaction)
        .execution_options(**NO_SYNC)
    )
    return (await session.execute(stmt)).scalars().first()


async def renew_leases(session: AsyncSession, worker_id: str, lease_ttl: float) -> int:
    """One statement extends every lease this worker holds (cheaper than a heartbeat per transaction)."""
    stmt = (
        update(Transaction)
        .where(Transaction.lease_owner == worker_id, Transaction.status.in_((TxStatus.PROCESSING.value, TxStatus.VERIFYING.value)))
        .values(lease_expires_at=db_in(lease_ttl))
        .execution_options(**NO_SYNC)
    )
    return (await session.execute(stmt)).rowcount


async def add_event(session: AsyncSession, tx_id: UUID, event_type: str, details: dict[str, Any] | None = None) -> None:
    await session.execute(insert(TransactionEvent).values(transaction_id=tx_id, event_type=event_type, details=details or {}))


async def transition(
    session: AsyncSession, tx: Transaction, worker_id: str, target: TxStatus, *, event: str,
    event_details: dict[str, Any] | None = None, extra_events: Sequence[tuple[str, dict[str, Any]]] = (), **values: Any,
) -> bool:
    """Validated compare-and-swap transition. Returns False if this worker no longer owns the row (stale worker).

    The WHERE clause pins lease_owner, status AND version, so a worker whose lease was reaped and re-claimed can
    never overwrite newer state (lost-update protection). The lease is always released by a transition.
    """
    assert_transition(TxStatus(tx.status), target)
    stmt = (
        update(Transaction)
        .where(Transaction.id == tx.id, Transaction.lease_owner == worker_id, Transaction.status == tx.status, Transaction.version == tx.version)
        .values(status=target.value, version=Transaction.version + 1, updated_at=func.now(), lease_owner=None, lease_expires_at=None, **values)
        .execution_options(**NO_SYNC)
    )
    if (await session.execute(stmt)).rowcount != 1:
        return False
    for extra_type, extra_details in extra_events:
        await add_event(session, tx.id, extra_type, extra_details)
    await add_event(session, tx.id, event, event_details)
    return True


async def reap_expired_leases(session: AsyncSession, settle_seconds: float) -> dict[str, list[UUID]]:
    """Recover work from crashed/stalled workers.

    A `processing` row may already have had its POST written, so it is NEVER re-POSTed: it moves to `verifying`
    and is checked with GET after the settle window. An expired `verifying` lease is simply released.
    """
    error = {"code": "WORKER_LOST", "message": "Worker lease expired mid-processing; verifying posting-service state", "at": utc_iso()}
    processing = (await session.execute(
        update(Transaction)
        .where(Transaction.status == TxStatus.PROCESSING.value, Transaction.lease_expires_at < func.now())
        .values(status=TxStatus.VERIFYING.value, next_attempt_at=db_in(settle_seconds), lease_owner=None, lease_expires_at=None,
                enqueued_at=None, version=Transaction.version + 1, updated_at=func.now(), last_error=error)
        .returning(Transaction.id).execution_options(**NO_SYNC)
    )).scalars().all()
    verifying = (await session.execute(
        update(Transaction)
        .where(Transaction.status == TxStatus.VERIFYING.value, Transaction.lease_owner.is_not(None), Transaction.lease_expires_at < func.now())
        .values(lease_owner=None, lease_expires_at=None, enqueued_at=None, version=Transaction.version + 1, updated_at=func.now())
        .returning(Transaction.id).execution_options(**NO_SYNC)
    )).scalars().all()
    events = [{"transaction_id": i, "event_type": "lease_expired", "details": {"from": "processing"}} for i in processing]
    events += [{"transaction_id": i, "event_type": "lease_expired", "details": {"from": "verifying"}} for i in verifying]
    if events:
        await session.execute(insert(TransactionEvent), events)
    return {"processing": list(processing), "verifying": list(verifying)}


async def dispatch_batch(session: AsyncSession, enqueue_many: Callable[[list[str]], Awaitable[None]], *, batch_size: int, requeue_after: float) -> int:
    """Outbox relay: push due, un-queued (or long-since-queued) rows to the stream.

    FOR UPDATE SKIP LOCKED lets any number of workers run this concurrently without duplicating work. If the XADD
    fails the surrounding DB transaction rolls back and the rows are retried next tick. Duplicate stream messages
    (e.g. XADD ok, commit failed) are harmless: the claim step is the single point of mutual exclusion.
    """
    stmt = (
        select(Transaction.id)
        .where(
            Transaction.status.in_(DISPATCHABLE_STATUSES),
            Transaction.next_attempt_at <= func.now(),
            or_(Transaction.enqueued_at.is_(None), Transaction.enqueued_at < func.now() - timedelta(seconds=requeue_after)),
            or_(Transaction.lease_expires_at.is_(None), Transaction.lease_expires_at < func.now()),
        )
        .order_by(Transaction.next_attempt_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    ids = list((await session.execute(stmt)).scalars().all())
    if not ids:
        return 0
    await enqueue_many([str(i) for i in ids])
    await session.execute(update(Transaction).where(Transaction.id.in_(ids)).values(enqueued_at=func.now()).execution_options(**NO_SYNC))
    return len(ids)


# ----------------------------------------------------------------------------- stats

async def status_counts(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(select(Transaction.status, func.count()).group_by(Transaction.status))).all()
    return {status: count for status, count in rows}


async def open_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(Transaction).where(Transaction.status.in_(OPEN_STATUSES)))).scalar_one()


async def recent_timings(session: AsyncSession, window_seconds: int) -> dict[str, float | int | None]:
    since = func.now() - timedelta(seconds=window_seconds)
    stmt = select(
        func.count(),
        func.avg(func.extract("epoch", Transaction.completed_at - Transaction.processing_started_at)),
        func.avg(func.extract("epoch", Transaction.completed_at - Transaction.submitted_at)),
    ).where(Transaction.status == "completed", Transaction.completed_at >= since)
    count, processing, end_to_end = (await session.execute(stmt)).one()
    return {"completed": count, "avgProcessingSeconds": float(processing) if processing is not None else None,
            "avgEndToEndSeconds": float(end_to_end) if end_to_end is not None else None}


async def recent_outcomes(session: AsyncSession, window_seconds: int) -> dict[str, int]:
    since = func.now() - timedelta(seconds=window_seconds)
    failed = (await session.execute(select(func.count()).select_from(Transaction).where(Transaction.status == "failed", Transaction.failed_at >= since))).scalar_one()
    completed = (await session.execute(select(func.count()).select_from(Transaction).where(Transaction.status == "completed", Transaction.completed_at >= since))).scalar_one()
    rows = (await session.execute(
        select(TransactionEvent.event_type, func.count()).where(TransactionEvent.event_type.in_(("post_ok", "post_failed")), TransactionEvent.at >= since).group_by(TransactionEvent.event_type)
    )).all()
    by_type = dict(rows)
    return {"failed": failed, "completed": completed, "postOk": by_type.get("post_ok", 0), "postFailed": by_type.get("post_failed", 0)}


async def timeseries(session: AsyncSession, minutes: int) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=minutes - 1)
    def minute(col):
        # literal 'minute' (not a bind parameter) so SELECT and GROUP BY render the identical expression
        return func.date_trunc(literal_column("'minute'"), col)

    async def grouped(col, *aggregates, where) -> dict[datetime, Any]:
        b = minute(col)
        rows = (await session.execute(select(b, *aggregates).where(*where).group_by(b))).all()
        return {row[0]: (row[1:] if len(row) > 2 else row[1]) for row in rows}

    submitted = await grouped(Transaction.submitted_at, func.count(), where=[Transaction.submitted_at >= start])
    completed = await grouped(
        Transaction.completed_at, func.count(), func.avg(func.extract("epoch", Transaction.completed_at - Transaction.processing_started_at)),
        where=[Transaction.status == "completed", Transaction.completed_at >= start],
    )
    failed = await grouped(Transaction.failed_at, func.count(), where=[Transaction.status == "failed", Transaction.failed_at >= start])
    retries = await grouped(TransactionEvent.at, func.count(), where=[TransactionEvent.event_type == "retry_scheduled", TransactionEvent.at >= start])
    depth = await grouped(MetricSample.ts, func.max(MetricSample.queue_depth), where=[MetricSample.ts >= start])

    points = []
    for i in range(minutes):
        t = start + timedelta(minutes=i)
        done = completed.get(t)
        points.append({
            "time": t.isoformat(), "submitted": submitted.get(t, 0), "completed": done[0] if done else 0,
            "failed": failed.get(t, 0), "retries": retries.get(t, 0),
            "avgProcessingSeconds": float(done[1]) if done and done[1] is not None else None,
            "queueDepth": depth.get(t),
        })
    return points


async def record_sample(session: AsyncSession, bucket: datetime, queue_depth: int) -> bool:
    """One sample per time bucket cluster-wide (PK dedup), so N workers don't all write."""
    if (await session.execute(select(MetricSample.ts).where(MetricSample.ts == bucket))).first():
        return False
    counts = await status_counts(session)
    stmt = pg_insert(MetricSample.__table__).values(
        ts=bucket, queue_depth=queue_depth,
        pending=counts.get("pending", 0), processing=counts.get("processing", 0) + counts.get("verifying", 0),
        completed=counts.get("completed", 0), failed=counts.get("failed", 0),
    ).on_conflict_do_nothing(index_elements=["ts"])
    return (await session.execute(stmt)).rowcount == 1


async def prune_samples(session: AsyncSession, retention_hours: int) -> None:
    await session.execute(delete(MetricSample).where(MetricSample.ts < func.now() - timedelta(hours=retention_hours)))
