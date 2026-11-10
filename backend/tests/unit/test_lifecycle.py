import pytest

from control_tower.core.enums import ExecutionStatus as S
from control_tower.core.lifecycle import (
    INITIAL_STATES,
    TERMINAL_STATES,
    IllegalTransitionError,
    assert_transition,
    can_transition,
)

LEGAL = {
    (S.PENDING_APPROVAL, S.APPROVED),
    (S.PENDING_APPROVAL, S.REJECTED),
    (S.PENDING_APPROVAL, S.EXPIRED),
    (S.APPROVED, S.EXECUTING),
    (S.APPROVED, S.EXPIRED),
    (S.EXECUTING, S.COMPLETED),
    (S.EXECUTING, S.FAILED),
}


@pytest.mark.parametrize("current", list(S))
@pytest.mark.parametrize("target", list(S))
def test_only_the_documented_transitions_are_legal(current: S, target: S) -> None:
    assert can_transition(current, target) is ((current, target) in LEGAL)


def test_terminal_states() -> None:
    assert {S.DENIED, S.REJECTED, S.EXPIRED, S.COMPLETED, S.FAILED} == TERMINAL_STATES


def test_initial_states() -> None:
    assert {S.DENIED, S.PENDING_APPROVAL, S.EXECUTING} == INITIAL_STATES


def test_pending_request_cannot_skip_approval() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_transition(S.PENDING_APPROVAL, S.EXECUTING)


def test_denied_request_can_never_execute() -> None:
    for target in S:
        assert not can_transition(S.DENIED, target)
