"""Explicit transaction state machine. Pure logic, no I/O.

Internal states: pending, processing, verifying, completed, failed.
`verifying` is internal: the public API reports it as `processing`.

    pending ──claim──▶ processing ──POST ok / already exists──▶ completed
       ▲                   │  │
       │ retry (backoff)   │  └─ uncertain POST failure ─▶ verifying ──GET finds it──▶ completed
       └───────────────────┘                                  │
       ▲   pre-write failure confirmed by GET ────────────────┘ (retry → pending, or failed)
    processing / verifying ──retries exhausted / permanent rejection──▶ failed
"""
from enum import Enum


class TxStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[TxStatus, frozenset[TxStatus]] = {
    # claim: pending -> processing
    TxStatus.PENDING: frozenset({TxStatus.PROCESSING}),
    # processing -> completed (success / found), verifying (uncertain POST or crashed worker),
    # pending (retry or breaker deferral), failed (permanent rejection / exhaustion)
    TxStatus.PROCESSING: frozenset({TxStatus.COMPLETED, TxStatus.VERIFYING, TxStatus.PENDING, TxStatus.FAILED}),
    # verifying -> completed (GET found it), pending (confirmed missing -> retry), failed,
    # verifying (reschedule when GET is unavailable or the breaker is open)
    TxStatus.VERIFYING: frozenset({TxStatus.COMPLETED, TxStatus.PENDING, TxStatus.FAILED, TxStatus.VERIFYING}),
    TxStatus.COMPLETED: frozenset(),
    TxStatus.FAILED: frozenset(),
}

TERMINAL_STATUSES = frozenset({TxStatus.COMPLETED, TxStatus.FAILED})
OPEN_STATUSES = tuple(s.value for s in (TxStatus.PENDING, TxStatus.PROCESSING, TxStatus.VERIFYING))
DISPATCHABLE_STATUSES = (TxStatus.PENDING.value, TxStatus.VERIFYING.value)


class InvalidTransitionError(Exception):
    def __init__(self, current: TxStatus, target: TxStatus) -> None:
        super().__init__(f"Invalid transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: TxStatus, target: TxStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: TxStatus, target: TxStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def public_status(status: str | TxStatus) -> str:
    """Map internal status to the public four-state vocabulary."""
    status = TxStatus(status)
    return TxStatus.PROCESSING.value if status is TxStatus.VERIFYING else status.value


def internal_statuses_for(public: str) -> list[str]:
    """Inverse mapping, used by list filters."""
    if public == TxStatus.PROCESSING.value:
        return [TxStatus.PROCESSING.value, TxStatus.VERIFYING.value]
    return [public]
