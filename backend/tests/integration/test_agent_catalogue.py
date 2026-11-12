import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_tower.core.enums import AgentStatus, PolicyEffect, RiskClass
from control_tower.core.operations import OperationCatalog
from control_tower.persistence.models import AuditEvent, Execution, PolicyRule
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter
from tests.conftest import AgentFactory, World, bearer

URL = "/api/v1/gateway/operations"


@pytest.mark.parametrize("environment", ["dev", "uat", "prod"])
def test_catalogue_matches_the_bound_environment_and_has_strict_schemas(
    client: TestClient,
    world: World,
    make_agent: AgentFactory,
    catalog: OperationCatalog,
    environment: str,
) -> None:
    _, token = make_agent(getattr(world, environment))
    response = client.get(URL, headers=bearer(token))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    rows = response.json()
    expected = (
        {sf.ORG_DESCRIBE, sf.METADATA_LIST, sf.METADATA_READ}
        if environment == "prod"
        else catalog.keys()
    )
    assert [row["key"] for row in rows] == sorted(expected)
    for row in rows:
        spec = catalog.get(row["key"])
        assert spec is not None
        assert row == {
            "key": spec.key,
            "platform": spec.platform,
            "title": spec.title,
            "description": spec.description,
            "risk": spec.risk.value,
            "parameters_schema": spec.params_model.model_json_schema(),
        }
        assert row["parameters_schema"]["additionalProperties"] is False


def test_only_the_calling_agents_grants_are_exposed(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev, granted=[sf.ORG_DESCRIBE, "salesforce.unknown"])
    _, other_token = make_agent(world.uat, granted=[sf.DEPLOY])
    own = client.get(URL, headers=bearer(token))
    assert [row["key"] for row in own.json()] == [sf.ORG_DESCRIBE]
    other = client.get(URL, headers=bearer(other_token))
    assert [row["key"] for row in other.json()] == [sf.DEPLOY]
    assert token not in own.text and other_token not in other.text


@pytest.mark.parametrize("disabled", [False, True])
def test_disabled_or_ungranted_agents_have_no_catalogue(
    client: TestClient, world: World, make_agent: AgentFactory, disabled: bool
) -> None:
    _, token = make_agent(
        world.dev,
        status=AgentStatus.DISABLED if disabled else AgentStatus.ACTIVE,
        granted=None if disabled else [],
    )
    assert client.get(URL, headers=bearer(token)).json() == []


@pytest.mark.parametrize("change", ["missing_adapter", "wrong_platform"])
def test_unusable_environments_expose_no_schemas(
    client: TestClient,
    world: World,
    make_agent: AgentFactory,
    db: Session,
    change: str,
) -> None:
    _, token = make_agent(world.dev)
    if change == "missing_adapter":
        world.dev.adapter_key = "missing-adapter"
    else:
        world.dev.platform = "other-platform"
    db.commit()
    assert client.get(URL, headers=bearer(token)).json() == []


def test_adapter_capabilities_are_respected_without_executing(
    client: TestClient,
    world: World,
    make_agent: AgentFactory,
    mock_adapter: MockSalesforceAdapter,
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
) -> None:
    _, token = make_agent(world.dev)
    monkeypatch.setattr(mock_adapter, "supports", lambda key: key == sf.ORG_DESCRIBE)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Discovery must not execute an adapter.")

    monkeypatch.setattr(mock_adapter, "execute", forbidden)
    response = client.get(URL, headers=bearer(token))
    assert [row["key"] for row in response.json()] == [sf.ORG_DESCRIBE]
    assert db.scalar(select(func.count()).select_from(Execution)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("effect", [None, PolicyEffect.DENY, PolicyEffect.REQUIRE_APPROVAL])
def test_default_deny_and_policy_effects_are_applied(
    client: TestClient,
    world: World,
    make_agent: AgentFactory,
    db: Session,
    effect: PolicyEffect | None,
) -> None:
    _, token = make_agent(world.dev, granted=[sf.ORG_DESCRIBE])
    rule = db.scalar(
        select(PolicyRule).where(
            PolicyRule.operation_key == sf.ORG_DESCRIBE,
            PolicyRule.environment_kind == world.dev.kind,
        )
    )
    assert rule is not None
    if effect is None:
        db.delete(rule)
    else:
        rule.effect = effect
    db.commit()
    keys = [row["key"] for row in client.get(URL, headers=bearer(token)).json()]
    assert keys == ([sf.ORG_DESCRIBE] if effect is PolicyEffect.REQUIRE_APPROVAL else [])


def test_production_guardrail_hides_non_reads_even_with_allow_rules(
    client: TestClient,
    world: World,
    make_agent: AgentFactory,
    db: Session,
    catalog: OperationCatalog,
) -> None:
    _, token = make_agent(world.prod)
    for rule in db.scalars(
        select(PolicyRule).where(PolicyRule.environment_kind == world.prod.kind)
    ):
        rule.effect = PolicyEffect.ALLOW
    db.commit()
    keys = [row["key"] for row in client.get(URL, headers=bearer(token)).json()]
    assert keys == sorted(spec.key for spec in catalog if spec.risk is RiskClass.READ)


def test_revoked_grants_and_policy_changes_apply_to_the_next_discovery(
    client: TestClient, world: World, make_agent: AgentFactory, db: Session
) -> None:
    agent, token = make_agent(world.dev, granted=[sf.ORG_DESCRIBE])
    assert client.get(URL, headers=bearer(token)).json()
    rule = db.scalar(
        select(PolicyRule).where(
            PolicyRule.operation_key == sf.ORG_DESCRIBE,
            PolicyRule.environment_kind == world.dev.kind,
        )
    )
    assert rule is not None
    rule.effect = PolicyEffect.DENY
    db.commit()
    assert client.get(URL, headers=bearer(token)).json() == []
    rule.effect = PolicyEffect.ALLOW
    db.commit()
    assert client.get(URL, headers=bearer(token)).json()
    agent.granted_operations = []
    db.commit()
    assert client.get(URL, headers=bearer(token)).json() == []


def test_query_parameters_cannot_select_another_target_or_identity(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.prod, granted=[sf.ORG_DESCRIBE, sf.DEPLOY])
    other, _ = make_agent(world.dev)
    response = client.get(
        URL,
        headers=bearer(token),
        params={"environment_id": str(world.dev.id), "agent_id": str(other.id)},
    )
    assert [row["key"] for row in response.json()] == [sf.ORG_DESCRIBE]
