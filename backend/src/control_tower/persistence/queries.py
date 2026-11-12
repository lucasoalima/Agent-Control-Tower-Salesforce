"""Read-side queries used by the dashboard API."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session

from control_tower.core.enums import AgentStatus, ExecutionStatus, PolicyEffect
from control_tower.persistence.models import Agent, Environment, Execution


@dataclass(frozen=True, slots=True)
class OverviewCounts:
    agents_total: int
    agents_active: int
    executions_total: int
    pending_approvals: int
    failed_actions: int
    policy_violations: int


def overview_counts(session: Session) -> OverviewCounts:
    by_status: dict[ExecutionStatus, int] = {
        status: count
        for status, count in session.execute(
            select(Execution.status, func.count()).group_by(Execution.status)
        )
    }
    agents_total = session.scalar(select(func.count()).select_from(Agent)) or 0
    agents_active = (
        session.scalar(
            select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ACTIVE)
        )
        or 0
    )
    return OverviewCounts(
        agents_total=agents_total,
        agents_active=agents_active,
        executions_total=sum(by_status.values()),
        pending_approvals=by_status.get(ExecutionStatus.PENDING_APPROVAL, 0),
        failed_actions=by_status.get(ExecutionStatus.FAILED, 0),
        policy_violations=by_status.get(ExecutionStatus.DENIED, 0),
    )


@dataclass(frozen=True, slots=True)
class ExecutionFilter:
    statuses: tuple[ExecutionStatus, ...] = ()
    agent_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    operation_key: str | None = None
    search: str | None = None
    policy_effect: PolicyEffect | None = None


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _apply(stmt: Select[Execution], f: ExecutionFilter) -> Select[Execution]:
    if f.policy_effect is not None:
        stmt = stmt.where(Execution.policy_effect == f.policy_effect)
    if f.statuses:
        stmt = stmt.where(Execution.status.in_(f.statuses))
    if f.agent_id:
        stmt = stmt.where(Execution.agent_id == f.agent_id)
    if f.environment_id:
        stmt = stmt.where(Execution.environment_id == f.environment_id)
    if f.operation_key:
        stmt = stmt.where(Execution.operation_key == f.operation_key)
    if f.search and f.search.strip():
        pattern = _like_pattern(f.search.strip())
        stmt = stmt.where(
            or_(
                Execution.operation_key.ilike(pattern, escape="\\"),
                Execution.correlation_id.ilike(pattern, escape="\\"),
                cast(Execution.id, String).ilike(pattern, escape="\\"),
                Execution.action_hash.ilike(pattern, escape="\\"),
                Agent.name.ilike(pattern, escape="\\"),
                Environment.name.ilike(pattern, escape="\\"),
            )
        )
    return stmt


def list_executions(
    session: Session, f: ExecutionFilter, *, limit: int, offset: int
) -> tuple[list[Execution], int]:
    base = (
        select(Execution)
        .join(Agent, Execution.agent_id == Agent.id)
        .join(Environment, Execution.environment_id == Environment.id)
    )
    filtered = _apply(base, f)
    total = session.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    rows = session.scalars(
        filtered.order_by(Execution.requested_at.desc(), Execution.id).limit(limit).offset(offset)
    ).all()
    return list(rows), total
