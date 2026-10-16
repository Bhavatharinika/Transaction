import pytest
from pydantic import ValidationError

from app.schemas.transaction import TransactionCreate

VALID = {"id": "123e4567-e89b-12d3-a456-426614174000", "amount": 100.50, "currency": "usd", "description": "Test", "timestamp": "2023-01-15T10:30:00Z"}


def test_valid_payload_normalised():
    tx = TransactionCreate(**VALID)
    assert tx.currency == "USD" and str(tx.amount) == "100.5" and tx.timestamp.utcoffset().total_seconds() == 0


def test_naive_timestamp_assumed_utc():
    assert TransactionCreate(**{**VALID, "timestamp": "2023-01-15T10:30:00"}).timestamp.tzinfo is not None


@pytest.mark.parametrize("override", [
    {"id": "not-a-uuid"}, {"amount": 0}, {"amount": -5}, {"amount": "abc"}, {"amount": "NaN"}, {"amount": "Infinity"}, {"amount": 1.23456},
    {"currency": "XXXX"}, {"currency": "ZZZ"}, {"currency": ""}, {"timestamp": "yesterday"}, {"description": "x" * 1001},
    {"metadata": {"k": "v" * 9000}},
])
def test_invalid_payloads_rejected(override):
    with pytest.raises(ValidationError):
        TransactionCreate(**{**VALID, **override})


@pytest.mark.parametrize("missing", ["id", "amount", "currency", "timestamp"])
def test_required_fields(missing):
    payload = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ValidationError):
        TransactionCreate(**payload)
