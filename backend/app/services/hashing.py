"""Canonical payload hashing for idempotency-conflict detection. Pure logic."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def normalize_timestamp(value: datetime) -> datetime:
    """Naive timestamps are interpreted as UTC (documented assumption)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_payload(*, amount: Decimal, currency: str, description: str, timestamp: datetime, metadata: dict[str, Any] | None) -> str:
    """Stable JSON: 100.5 == 100.50, UTC timestamps, sorted keys, metadata None == {}."""
    document = {
        "amount": format(Decimal(amount).normalize(), "f"),
        "currency": currency.upper(),
        "description": description,
        "timestamp": normalize_timestamp(timestamp).isoformat(timespec="microseconds"),
        "metadata": metadata or {},
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_payload_hash(**kwargs: Any) -> str:
    return hashlib.sha256(canonical_payload(**kwargs).encode("utf-8")).hexdigest()
