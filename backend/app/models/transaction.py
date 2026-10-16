"""ORM models. The schema is created by Alembic (alembic/versions/0001_initial.py), never by create_all."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STATUS_VALUES = "('pending','processing','verifying','completed','failed')"


class Transaction(Base):
    __tablename__ = "transactions"

    # The client-supplied UUID is both the primary key and the idempotency key (and the ID used at the posting service).
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_transactions_currency_iso"),
        CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_transactions_status"),
        CheckConstraint("retry_count >= 0", name="ck_transactions_retry_count"),
        Index("ix_transactions_status", "status"),
        # Dispatcher: "which open rows are due and not already queued?"
        Index("ix_transactions_dispatch", "next_attempt_at", postgresql_where=text("status IN ('pending','verifying')")),
        # Reaper: "which leases expired?"; heartbeat: "which leases does this worker hold?"
        Index("ix_transactions_lease_expiry", "lease_expires_at", postgresql_where=text("lease_owner IS NOT NULL")),
        Index("ix_transactions_lease_owner", "lease_owner", postgresql_where=text("lease_owner IS NOT NULL")),
        Index("ix_transactions_submitted_at", text("submitted_at DESC"), "id"),
        Index("ix_transactions_completed_at", "completed_at", postgresql_where=text("status = 'completed'")),
        Index("ix_transactions_failed_at", "failed_at", postgresql_where=text("status = 'failed'")),
    )


class TransactionEvent(Base):
    """Append-only audit trail written in the same DB transaction as each state change."""

    __tablename__ = "transaction_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        Index("ix_transaction_events_tx", "transaction_id", "id"),
        Index("ix_transaction_events_type_at", "event_type", "at"),
    )


class MetricSample(Base):
    """Periodic gauge snapshots so the dashboard can chart queue depth over time from real data."""

    __tablename__ = "metric_samples"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    queue_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    processing: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[int] = mapped_column(Integer, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, nullable=False)
