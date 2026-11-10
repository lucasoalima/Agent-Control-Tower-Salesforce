"""Expire waiting approvals and flag unknown outcomes without calling adapters."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from control_tower.core import lifecycle
from control_tower.core.enums import AuditActorType, ExecutionStatus
from control_tower.core.ports import Clock
from control_tower.logging import get_logger
from control_tower.persistence.audit import record_event
from control_tower.persistence.models import Execution
from control_tower.settings import Settings

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    expired: int
    reconciliation_required: int


def run_maintenance(
    session: Session, *, now: datetime, stale_after_seconds: int, batch_size: int = 100
) -> MaintenanceResult:
    session.execute(text("SET LOCAL statement_timeout = '5s'"))
    session.execute(text("SET LOCAL lock_timeout = '1s'"))
    waiting = list(
        session.scalars(
            select(Execution)
            .where(
                Execution.status.in_([ExecutionStatus.PENDING_APPROVAL, ExecutionStatus.APPROVED]),
                Execution.approval_expires_at <= now,
            )
            .order_by(Execution.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=Execution)
            .execution_options(populate_existing=True)
        )
    )
    for execution in waiting:
        deadline = execution.approval_expires_at
        assert deadline is not None
        lifecycle.assert_transition(execution.status, ExecutionStatus.EXPIRED)
        execution.status = ExecutionStatus.EXPIRED
        execution.finished_at = now
        record_event(
            session,
            "approval.expired",
            actor_type=AuditActorType.SYSTEM,
            actor_id=None,
            correlation_id=execution.correlation_id,
            occurred_at=now,
            execution_id=execution.id,
            details={"action_hash": execution.action_hash, "expires_at": deadline.isoformat()},
        )
    stale = list(
        session.scalars(
            select(Execution)
            .where(
                Execution.status == ExecutionStatus.EXECUTING,
                Execution.started_at <= now - timedelta(seconds=stale_after_seconds),
                Execution.reconciliation_required_at.is_(None),
            )
            .order_by(Execution.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=Execution)
            .execution_options(populate_existing=True)
        )
    )
    for execution in stale:
        execution.reconciliation_required_at = now
        record_event(
            session,
            "execution.reconciliation_required",
            actor_type=AuditActorType.SYSTEM,
            actor_id=None,
            correlation_id=execution.correlation_id,
            occurred_at=now,
            execution_id=execution.id,
            details={"outcome": "UNKNOWN"},
        )
    session.commit()
    return MaintenanceResult(expired=len(waiting), reconciliation_required=len(stale))


def maintenance_loop(
    sessions: sessionmaker[Session], settings: Settings, clock: Clock, stop: Event
) -> None:
    while not stop.is_set():
        try:
            with sessions() as session:
                run_maintenance(
                    session,
                    now=clock(),
                    stale_after_seconds=settings.execution_stale_after_seconds,
                )
        except Exception as exc:
            log.error("maintenance.failed", exception_type=type(exc).__name__)
        stop.wait(settings.maintenance_interval_seconds)
