import json
from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from control_tower import cli
from control_tower.bootstrap import seed
from control_tower.cli import alembic_config
from control_tower.clock import utcnow
from control_tower.core.enums import AuditActorType
from control_tower.core.enums import ExecutionStatus as S
from control_tower.core.operations import OperationCatalog, OperationParams
from control_tower.core.ports import TargetEnvironment
from control_tower.gateway import maintenance
from control_tower.gateway.maintenance import MaintenanceResult, run_maintenance
from control_tower.gateway.service import Gateway
from control_tower.persistence import session as persistence_session
from control_tower.persistence.models import Agent, AuditEvent, Execution
from control_tower.platforms.registry import AdapterRegistry
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter
from control_tower.security.tokens import new_agent_token
from control_tower.settings import Settings
from tests.conftest import AgentFactory, World, bearer


def pending(gateway: Gateway, world: World, make_agent: AgentFactory) -> Execution:
    agent, _ = make_agent(world.uat)
    return gateway.submit(
        agent,
        sf.APEX_WRITE,
        {"class_name": "MaintenanceClass", "body": "public class MaintenanceClass {}"},
        "maintenance-request",
    )


@pytest.mark.parametrize("state", [S.PENDING_APPROVAL, S.APPROVED])
def test_deadline_expires_once_and_never_runs_the_action(
    gateway: Gateway, world: World, make_agent: AgentFactory, db: Session, state: S
) -> None:
    execution = pending(gateway, world, make_agent)
    execution.status = state
    db.commit()
    deadline = execution.approval_expires_at
    assert deadline is not None
    assert (
        run_maintenance(
            db, now=deadline - timedelta(microseconds=1), stale_after_seconds=300
        ).expired
        == 0
    )
    assert run_maintenance(db, now=deadline, stale_after_seconds=300).expired == 1
    db.refresh(execution)
    assert execution.status is S.EXPIRED and execution.finished_at == deadline
    assert execution.started_at is None and execution.result is None and execution.error is None
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "approval.expired"))
    assert event is not None and event.actor_type is AuditActorType.SYSTEM
    assert event.execution_id == execution.id and event.correlation_id == execution.correlation_id
    assert event.details["action_hash"] == execution.action_hash
    assert run_maintenance(db, now=deadline, stale_after_seconds=300) == MaintenanceResult(0, 0)


def test_stale_execution_is_marked_unknown_once_without_guessing_the_result(
    gateway: Gateway, world: World, make_agent: AgentFactory, db: Session
) -> None:
    execution = pending(gateway, world, make_agent)
    now = utcnow()
    execution.status = S.EXECUTING
    execution.started_at = now - timedelta(seconds=301)
    execution.approval_expires_at = now - timedelta(seconds=1)
    db.commit()
    result = run_maintenance(db, now=now, stale_after_seconds=300)
    assert result == MaintenanceResult(0, 1)
    db.refresh(execution)
    assert execution.status is S.EXECUTING and execution.reconciliation_required_at == now
    assert execution.finished_at is None and execution.result is None and execution.error is None
    event = db.scalar(
        select(AuditEvent).where(AuditEvent.action == "execution.reconciliation_required")
    )
    assert event is not None and event.details == {"outcome": "UNKNOWN"}
    assert run_maintenance(db, now=now, stale_after_seconds=300) == MaintenanceResult(0, 0)


@pytest.mark.parametrize("state", [S.DENIED, S.REJECTED, S.EXPIRED, S.COMPLETED, S.FAILED])
def test_terminal_records_are_not_changed_by_maintenance(
    gateway: Gateway, world: World, make_agent: AgentFactory, db: Session, state: S
) -> None:
    execution = pending(gateway, world, make_agent)
    execution.status = state
    execution.started_at = utcnow() - timedelta(days=1)
    db.commit()
    assert run_maintenance(
        db, now=utcnow() + timedelta(days=1), stale_after_seconds=1
    ) == MaintenanceResult(0, 0)
    db.refresh(execution)
    assert execution.status is state and execution.reconciliation_required_at is None


def test_recent_executions_are_not_flagged(
    gateway: Gateway, world: World, make_agent: AgentFactory, db: Session
) -> None:
    execution = pending(gateway, world, make_agent)
    now = utcnow()
    execution.status = S.EXECUTING
    execution.started_at = now
    db.commit()
    assert run_maintenance(db, now=now, stale_after_seconds=300) == MaintenanceResult(0, 0)
    assert execution.reconciliation_required_at is None


def test_http_gateway_uses_the_configured_deadline(
    app: FastAPI, client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    now = utcnow()
    app.state.clock = lambda: now
    app.state.settings.approval_ttl_seconds = 37
    _agent, token = make_agent(world.uat)
    response = client.post(
        "/api/v1/gateway/tool-requests",
        headers=bearer(token),
        json={
            "operation": sf.APEX_WRITE,
            "parameters": {"class_name": "TimedClass", "body": "public class TimedClass {}"},
        },
    )
    assert response.status_code == 202
    assert datetime.fromisoformat(response.json()["approval_expires_at"]) == now + timedelta(
        seconds=37
    )
    assert response.json()["reconciliation_required_at"] is None


@pytest.mark.parametrize("state", [S.PENDING_APPROVAL, S.APPROVED])
def test_migration_backfills_legacy_deadlines(
    isolated_engine: Engine, catalog: OperationCatalog, adapters: AdapterRegistry, state: S
) -> None:
    now = utcnow() - timedelta(hours=1)
    with Session(isolated_engine, expire_on_commit=False) as session:
        seed(session, catalog, Settings(operator_password=None, _env_file=None))  # type: ignore[call-arg]
        agent = session.scalar(select(Agent).where(Agent.name == "codex-uat"))
        assert agent is not None
        execution = Gateway(session, catalog, adapters, lambda: now).submit(
            agent,
            sf.DEPLOY,
            {
                "components": [
                    {
                        "metadata_type": "ApexClass",
                        "full_name": "LegacyClass",
                        "body": "public class LegacyClass {}",
                    }
                ]
            },
            "legacy-request",
        )
        execution.status = state
        session.commit()
        execution_id = execution.id
    config = alembic_config(isolated_engine.url.render_as_string(hide_password=False))
    config.attributes["configure_logging"] = False
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    with Session(isolated_engine) as session:
        execution = session.get(Execution, execution_id)
        assert execution is not None and execution.status is state
        assert execution.approval_expires_at == now + timedelta(seconds=300)


def test_cli_simulation_uses_the_configured_deadline(
    isolated_engine: Engine,
    catalog: OperationCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(isolated_engine) as session:
        seed(session, catalog, Settings(operator_password=None, _env_file=None))  # type: ignore[call-arg]
    settings = Settings(approval_ttl_seconds=37, _env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    factory = sessionmaker(bind=isolated_engine, expire_on_commit=False)
    monkeypatch.setattr(persistence_session, "get_sessionmaker", lambda: factory)
    assert cli.main(["simulate"]) == 0
    assert "16 requests" in capsys.readouterr().out
    with Session(isolated_engine) as session:
        waiting = list(
            session.scalars(select(Execution).where(Execution.status == S.PENDING_APPROVAL))
        )
        assert len(waiting) == 2
        assert all(e.approval_expires_at == e.decided_at + timedelta(seconds=37) for e in waiting)


def test_locked_records_are_skipped_until_available(
    isolated_engine: Engine, catalog: OperationCatalog, adapters: AdapterRegistry
) -> None:
    now = utcnow()
    with Session(isolated_engine, expire_on_commit=False) as session:
        seed(session, catalog, Settings(operator_password=None, _env_file=None))  # type: ignore[call-arg]
        agent = session.scalar(select(Agent).where(Agent.name == "codex-uat"))
        assert agent is not None
        execution = Gateway(session, catalog, adapters, lambda: now).submit(
            agent,
            sf.DEPLOY,
            {
                "components": [
                    {
                        "metadata_type": "ApexClass",
                        "full_name": "LockedClass",
                        "body": "public class LockedClass {}",
                    }
                ]
            },
            "locked-request",
        )
        execution_id = execution.id
    now += timedelta(seconds=301)
    with Session(isolated_engine) as blocker, Session(isolated_engine) as worker:
        blocker.execute(
            select(Execution).where(Execution.id == execution_id).with_for_update(of=Execution)
        ).scalar_one()
        assert run_maintenance(worker, now=now, stale_after_seconds=300) == MaintenanceResult(0, 0)
        blocker.rollback()
        assert run_maintenance(worker, now=now, stale_after_seconds=300) == MaintenanceResult(1, 0)


def test_adapter_response_clears_a_marker_written_by_another_session(
    isolated_engine: Engine, catalog: OperationCatalog
) -> None:
    now = utcnow()

    class Adapter(MockSalesforceAdapter):
        def execute(
            self, target: TargetEnvironment, operation_key: str, params: OperationParams
        ) -> dict[str, Any]:
            nonlocal now
            now += timedelta(seconds=301)
            with Session(isolated_engine) as worker:
                assert (
                    run_maintenance(
                        worker, now=now, stale_after_seconds=300
                    ).reconciliation_required
                    == 1
                )
            return super().execute(target, operation_key, params)

    adapters = AdapterRegistry()
    adapters.register(Adapter())
    with Session(isolated_engine, expire_on_commit=False) as session:
        seed(session, catalog, Settings(operator_password=None, _env_file=None))  # type: ignore[call-arg]
        agent = session.scalar(select(Agent).where(Agent.name == "claude-code-dev"))
        assert agent is not None
        execution = Gateway(session, catalog, adapters, lambda: now).submit(
            agent, sf.ORG_DESCRIBE, {}, "late-completion"
        )
        session.refresh(execution)
        assert execution.status is S.COMPLETED and execution.result is not None
        assert execution.reconciliation_required_at is None


@pytest.mark.parametrize(
    "field",
    ["approval_ttl_seconds", "maintenance_interval_seconds", "execution_stale_after_seconds"],
)
def test_runtime_governance_intervals_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0}, _env_file=None)  # type: ignore[arg-type, call-arg]


def test_worker_recovers_from_errors_and_stops_with_the_app(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovered = Event()
    calls = 0
    token = new_agent_token()
    recorded: list[tuple[str, dict[str, Any]]] = []

    def capture(event: str, **values: Any) -> None:
        recorded.append((event, values))

    monkeypatch.setattr(maintenance, "log", SimpleNamespace(error=capture))

    def tick(*args: Any, **kwargs: Any) -> MaintenanceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(token)
        recovered.set()
        return MaintenanceResult(0, 0)

    monkeypatch.setattr(maintenance, "run_maintenance", tick)
    app.state.settings.maintenance_enabled = True
    app.state.settings.maintenance_interval_seconds = 1
    with TestClient(app):
        assert recovered.wait(5)
        worker = app.state.maintenance_thread
        assert worker.is_alive()
    assert not worker.is_alive()
    assert recorded and token not in json.dumps(recorded)


def test_disabled_maintenance_does_not_start_a_worker(app: FastAPI) -> None:
    app.state.settings.maintenance_enabled = False
    with TestClient(app):
        assert not hasattr(app.state, "maintenance_thread")


def test_maintenance_respects_its_batch_size(
    gateway: Gateway, world: World, make_agent: AgentFactory, db: Session
) -> None:
    requests = [pending(gateway, world, make_agent) for _ in range(2)]
    now = utcnow() + timedelta(seconds=601)
    assert run_maintenance(db, now=now, stale_after_seconds=300, batch_size=1).expired == 1
    assert run_maintenance(db, now=now, stale_after_seconds=300, batch_size=1).expired == 1
    assert run_maintenance(db, now=now, stale_after_seconds=300, batch_size=1).expired == 0
    assert all(db.get(Execution, request.id).status is S.EXPIRED for request in requests)
