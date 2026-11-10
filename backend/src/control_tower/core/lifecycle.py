"""Execution state machine.

    (new) ─┬─> DENIED
           ├─> PENDING_APPROVAL ─┬─> APPROVED ──> EXECUTING ─┬─> COMPLETED
           │                     ├─> REJECTED                └─> FAILED
           │                     └─> EXPIRED
           └─> EXECUTING  (policy effect ALLOW)

DENIED is deliberately outside the approval lifecycle: a denied request was
never a candidate for execution and can never become one.
"""

from control_tower.core.enums import ExecutionStatus as S

INITIAL_STATES: frozenset[S] = frozenset({S.DENIED, S.PENDING_APPROVAL, S.EXECUTING})

_TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.EXPIRED}),
    S.APPROVED: frozenset({S.EXECUTING}),
    S.EXECUTING: frozenset({S.COMPLETED, S.FAILED}),
    S.DENIED: frozenset(),
    S.REJECTED: frozenset(),
    S.EXPIRED: frozenset(),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL_STATES: frozenset[S] = frozenset(s for s, nxt in _TRANSITIONS.items() if not nxt)


class IllegalTransitionError(Exception):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"execution cannot move from {current.value} to {target.value}")
        self.current = current
        self.target = target


def can_transition(current: S, target: S) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)
