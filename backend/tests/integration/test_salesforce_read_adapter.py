"""Gateway and provisioning checks use a disposable test database and HTTP mocks."""

from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_tower.core.enums import ExecutionStatus
from control_tower.gateway.service import Gateway
from control_tower.persistence.models import AuditEvent, Environment, Execution
from control_tower.platforms.registry import AdapterRegistry
from control_tower.platforms.salesforce import operations as ops
from control_tower.platforms.salesforce.provisioning import register_environment
from tests.conftest import AgentFactory, World
from tests.unit.test_salesforce_read_adapter import Harness


def test_registration_is_audited_idempotent_and_never_contacts_salesforce(db: Session) -> None:
    case = Harness()
    assert register_environment(db, case.connection)
    assert not register_environment(db, case.connection)
    env = db.get(Environment, case.connection.environment_id)
    assert env is not None and env.adapter_key == case.adapter.adapter_key
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.action == "registry.environment_registered"))
    )
    assert len(events) == 1
    assert events[0].details["environment_id"] == str(env.id)
    assert case.calls == []


def test_existing_environment_cannot_be_retargeted(db: Session) -> None:
    case = Harness()
    register_environment(db, case.connection)
    changed = case.connection.model_copy(
        update={"org_id": "00D" + case.connection.environment_id.hex[:12]}
    )
    with pytest.raises(ValueError, match="binding differs"):
        register_environment(db, changed)
    assert db.get(Environment, case.connection.environment_id).external_id == case.connection.org_id
    assert case.calls == []


def test_registration_never_overwrites_another_environment_name(db: Session) -> None:
    first, second = Harness(), Harness()
    register_environment(db, first.connection)
    second_connection = second.connection.model_copy(update={"name": first.connection.name})
    with pytest.raises(ValueError, match="name already exists"):
        register_environment(db, second_connection)
    assert db.get(Environment, second_connection.environment_id) is None


def test_gateway_discovers_reads_and_records_authenticated_results(
    db: Session, world: World, make_agent: AgentFactory, gateway: Gateway, adapters: AdapterRegistry
) -> None:
    case = Harness()
    world.uat.adapter_key = case.adapter.adapter_key
    world.uat.external_id = case.connection.org_id
    case.connection = case.connection.model_copy(
        update={"environment_id": world.uat.id, "kind": world.uat.kind}
    )
    case.target = replace(case.target, id=world.uat.id, kind=world.uat.kind)
    case.adapter = type(case.adapter)([case.connection], transport=case.transport)
    adapters.register(case.adapter)
    db.commit()
    agent, _ = make_agent(world.uat)
    assert {spec.key for spec in gateway.available_operations(agent)} == {
        ops.ORG_DESCRIBE,
        ops.DATA_QUERY,
    }
    result = gateway.submit(
        agent,
        ops.DATA_QUERY,
        {"sobject": "Account", "fields": ["Name"], "limit": 5},
        "salesforce-read-check",
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert result.result["records"] == [{"Name": "Synthetic account"}]
    assert db.scalar(select(func.count()).select_from(Execution)) == 1
    calls = len(case.calls)
    denied = gateway.submit(
        agent,
        ops.DEPLOY,
        {
            "components": [
                {
                    "metadata_type": "ApexClass",
                    "full_name": "BlockedClass",
                    "body": "public class BlockedClass {}",
                }
            ]
        },
        "salesforce-read-refusal",
    )
    assert denied.status is ExecutionStatus.DENIED
    assert len(case.calls) == calls


def test_wrong_authenticated_org_is_recorded_as_failure(
    db: Session, world: World, make_agent: AgentFactory, gateway: Gateway, adapters: AdapterRegistry
) -> None:
    case = Harness()
    world.dev.adapter_key = case.adapter.adapter_key
    world.dev.external_id = case.connection.org_id
    case.connection = case.connection.model_copy(
        update={"environment_id": world.dev.id, "kind": world.dev.kind}
    )
    case.org_id = "00D" + world.dev.id.hex[:12]
    case.adapter = type(case.adapter)([case.connection], transport=case.transport)
    adapters.register(case.adapter)
    db.commit()
    agent, _ = make_agent(world.dev)
    result = gateway.submit(
        agent, ops.DATA_QUERY, {"sobject": "Account", "fields": ["Name"]}, "salesforce-wrong-org"
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error["code"] == "TARGET_MISMATCH"
    assert len(case.calls) == 2


def test_registration_cli_only_provisions_the_configured_database_binding(db: Session) -> None:
    import os
    from contextlib import nullcontext, redirect_stdout
    from io import StringIO
    from unittest.mock import patch

    from control_tower.cli import main

    case = Harness()
    stdout = StringIO()
    with (
        patch.dict(os.environ, {"ACT_SALESFORCE_CONNECTIONS": case.config_json()}),
        patch(
            "control_tower.persistence.session.get_sessionmaker",
            return_value=lambda: nullcontext(db),
        ),
        redirect_stdout(stdout),
    ):
        assert main(["register-salesforce", str(case.connection.environment_id)]) == 0
    assert db.get(Environment, case.connection.environment_id) is not None
    assert case.connection.client_secret.get_secret_value() not in stdout.getvalue()
    assert "No Salesforce request was sent." in stdout.getvalue()
    assert case.calls == []


def test_production_data_query_default_policy_remains_denied(
    db: Session, world: World, make_agent: AgentFactory, gateway: Gateway, adapters: AdapterRegistry
) -> None:
    case = Harness()
    world.prod.adapter_key = case.adapter.adapter_key
    world.prod.external_id = case.connection.org_id
    connection = case.connection.model_copy(
        update={"environment_id": world.prod.id, "kind": world.prod.kind}
    )
    adapters.register(type(case.adapter)([connection], transport=case.transport))
    db.commit()
    agent, _ = make_agent(world.prod)
    assert {spec.key for spec in gateway.available_operations(agent)} == {ops.ORG_DESCRIBE}
    result = gateway.submit(
        agent,
        ops.DATA_QUERY,
        {"sobject": "Account", "fields": ["Name"]},
        "salesforce-prod-data-denial",
    )
    assert result.status is ExecutionStatus.DENIED
    assert case.calls == []
