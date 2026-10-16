"""Request / response contracts (camelCase on the wire)."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import json
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.services.currencies import ISO_4217_CODES
from app.services.hashing import normalize_timestamp

MAX_METADATA_BYTES = 8192
PublicStatus = Literal["pending", "processing", "completed", "failed"]


class TransactionCreate(BaseModel):
    id: UUID
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    currency: str = Field(min_length=3, max_length=3)
    description: str = Field(default="", max_length=1000)
    timestamp: datetime
    metadata: dict[str, Any] | None = None

    @field_validator("amount")
    @classmethod
    def _finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("amount must be a finite number")
        return v

    @field_validator("currency")
    @classmethod
    def _iso_4217(cls, v: str) -> str:
        code = v.strip().upper()
        if code not in ISO_4217_CODES:
            raise ValueError("currency must be a valid ISO 4217 code (e.g. USD)")
        return code

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return normalize_timestamp(v)

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, default=str)) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be at most {MAX_METADATA_BYTES} bytes when serialised")
        return v


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TransactionAccepted(CamelModel):
    transaction_id: UUID
    status: PublicStatus
    submitted_at: datetime


class TransactionStatus(TransactionAccepted):
    completed_at: datetime | None = None
    error: str | None = None


class ErrorInfo(CamelModel):
    code: str
    message: str
    at: datetime | None = None


class TimelineEvent(CamelModel):
    type: str
    at: datetime
    details: dict[str, Any] = {}


class TransactionDetail(TransactionStatus):
    amount: Decimal
    currency: str
    description: str
    timestamp: datetime
    metadata: dict[str, Any]
    retry_count: int
    processing_started_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: ErrorInfo | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    events: list[TimelineEvent] = []


class TransactionListItem(CamelModel):
    transaction_id: UUID
    amount: Decimal
    currency: str
    description: str
    status: PublicStatus
    submitted_at: datetime
    processing_started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int


class TransactionPage(CamelModel):
    items: list[TransactionListItem]
    total: int
    limit: int
    offset: int
