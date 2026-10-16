"""Initial schema: transactions, transaction_events, metric_samples.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_transactions_currency_iso"),
        sa.CheckConstraint("status IN ('pending','processing','verifying','completed','failed')", name="ck_transactions_status"),
        sa.CheckConstraint("retry_count >= 0", name="ck_transactions_retry_count"),
    )
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index("ix_transactions_dispatch", "transactions", ["next_attempt_at"], postgresql_where=sa.text("status IN ('pending','verifying')"))
    op.create_index("ix_transactions_lease_expiry", "transactions", ["lease_expires_at"], postgresql_where=sa.text("lease_owner IS NOT NULL"))
    op.create_index("ix_transactions_lease_owner", "transactions", ["lease_owner"], postgresql_where=sa.text("lease_owner IS NOT NULL"))
    op.create_index("ix_transactions_submitted_at", "transactions", [sa.text("submitted_at DESC"), "id"])
    op.create_index("ix_transactions_completed_at", "transactions", ["completed_at"], postgresql_where=sa.text("status = 'completed'"))
    op.create_index("ix_transactions_failed_at", "transactions", ["failed_at"], postgresql_where=sa.text("status = 'failed'"))

    op.create_table(
        "transaction_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_transaction_events_tx", "transaction_events", ["transaction_id", "id"])
    op.create_index("ix_transaction_events_type_at", "transaction_events", ["event_type", "at"])

    op.create_table(
        "metric_samples",
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("queue_depth", sa.Integer, nullable=False),
        sa.Column("pending", sa.Integer, nullable=False),
        sa.Column("processing", sa.Integer, nullable=False),
        sa.Column("completed", sa.Integer, nullable=False),
        sa.Column("failed", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("metric_samples")
    op.drop_table("transaction_events")
    op.drop_table("transactions")
