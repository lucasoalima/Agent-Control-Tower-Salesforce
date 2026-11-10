from collections import Counter

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_tower.bootstrap import seed
from control_tower.core.enums import ExecutionStatus
from control_tower.core.operations import OperationCatalog
from control_tower.persistence.models import Agent, Environment, Operator, PolicyRule
from control_tower.platforms.registry import AdapterRegistry
from control_tower.security.passwords import verify_password
from control_tower.security.tokens import hash_token
from control_tower.settings import Settings
from control_tower.simulate import SCRIPT, simulate


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg, arg-type]


@pytest.mark.parametrize("operator_password", [None, ""])
def test_seed_creates_reference_data_and_is_idempotent(
    db: Session, catalog: OperationCatalog, operator_password: str | None
) -> None:
    report = seed(db, catalog, settings(operator_password=operator_password))

    assert db.scalar(select(func.count()).select_from(Environment)) == 3
    assert db.scalar(select(func.count()).select_from(PolicyRule)) == 24
    assert db.scalar(select(func.count()).select_from(Agent)) == 3
    assert set(report.agent_tokens) == {"claude-code-dev", "codex-uat", "claude-code-prod"}

    for name, token in report.agent_tokens.items():
        agent = db.scalar(select(Agent).where(Agent.name == name))
        assert agent is not None and agent.token_hash == hash_token(token)

    operator = db.scalar(select(Operator))
    assert operator is not None and report.operator_password is not None
    assert len(report.operator_password) >= 20, "generated passwords are long"
    assert verify_password(operator.password_hash, report.operator_password)
    assert not verify_password(operator.password_hash, "")

    again = seed(db, catalog, settings(operator_password=operator_password))
    assert again.created == [] and again.agent_tokens == {} and again.operator_password is None
    assert db.scalar(select(func.count()).select_from(PolicyRule)) == 24


def test_seed_uses_a_configured_password_without_echoing_it(
    db: Session, catalog: OperationCatalog
) -> None:
    report = seed(
        db, catalog, settings(operator_password="chosen-by-the-user-1", operator_username="lucas")
    )
    operator = db.scalar(select(Operator))
    assert operator is not None and operator.username == "lucas"
    assert verify_password(operator.password_hash, "chosen-by-the-user-1")
    assert report.operator_password is None


def test_simulation_produces_every_kind_of_outcome(
    db: Session, catalog: OperationCatalog, adapters: AdapterRegistry
) -> None:
    seed(db, catalog, settings())
    executions = simulate(db, catalog, adapters)
    assert len(executions) == len(SCRIPT)
    assert Counter(e.status for e in executions) == {
        ExecutionStatus.COMPLETED: 7,
        ExecutionStatus.FAILED: 2,
        ExecutionStatus.PENDING_APPROVAL: 2,
        ExecutionStatus.DENIED: 5,
    }


def test_seed_generates_password_when_environment_is_empty(
    db: Session, catalog: OperationCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACT_OPERATOR_PASSWORD", "")
    report = seed(db, catalog, settings())
    operator = db.scalar(select(Operator))
    assert operator is not None and report.operator_password is not None
    assert len(report.operator_password) >= 20
    assert verify_password(operator.password_hash, report.operator_password)
    assert not verify_password(operator.password_hash, "")
