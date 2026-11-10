"""Enumerations shared by the whole backend.

Values are stored in the database as plain strings, so renaming a member is a
schema change and needs a migration.
"""

from enum import StrEnum


class EnvironmentKind(StrEnum):
    """How sensitive a target environment is. Policies are written per kind."""

    DEVELOPMENT = "DEVELOPMENT"
    TESTING = "TESTING"
    PRODUCTION = "PRODUCTION"


class RiskClass(StrEnum):
    """What an operation can do to the target system."""

    READ = "READ"  # observes state, changes nothing
    EXECUTE = "EXECUTE"  # runs something (for example tests) without changing metadata
    WRITE = "WRITE"  # changes a single piece of metadata or configuration
    DEPLOY = "DEPLOY"  # changes a set of components as one deployment


class PolicyEffect(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class DecisionReason(StrEnum):
    """Why the gateway reached its decision. Stored with every execution record."""

    RULE_MATCH = "RULE_MATCH"
    NO_MATCHING_RULE = "NO_MATCHING_RULE"
    PRODUCTION_GUARDRAIL = "PRODUCTION_GUARDRAIL"
    OPERATION_NOT_GRANTED = "OPERATION_NOT_GRANTED"
    AGENT_DISABLED = "AGENT_DISABLED"
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    UNSUPPORTED_BY_ADAPTER = "UNSUPPORTED_BY_ADAPTER"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    CREDENTIAL_IN_PAYLOAD = "CREDENTIAL_IN_PAYLOAD"


class ExecutionStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DENIED = "DENIED"


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AgentProvider(StrEnum):
    CLAUDE_CODE = "CLAUDE_CODE"
    CODEX = "CODEX"
    OTHER = "OTHER"


class AuditActorType(StrEnum):
    OPERATOR = "OPERATOR"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"
    ANONYMOUS = "ANONYMOUS"
