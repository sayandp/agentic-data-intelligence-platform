import pytest

from app.state_machine import (
    AUTO_FIXED,
    AWAITING_APPROVAL,
    DETECTED,
    DIAGNOSED,
    REJECTED,
    RESOLVED,
    IllegalTransitionError,
    transition,
)


def test_detected_to_diagnosed_is_legal():
    transition(DETECTED, DIAGNOSED)  # does not raise


@pytest.mark.parametrize("target", [AUTO_FIXED, AWAITING_APPROVAL])
def test_diagnosed_to_terminal_gate_decision_is_legal(target):
    transition(DIAGNOSED, target)


@pytest.mark.parametrize("target", [RESOLVED, REJECTED])
def test_awaiting_approval_to_resolution_is_legal(target):
    transition(AWAITING_APPROVAL, target)


@pytest.mark.parametrize("target", [RESOLVED, REJECTED])
def test_auto_fixed_to_resolution_is_legal(target):
    transition(AUTO_FIXED, target)


def test_detected_cannot_skip_straight_to_auto_fixed():
    with pytest.raises(IllegalTransitionError):
        transition(DETECTED, AUTO_FIXED)


def test_detected_cannot_skip_straight_to_awaiting_approval():
    with pytest.raises(IllegalTransitionError):
        transition(DETECTED, AWAITING_APPROVAL)


def test_diagnosed_cannot_go_backwards_to_detected():
    with pytest.raises(IllegalTransitionError):
        transition(DIAGNOSED, DETECTED)


def test_resolved_is_terminal():
    with pytest.raises(IllegalTransitionError):
        transition(RESOLVED, DIAGNOSED)


def test_rejected_is_terminal():
    with pytest.raises(IllegalTransitionError):
        transition(REJECTED, AUTO_FIXED)


def test_unknown_state_raises():
    with pytest.raises(IllegalTransitionError):
        transition("not_a_real_state", DIAGNOSED)


def test_illegal_transition_error_carries_states():
    with pytest.raises(IllegalTransitionError) as exc_info:
        transition(DETECTED, AUTO_FIXED)
    assert exc_info.value.current_state == DETECTED
    assert exc_info.value.new_state == AUTO_FIXED
