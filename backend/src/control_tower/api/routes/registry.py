"""Agents, environments, the operation catalogue and the policy matrix."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from control_tower.api.deps import AppClock, Catalog, CurrentOperator, DbSession
from control_tower.api.schemas import (
    AgentCreate,
    AgentCreated,
    AgentOut,
    AgentStatusUpdate,
    EnvironmentOut,
    OperationOut,
    PolicyCell,
    PolicyMatrix,
    PolicyRow,
)
from control_tower.core.enums import AgentStatus, EnvironmentKind
from control_tower.core.operations import OperationSpec
from control_tower.core.policy import PolicyRequest, decide
from control_tower.logging import get_logger
from control_tower.persistence.models import Agent, Environment, PolicyRule
from control_tower.security.tokens import hash_token, new_agent_token, token_hint

log = get_logger(__name__)
router = APIRouter(tags=["registry"])


def operation_out(spec: OperationSpec) -> OperationOut:
    return OperationOut(
        key=spec.key,
        platform=spec.platform,
        title=spec.title,
        description=spec.description,
        risk=spec.risk,
        parameters_schema=spec.params_model.model_json_schema(),
    )


@router.get("/environments", response_model=list[EnvironmentOut], summary="List environments")
def list_environments(_: CurrentOperator, session: DbSession) -> list[Environment]:
    return list(session.scalars(select(Environment).order_by(Environment.name)))


@router.get("/operations", response_model=list[OperationOut], summary="List operations")
def list_operations(_: CurrentOperator, catalog: Catalog) -> list[OperationOut]:
    return [operation_out(spec) for spec in catalog]


@router.get("/agents", response_model=list[AgentOut], summary="List registered agents")
def list_agents(_: CurrentOperator, session: DbSession) -> list[Agent]:
    return list(session.scalars(select(Agent).order_by(Agent.name)))


def _get_agent(session: DbSession, agent_id: uuid.UUID) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found.")
    return agent


@router.get("/agents/{agent_id}", response_model=AgentOut, summary="Get one agent")
def get_agent(agent_id: uuid.UUID, _: CurrentOperator, session: DbSession) -> Agent:
    return _get_agent(session, agent_id)


@router.post(
    "/agents",
    response_model=AgentCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Register an agent and issue its token",
)
def create_agent(
    body: AgentCreate,
    operator: CurrentOperator,
    session: DbSession,
    catalog: Catalog,
    clock: AppClock,
) -> AgentCreated:
    environment = session.get(Environment, body.environment_id)
    if environment is None:
        raise HTTPException(422, "Environment not found.")

    granted = sorted(set(body.granted_operations))
    for key in granted:
        spec = catalog.get(key)
        if spec is None:
            raise HTTPException(422, f"Unknown operation '{key}'.")
        if spec.platform != environment.platform:
            raise HTTPException(
                422,
                f"Operation '{key}' does not apply to {environment.platform} environments.",
            )

    token = new_agent_token()
    agent = Agent(
        name=body.name,
        provider=body.provider,
        status=AgentStatus.ACTIVE,
        environment_id=environment.id,
        granted_operations=granted,
        token_hash=hash_token(token),
        token_hint=token_hint(token),
        created_at=clock(),
    )
    session.add(agent)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"An agent named '{body.name}' already exists."
        ) from exc
    session.refresh(agent)
    log.info("registry.agent_created", agent_id=str(agent.id), operator_id=str(operator.id))
    return AgentCreated(agent=AgentOut.model_validate(agent), token=token)


@router.patch("/agents/{agent_id}", response_model=AgentOut, summary="Enable or disable an agent")
def update_agent_status(
    agent_id: uuid.UUID, body: AgentStatusUpdate, operator: CurrentOperator, session: DbSession
) -> Agent:
    agent = _get_agent(session, agent_id)
    agent.status = body.status
    session.commit()
    log.info(
        "registry.agent_status_changed",
        agent_id=str(agent.id),
        status=body.status.value,
        operator_id=str(operator.id),
    )
    return agent


@router.get(
    "/policies",
    response_model=PolicyMatrix,
    summary="Policy for every operation on every kind of environment",
)
def policy_matrix(_: CurrentOperator, session: DbSession, catalog: Catalog) -> PolicyMatrix:
    rules = {
        (rule.operation_key, rule.environment_kind): rule.effect
        for rule in session.scalars(select(PolicyRule))
    }
    kinds = list(EnvironmentKind)
    rows = []
    for spec in catalog:
        cells = []
        for kind in kinds:
            # Evaluate with the real decision function for a hypothetical active
            # agent holding the grant, so the matrix can never drift from enforcement.
            decision = decide(
                PolicyRequest(
                    agent_active=True,
                    agent_granted_operations=frozenset({spec.key}),
                    operation_key=spec.key,
                    risk=spec.risk,
                    environment_kind=kind,
                ),
                rules,
            )
            cells.append(
                PolicyCell(
                    environment_kind=kind,
                    configured=rules.get((spec.key, kind)),
                    effective=decision.effect,
                    reason=decision.reason,
                )
            )
        rows.append(PolicyRow(operation=operation_out(spec), cells=cells))
    return PolicyMatrix(environment_kinds=kinds, rows=rows)
