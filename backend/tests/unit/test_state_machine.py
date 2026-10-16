import pytest

from app.services.state_machine import (
    ALLOWED_TRANSITIONS, TERMINAL_STATUSES, InvalidTransitionError, TxStatus, assert_transition, can_transition, internal_statuses_for, public_status,
)


@pytest.mark.parametrize("current,target", [
    (TxStatus.PENDING, TxStatus.PROCESSING),
    (TxStatus.PROCESSING, TxStatus.COMPLETED),
    (TxStatus.PROCESSING, TxStatus.VERIFYING),
    (TxStatus.PROCESSING, TxStatus.PENDING),
    (TxStatus.PROCESSING, TxStatus.FAILED),
    (TxStatus.VERIFYING, TxStatus.COMPLETED),
    (TxStatus.VERIFYING, TxStatus.PENDING),
    (TxStatus.VERIFYING, TxStatus.FAILED),
    (TxStatus.VERIFYING, TxStatus.VERIFYING),
])
def test_valid_transitions(current, target):
    assert can_transition(current, target)
    assert_transition(current, target)


@pytest.mark.parametrize("current,target", [
    (TxStatus.PENDING, TxStatus.COMPLETED),   # must be claimed first
    (TxStatus.PENDING, TxStatus.VERIFYING),
    (TxStatus.COMPLETED, TxStatus.PENDING),   # terminal
    (TxStatus.COMPLETED, TxStatus.PROCESSING),
    (TxStatus.FAILED, TxStatus.PENDING),
    (TxStatus.FAILED, TxStatus.COMPLETED),
    (TxStatus.VERIFYING, TxStatus.PROCESSING),  # verification never goes straight back to a POST attempt
])
def test_invalid_transitions_rejected(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)


def test_terminal_states_have_no_exits():
    for status in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_every_status_is_mapped():
    assert set(ALLOWED_TRANSITIONS) == set(TxStatus)


def test_verifying_is_hidden_from_public_api():
    assert public_status("verifying") == "processing"
    assert {public_status(s) for s in TxStatus} == {"pending", "processing", "completed", "failed"}
    assert internal_statuses_for("processing") == ["processing", "verifying"]
    assert internal_statuses_for("completed") == ["completed"]
