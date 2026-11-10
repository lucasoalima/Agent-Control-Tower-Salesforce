"""Reference data for a local installation: environments, default policies,
demo agents and the first operator. Idempotent: running it twice changes nothing.
"""

import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.clock import utcnow
from control_tower.core.enums import (
    AgentProvider,
    AgentStatus,
    EnvironmentKind,
    PolicyEffect,
)
from control_tower.core.operations import OperationCatalog
from control_tower.persistence.models import Agent, Environment, Operator, PolicyRule
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter
from control_tower.security.passwords import hash_password
from control_tower.security.tokens import hash_token, new_agent_token, token_hint
from control_tower.settings import Settings

A, R, D = PolicyEffect.ALLOW, PolicyEffect.REQUIRE_APPROVAL, PolicyEffect.DENY

# operation -> (DEVELOPMENT, TESTING, PRODUCTION)
DEFAULT_POLICIES: dict[str, tuple[PolicyEffect, PolicyEffect, PolicyEffect]] = {
    sf.ORG_DESCRIBE: (A, A, A),
    sf.METADATA_LIST: (A, A, A),
    sf.METADATA_READ: (A, A, A),
    sf.DATA_QUERY: (A, A, D),
    sf.TESTS_RUN: (A, A, D),
    sf.APEX_WRITE: (R, R, D),
    sf.PERMISSION_SET_MODIFY: (R, R, D),
    sf.DEPLOY: (R, R, D),
}

_READ_ONLY = [sf.ORG_DESCRIBE, sf.METADATA_LIST, sf.METADATA_READ, sf.DATA_QUERY]

DEMO_ENVIRONMENTS = [
    ("Developer Org (mock)", EnvironmentKind.DEVELOPMENT, "MOCK-ORG-DEV"),
    ("UAT Sandbox (mock)", EnvironmentKind.TESTING, "MOCK-ORG-UAT"),
    ("Production (mock)", EnvironmentKind.PRODUCTION, "MOCK-ORG-PROD"),
]

# name, provider, environment name, granted operations (None = every operation)
DEMO_AGENTS: list[tuple[str, AgentProvider, str, list[str] | None]] = [
    ("claude-code-dev", AgentProvider.CLAUDE_CODE, "Developer Org (mock)", None),
    (
        "codex-uat",
        AgentProvider.CODEX,
        "UAT Sandbox (mock)",
        [*_READ_ONLY, sf.TESTS_RUN, sf.DEPLOY],
    ),
    ("claude-code-prod", AgentProvider.CLAUDE_CODE, "Production (mock)", None),
]


@dataclass
class SeedReport:
    operator_username: str | None = None
    operator_password: str | None = None  # set only if generated or created now
    agent_tokens: dict[str, str] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)


def seed(session: Session, catalog: OperationCatalog, settings: Settings) -> SeedReport:
    report = SeedReport()
    now = utcnow()

    environments: dict[str, Environment] = {}
    for name, kind, external_id in DEMO_ENVIRONMENTS:
        env = session.scalar(select(Environment).where(Environment.name == name))
        if env is None:
            env = Environment(
                name=name,
                platform=sf.PLATFORM,
                kind=kind,
                adapter_key=MockSalesforceAdapter.adapter_key,
                external_id=external_id,
                created_at=now,
            )
            session.add(env)
            report.created.append(f"environment {name}")
        environments[name] = env
    session.flush()

    existing_rules = {
        (r.operation_key, r.environment_kind) for r in session.scalars(select(PolicyRule))
    }
    for operation_key, effects in DEFAULT_POLICIES.items():
        if operation_key not in catalog:
            raise RuntimeError(f"default policy refers to unknown operation {operation_key}")
        for kind, effect in zip(EnvironmentKind, effects, strict=True):
            if (operation_key, kind) not in existing_rules:
                session.add(
                    PolicyRule(
                        operation_key=operation_key,
                        environment_kind=kind,
                        effect=effect,
                        updated_at=now,
                        updated_by="seed",
                    )
                )
                report.created.append(f"policy {operation_key} on {kind.value}")

    for name, provider, env_name, granted in DEMO_AGENTS:
        if session.scalar(select(Agent).where(Agent.name == name)) is not None:
            continue
        token = new_agent_token()
        session.add(
            Agent(
                name=name,
                provider=provider,
                status=AgentStatus.ACTIVE,
                environment_id=environments[env_name].id,
                granted_operations=sorted(granted if granted is not None else catalog.keys()),
                token_hash=hash_token(token),
                token_hint=token_hint(token),
                created_at=now,
            )
        )
        report.agent_tokens[name] = token
        report.created.append(f"agent {name}")

    if session.scalar(select(Operator).limit(1)) is None:
        password = (
            settings.operator_password.get_secret_value()
            if settings.operator_password is not None
            else secrets.token_urlsafe(18)
        )
        session.add(
            Operator(
                username=settings.operator_username,
                password_hash=hash_password(password),
                created_at=now,
            )
        )
        report.operator_username = settings.operator_username
        # Only echo a password we generated; one supplied by the user is already known to them.
        report.operator_password = password if settings.operator_password is None else None
        report.created.append(f"operator {settings.operator_username}")

    session.commit()
    return report
