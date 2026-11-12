import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from control_tower.api.deps import (
    Adapters,
    AppClock,
    Catalog,
    CorrelationId,
    CurrentOperator,
    DbSession,
)
from control_tower.api.schemas import (
    ApprovalDecision,
    AuditEventOut,
    ExecutionDetail,
    ExecutionPage,
    ExecutionSummary,
    Overview,
)
from control_tower.core.enums import ExecutionStatus, PolicyEffect
from control_tower.core.operations import OperationCatalog
from control_tower.gateway.service import ApprovalBinding, ApprovalError, Gateway
from control_tower.persistence import queries
from control_tower.persistence.models import AuditEvent, Execution

router = APIRouter(tags=["executions"])


def _title(catalog: OperationCatalog, key: str) -> str | None:
    spec = catalog.get(key)
    return spec.title if spec else None


@router.get("/overview", response_model=Overview, summary="Counts for the overview page")
def overview(_: CurrentOperator, session: DbSession) -> queries.OverviewCounts:
    return queries.overview_counts(session)


@router.get("/executions", response_model=ExecutionPage, summary="Search execution records")
def list_executions(
    _: CurrentOperator,
    session: DbSession,
    catalog: Catalog,
    status_: Annotated[list[ExecutionStatus] | None, Query(alias="status")] = None,
    agent_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    operation: Annotated[str | None, Query(max_length=120)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    policy_effect: PolicyEffect | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExecutionPage:
    rows, total = queries.list_executions(
        session,
        queries.ExecutionFilter(
            statuses=tuple(status_ or ()),
            agent_id=agent_id,
            environment_id=environment_id,
            operation_key=operation,
            search=q,
            policy_effect=policy_effect,
        ),
        limit=limit,
        offset=offset,
    )
    items = [
        ExecutionSummary.model_validate(row).model_copy(
            update={"operation_title": _title(catalog, row.operation_key)}
        )
        for row in rows
    ]
    return ExecutionPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/executions/{execution_id}", response_model=ExecutionDetail, summary="Get one execution record"
)
def get_execution(
    execution_id: uuid.UUID, _: CurrentOperator, session: DbSession, catalog: Catalog
) -> ExecutionDetail:
    row = session.get(Execution, execution_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found.")
    return ExecutionDetail.model_validate(row).model_copy(
        update={"operation_title": _title(catalog, row.operation_key)}
    )


@router.get("/executions/{execution_id}/audit-events", response_model=list[AuditEventOut])
def execution_audit(
    execution_id: uuid.UUID,
    _: CurrentOperator,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AuditEventOut]:
    if session.get(Execution, execution_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found.")
    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.execution_id == execution_id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    return [AuditEventOut.model_validate(row) for row in rows]


def _decision(
    action: Literal["approve", "reject", "execute"],
    execution_id: uuid.UUID,
    body: ApprovalDecision,
    operator_id: uuid.UUID,
    session: DbSession,
    catalog: Catalog,
    adapters: Adapters,
    clock: AppClock,
    correlation_id: CorrelationId,
) -> ExecutionDetail:
    gateway = Gateway(session, catalog, adapters, clock)
    binding = ApprovalBinding(body.action_hash, body.agent_id, body.environment_id)
    try:
        if action == "approve":
            gateway.approve(execution_id, binding, operator_id, correlation_id)
            row = gateway.execute_approved(execution_id, binding, operator_id, correlation_id)
        elif action == "reject":
            row = gateway.reject(execution_id, binding, operator_id, correlation_id)
        else:
            row = gateway.execute_approved(execution_id, binding, operator_id, correlation_id)
    except ApprovalError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    return ExecutionDetail.model_validate(row).model_copy(
        update={"operation_title": _title(catalog, row.operation_key)}
    )


@router.post("/executions/{execution_id}/approve", response_model=ExecutionDetail)
def approve_execution(
    execution_id: uuid.UUID,
    body: ApprovalDecision,
    operator: CurrentOperator,
    session: DbSession,
    catalog: Catalog,
    adapters: Adapters,
    clock: AppClock,
    correlation_id: CorrelationId,
) -> ExecutionDetail:
    return _decision(
        "approve",
        execution_id,
        body,
        operator.id,
        session,
        catalog,
        adapters,
        clock,
        correlation_id,
    )


@router.post("/executions/{execution_id}/reject", response_model=ExecutionDetail)
def reject_execution(
    execution_id: uuid.UUID,
    body: ApprovalDecision,
    operator: CurrentOperator,
    session: DbSession,
    catalog: Catalog,
    adapters: Adapters,
    clock: AppClock,
    correlation_id: CorrelationId,
) -> ExecutionDetail:
    return _decision(
        "reject", execution_id, body, operator.id, session, catalog, adapters, clock, correlation_id
    )


@router.post("/executions/{execution_id}/execute", response_model=ExecutionDetail)
def execute_approval(
    execution_id: uuid.UUID,
    body: ApprovalDecision,
    operator: CurrentOperator,
    session: DbSession,
    catalog: Catalog,
    adapters: Adapters,
    clock: AppClock,
    correlation_id: CorrelationId,
) -> ExecutionDetail:
    return _decision(
        "execute",
        execution_id,
        body,
        operator.id,
        session,
        catalog,
        adapters,
        clock,
        correlation_id,
    )
