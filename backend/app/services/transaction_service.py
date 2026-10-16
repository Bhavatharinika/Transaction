"""Submission and query use-cases for the API."""
from dataclasses import dataclass
from uuid import UUID

from app.core.errors import AppError
from app.core.metrics import TX_ACCEPTED
from app.db.session import Database
from app.models.transaction import Transaction
from app.schemas.transaction import (
    ErrorInfo, TimelineEvent, TransactionCreate, TransactionDetail, TransactionListItem, TransactionPage, TransactionStatus,
)
from app.services import repository
from app.services.hashing import compute_payload_hash
from app.services.load_shedder import LoadShedder
from app.services.queue import SafeEnqueuer
from app.services.state_machine import public_status


@dataclass(frozen=True)
class SubmitResult:
    transaction_id: UUID
    status: str
    submitted_at: object
    created: bool


def to_status(tx: Transaction) -> TransactionStatus:
    failed = tx.status == "failed"
    return TransactionStatus(
        transaction_id=tx.id, status=public_status(tx.status), submitted_at=tx.submitted_at, completed_at=tx.completed_at,
        error=(tx.last_error or {}).get("message") if failed else None,
    )


class TransactionService:
    def __init__(self, db: Database, enqueuer: SafeEnqueuer, shedder: LoadShedder) -> None:
        self._db, self._enqueuer, self._shedder = db, enqueuer, shedder

    async def submit(self, body: TransactionCreate) -> SubmitResult:
        payload_hash = compute_payload_hash(
            amount=body.amount, currency=body.currency, description=body.description, timestamp=body.timestamp, metadata=body.metadata
        )
        try:
            self._shedder.check()
        except AppError:
            TX_ACCEPTED.labels("shed").inc()
            raise

        # 1) Durable commit FIRST. After this line the transaction can no longer be lost.
        async with self._db.transaction() as session:
            submitted_at = await repository.insert_if_absent(
                session, tx_id=body.id, amount=body.amount, currency=body.currency, description=body.description,
                timestamp=body.timestamp, metadata=body.metadata, payload_hash=payload_hash,
            )
            existing = None if submitted_at is not None else await repository.get_transaction(session, body.id)

        if existing is not None:  # idempotent replay or conflict
            if existing.payload_hash != payload_hash:
                TX_ACCEPTED.labels("conflict").inc()
                raise AppError(
                    409, "IDEMPOTENCY_CONFLICT",
                    f"Transaction {body.id} was already submitted with a different payload. Use a new id for a different transaction.",
                )
            TX_ACCEPTED.labels("duplicate").inc()
            return SubmitResult(existing.id, public_status(existing.status), existing.submitted_at, created=False)

        # 2) Then enqueue. Failure here is safe: the row's outbox flag is cleared and the dispatcher delivers it.
        self._shedder.note_accepted()
        if not await self._enqueuer.try_enqueue(str(body.id)):
            async with self._db.transaction() as session:
                await repository.clear_enqueued(session, body.id)
        TX_ACCEPTED.labels("created").inc()
        return SubmitResult(body.id, "pending", submitted_at, created=True)

    async def get(self, tx_id: UUID) -> TransactionDetail:
        async with self._db.transaction() as session:
            tx = await repository.get_transaction(session, tx_id)
            if tx is None:
                raise AppError(404, "TRANSACTION_NOT_FOUND", f"Transaction {tx_id} not found")
            events = await repository.get_events(session, tx_id)
        base = to_status(tx)
        last_error = ErrorInfo(**tx.last_error) if tx.last_error else None
        return TransactionDetail(
            **base.model_dump(), amount=tx.amount, currency=tx.currency, description=tx.description, timestamp=tx.timestamp,
            metadata=tx.meta, retry_count=tx.retry_count, processing_started_at=tx.processing_started_at, failed_at=tx.failed_at,
            last_error=last_error, next_attempt_at=tx.next_attempt_at if tx.status in ("pending", "verifying") else None,
            created_at=tx.created_at, updated_at=tx.updated_at,
            events=[TimelineEvent(type=e.event_type, at=e.at, details=e.details) for e in events],
        )

    async def list(self, *, status, q, limit, offset, sort, descending) -> TransactionPage:
        async with self._db.transaction() as session:
            rows, total = await repository.list_transactions(session, status=status, q=q, limit=limit, offset=offset, sort=sort, descending=descending)
        items = [
            TransactionListItem(
                transaction_id=t.id, amount=t.amount, currency=t.currency, description=t.description, status=public_status(t.status),
                submitted_at=t.submitted_at, processing_started_at=t.processing_started_at, completed_at=t.completed_at, retry_count=t.retry_count,
            ) for t in rows
        ]
        return TransactionPage(items=items, total=total, limit=limit, offset=offset)
