"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-10 12:48:00.745597
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "DEVELOPMENT",
                "TESTING",
                "PRODUCTION",
                name="environment_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environments")),
        sa.UniqueConstraint("name", name=op.f("uq_environments_name")),
    )
    op.create_table(
        "operators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operators")),
        sa.UniqueConstraint("username", name=op.f("uq_operators_username")),
    )
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.String(length=120), nullable=False),
        sa.Column(
            "environment_kind",
            sa.Enum(
                "DEVELOPMENT",
                "TESTING",
                "PRODUCTION",
                name="environment_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "effect",
            sa.Enum(
                "ALLOW",
                "REQUIRE_APPROVAL",
                "DENY",
                name="policy_effect",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_rules")),
        sa.UniqueConstraint(
            "operation_key", "environment_kind", name=op.f("uq_policy_rules_operation_key")
        ),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "CLAUDE_CODE",
                "CODEX",
                "OTHER",
                name="agent_provider",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DISABLED",
                name="agent_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("granted_operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_agents_environment_id_environments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("name", name=op.f("uq_agents_name")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_agents_token_hash")),
    )
    op.create_table(
        "operator_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            name=op.f("fk_operator_sessions_operator_id_operators"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_operator_sessions_token_hash")),
    )
    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.String(length=120), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "policy_effect",
            sa.Enum(
                "ALLOW",
                "REQUIRE_APPROVAL",
                "DENY",
                name="policy_effect",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "decision_reason",
            sa.Enum(
                "RULE_MATCH",
                "NO_MATCHING_RULE",
                "PRODUCTION_GUARDRAIL",
                "OPERATION_NOT_GRANTED",
                "AGENT_DISABLED",
                "UNKNOWN_OPERATION",
                "UNSUPPORTED_BY_ADAPTER",
                "INVALID_PARAMETERS",
                "CREDENTIAL_IN_PAYLOAD",
                name="decision_reason",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("decision_detail", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_APPROVAL",
                "APPROVED",
                "REJECTED",
                "EXPIRED",
                "EXECUTING",
                "COMPLETED",
                "FAILED",
                "DENIED",
                name="execution_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_executions_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_executions_environment_id_environments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executions")),
    )
    op.create_index(
        "ix_executions_agent_id_requested_at",
        "executions",
        ["agent_id", "requested_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_executions_correlation_id"), "executions", ["correlation_id"], unique=False
    )
    op.create_index(
        op.f("ix_executions_operation_key"), "executions", ["operation_key"], unique=False
    )
    op.create_index(
        op.f("ix_executions_requested_at"), "executions", ["requested_at"], unique=False
    )
    op.create_index(
        "ix_executions_status_requested_at", "executions", ["status", "requested_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_executions_status_requested_at", table_name="executions")
    op.drop_index(op.f("ix_executions_requested_at"), table_name="executions")
    op.drop_index(op.f("ix_executions_operation_key"), table_name="executions")
    op.drop_index(op.f("ix_executions_correlation_id"), table_name="executions")
    op.drop_index("ix_executions_agent_id_requested_at", table_name="executions")
    op.drop_table("executions")
    op.drop_table("operator_sessions")
    op.drop_table("agents")
    op.drop_table("policy_rules")
    op.drop_table("operators")
    op.drop_table("environments")
