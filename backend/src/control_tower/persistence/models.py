"""SQLAlchemy models.

Enumerations are stored as VARCHAR with a CHECK constraint rather than native
PostgreSQL enum types, which keeps migrations that add a value simple.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from control_tower.core.enums import (
    AgentProvider,
    AgentStatus,
    AuditActorType,
    DecisionReason,
    EnvironmentKind,
    ExecutionStatus,
    PolicyEffect,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _enum(enum_cls: type, name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
    }


class Operator(Base):
    """A human who can sign in to the dashboard."""

    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime]


class OperatorSession(Base):
    __tablename__ = "operator_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    operator: Mapped[Operator] = relationship(lazy="joined")


class Environment(Base):
    """A target system instance, for example one Salesforce sandbox."""

    __tablename__ = "environments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    platform: Mapped[str] = mapped_column(String(64))
    kind: Mapped[EnvironmentKind] = mapped_column(_enum(EnvironmentKind, "environment_kind"))
    adapter_key: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime]

    @property
    def is_mock(self) -> bool:
        return self.adapter_key.startswith("mock-")


class Agent(Base):
    """A registered AI agent identity, bound to exactly one environment."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provider: Mapped[AgentProvider] = mapped_column(_enum(AgentProvider, "agent_provider"))
    status: Mapped[AgentStatus] = mapped_column(_enum(AgentStatus, "agent_status"))
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id"))
    granted_operations: Mapped[list[str]]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_hint: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None]

    environment: Mapped[Environment] = relationship(lazy="joined")


class PolicyRule(Base):
    __tablename__ = "policy_rules"
    __table_args__ = (UniqueConstraint("operation_key", "environment_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operation_key: Mapped[str] = mapped_column(String(120))
    environment_kind: Mapped[EnvironmentKind] = mapped_column(
        _enum(EnvironmentKind, "environment_kind")
    )
    effect: Mapped[PolicyEffect] = mapped_column(_enum(PolicyEffect, "policy_effect"))
    updated_at: Mapped[datetime]
    updated_by: Mapped[str | None] = mapped_column(String(64))


class Execution(Base):
    """One agent tool request and everything that happened to it."""

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_agent_id_requested_at", "agent_id", "requested_at"),
        Index("ix_executions_status_requested_at", "status", "requested_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id"))

    # As requested by the agent. Not a foreign key: unknown operations are recorded too.
    operation_key: Mapped[str] = mapped_column(String(120), index=True)
    # Validated, canonical parameters for accepted requests; a redacted copy of
    # the raw input for requests refused at intake.
    parameters: Mapped[dict[str, Any]]
    # SHA-256 of (agent, environment, operation, parameters). NULL when the
    # request was refused before it could be validated.
    action_hash: Mapped[str | None] = mapped_column(String(64))

    policy_effect: Mapped[PolicyEffect] = mapped_column(_enum(PolicyEffect, "policy_effect"))
    decision_reason: Mapped[DecisionReason] = mapped_column(
        _enum(DecisionReason, "decision_reason")
    )
    decision_detail: Mapped[str] = mapped_column(Text)

    status: Mapped[ExecutionStatus] = mapped_column(_enum(ExecutionStatus, "execution_status"))
    result: Mapped[dict[str, Any] | None]
    error: Mapped[dict[str, Any] | None]

    requested_at: Mapped[datetime] = mapped_column(index=True)
    decided_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    approval_expires_at: Mapped[datetime | None]
    reconciliation_required_at: Mapped[datetime | None]

    agent: Mapped[Agent] = relationship(lazy="joined")
    environment: Mapped[Environment] = relationship(lazy="joined")


class AuditEvent(Base):
    """One immutable governance event, retained independently of its actor."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_execution_id_occurred_at", "execution_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime]
    action: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[AuditActorType] = mapped_column(_enum(AuditActorType, "audit_actor_type"))
    actor_id: Mapped[uuid.UUID | None]
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    execution_id: Mapped[uuid.UUID | None]
    details: Mapped[dict[str, Any]]
