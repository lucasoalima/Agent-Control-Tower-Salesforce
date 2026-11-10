"""API contracts. These, not the ORM models, define what leaves the service."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from control_tower.core.enums import (
    AgentProvider,
    AgentStatus,
    DecisionReason,
    EnvironmentKind,
    ExecutionStatus,
    PolicyEffect,
    RiskClass,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# -- auth -----------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class OperatorOut(ApiModel):
    id: uuid.UUID
    username: str


# -- registry -------------------------------------------------------------


class EnvironmentOut(ApiModel):
    id: uuid.UUID
    name: str
    platform: str
    kind: EnvironmentKind
    adapter_key: str
    external_id: str
    is_mock: bool
    created_at: datetime


class AgentOut(ApiModel):
    id: uuid.UUID
    name: str
    provider: AgentProvider
    status: AgentStatus
    environment: EnvironmentOut
    granted_operations: list[str]
    token_hint: str
    created_at: datetime
    last_seen_at: datetime | None


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")]
    provider: AgentProvider
    environment_id: uuid.UUID
    granted_operations: Annotated[list[str], Field(max_length=100)]


class AgentCreated(BaseModel):
    agent: AgentOut
    token: str = Field(description="Shown once. Only its hash is stored.")


class AgentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: AgentStatus


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect: PolicyEffect


class OperationOut(BaseModel):
    key: str
    platform: str
    title: str
    description: str
    risk: RiskClass
    parameters_schema: dict[str, Any]


class PolicyCell(BaseModel):
    environment_kind: EnvironmentKind
    # What is configured. None means no rule exists.
    configured: PolicyEffect | None
    # What the gateway will actually do for an agent that holds the grant.
    effective: PolicyEffect
    reason: DecisionReason


class PolicyRow(BaseModel):
    operation: OperationOut
    cells: list[PolicyCell]


class PolicyMatrix(BaseModel):
    environment_kinds: list[EnvironmentKind]
    rows: list[PolicyRow]


# -- executions -----------------------------------------------------------


class AgentRef(ApiModel):
    id: uuid.UUID
    name: str
    provider: AgentProvider


class EnvironmentRef(ApiModel):
    id: uuid.UUID
    name: str
    kind: EnvironmentKind
    platform: str


class ExecutionSummary(ApiModel):
    id: uuid.UUID
    correlation_id: str
    agent: AgentRef
    environment: EnvironmentRef
    operation_key: str
    operation_title: str | None = None
    policy_effect: PolicyEffect
    decision_reason: DecisionReason
    status: ExecutionStatus
    requested_at: datetime
    decided_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionDetail(ExecutionSummary):
    decision_detail: str
    action_hash: str | None
    parameters: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None


class ExecutionPage(BaseModel):
    items: list[ExecutionSummary]
    total: int
    limit: int
    offset: int


class Overview(BaseModel):
    agents_total: int
    agents_active: int
    executions_total: int
    pending_approvals: int
    failed_actions: int
    policy_violations: int


# -- gateway --------------------------------------------------------------


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(ApiModel):
    execution_id: uuid.UUID
    correlation_id: str
    operation: str
    status: ExecutionStatus
    policy_effect: PolicyEffect
    decision_reason: DecisionReason
    decision_detail: str
    action_hash: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
