from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.hashing import compute_payload_hash

BASE = dict(amount=Decimal("100.50"), currency="USD", description="x", timestamp=datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc), metadata={"a": 1, "b": 2})


def test_same_payload_same_hash():
    assert compute_payload_hash(**BASE) == compute_payload_hash(**BASE)


def test_numeric_representation_is_normalised():
    assert compute_payload_hash(**{**BASE, "amount": Decimal("100.5")}) == compute_payload_hash(**{**BASE, "amount": Decimal("100.5000")})
    assert compute_payload_hash(**{**BASE, "amount": Decimal("100")}) == compute_payload_hash(**{**BASE, "amount": Decimal("1E+2")})


def test_metadata_key_order_irrelevant_and_none_equals_empty():
    assert compute_payload_hash(**{**BASE, "metadata": {"b": 2, "a": 1}}) == compute_payload_hash(**BASE)
    assert compute_payload_hash(**{**BASE, "metadata": None}) == compute_payload_hash(**{**BASE, "metadata": {}})


def test_equivalent_instants_in_different_zones_match():
    plus_two = timezone(timedelta(hours=2))
    shifted = BASE["timestamp"].astimezone(plus_two)
    assert compute_payload_hash(**{**BASE, "timestamp": shifted}) == compute_payload_hash(**BASE)


def test_any_semantic_change_alters_hash():
    original = compute_payload_hash(**BASE)
    for change in ({"amount": Decimal("100.51")}, {"currency": "EUR"}, {"description": "y"}, {"metadata": {"a": 2, "b": 2}},
                   {"timestamp": BASE["timestamp"] + timedelta(seconds=1)}):
        assert compute_payload_hash(**{**BASE, **change}) != original
