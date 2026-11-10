"""Agent-facing API. The only way an agent can act on a target environment."""

import uuid

from fastapi import APIRouter, HTTPException, Response, status

from control_tower.api.deps import (
    Adapters,
    AppClock,
    Catalog,
    CorrelationId,
    CurrentAgent,
    DbSession,
)
from control_tower.api.schemas import ToolRequest, ToolResponse
from control_tower.core.enums import ExecutionStatus
from control_tower.gateway.service import Gateway
from control_tower.persistence.models import Execution

router = APIRouter(prefix="/gateway", tags=["gateway"])


def _response(execution: Execution) -> ToolResponse:
    return ToolResponse(
        execution_id=execution.id,
        correlation_id=execution.correlation_id,
        operation=execution.operation_key,
        status=execution.status,
        policy_effect=execution.policy_effect,
        decision_reason=execution.decision_reason,
        decision_detail=execution.decision_detail,
        action_hash=execution.action_hash,
        result=execution.result,
        error=execution.error,
    )


@router.post(
    "/tool-requests",
    response_model=ToolResponse,
    summary="Ask the gateway to perform an operation",
    responses={202: {"model": ToolResponse, "description": "Waiting for human approval"}},
)
def submit_tool_request(
    body: ToolRequest,
    response: Response,
    agent: CurrentAgent,
    session: DbSession,
    catalog: Catalog,
    adapters: Adapters,
    clock: AppClock,
    correlation_id: CorrelationId,
) -> ToolResponse:
    """The request is always recorded. The HTTP status says whether it was
    *processed* (200) or is *waiting* (202); the outcome is in ``status``.
    A denied request is a 200 with ``status: DENIED``, not a 403: the caller was
    authenticated and the gateway did its job."""
    execution = Gateway(session, catalog, adapters, clock).submit(
        agent, body.operation, body.parameters, correlation_id
    )
    if execution.status is ExecutionStatus.PENDING_APPROVAL:
        response.status_code = status.HTTP_202_ACCEPTED
    return _response(execution)


@router.get(
    "/tool-requests/{execution_id}",
    response_model=ToolResponse,
    summary="Check the state of one of the calling agent's own requests",
)
def get_tool_request(
    execution_id: uuid.UUID, agent: CurrentAgent, session: DbSession
) -> ToolResponse:
    execution = session.get(Execution, execution_id)
    # Same answer for "does not exist" and "belongs to another agent".
    if execution is None or execution.agent_id != agent.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool request not found.")
    return _response(execution)
