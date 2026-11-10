import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from control_tower.api.deps import Catalog, CurrentOperator, DbSession
from control_tower.api.schemas import ExecutionDetail, ExecutionPage, ExecutionSummary, Overview
from control_tower.core.enums import ExecutionStatus
from control_tower.core.operations import OperationCatalog
from control_tower.persistence import queries
from control_tower.persistence.models import Execution

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
