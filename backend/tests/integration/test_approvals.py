import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from control_tower.bootstrap import seed
from control_tower.core.enums import AgentStatus, AuditActorType, EnvironmentKind, PolicyEffect
from control_tower.core.enums import ExecutionStatus as S
from control_tower.core.fingerprint import action_fingerprint
from control_tower.core.operations import OperationCatalog, OperationParams
from control_tower.core.ports import TargetEnvironment
from control_tower.gateway.service import ApprovalBinding, ApprovalError, Gateway
from control_tower.persistence.models import Agent, AuditEvent, Execution, Operator, PolicyRule
from control_tower.platforms.registry import AdapterRegistry
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter
from control_tower.security.tokens import new_agent_token
from control_tower.settings import Settings
from tests.conftest import AgentFactory, World


@pytest.fixture
def waiting(gateway: Gateway, world: World, make_agent: AgentFactory) -> Execution:
    agent, _ = make_agent(world.uat)
    return gateway.submit(
        agent,
        sf.APEX_WRITE,
        {"class_name": "ApprovedClass", "body": "public class ApprovedClass {}"},
        "approval-request",
    )


def binding(execution: Execution) -> ApprovalBinding:
    assert execution.action_hash is not None
    return ApprovalBinding(execution.action_hash, execution.agent_id, execution.environment_id)


def body(execution: Execution) -> dict[str, str]:
    value = binding(execution)
    return {
        "action_hash": value.action_hash,
        "agent_id": str(value.agent_id),
        "environment_id": str(value.environment_id),
    }


def url(execution: Execution, action: str) -> str:
    return f"/api/v1/executions/{execution.id}/{action}"


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_operator_decision_uses_the_stored_action_and_is_audited(
    action: str,
    waiting: Execution,
    operator_client: TestClient,
    mock_adapter: MockSalesforceAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    original = mock_adapter.execute

    def execute(target: TargetEnvironment, key: str, params: OperationParams) -> dict[str, Any]:
        calls.append(params.canonical())
        return original(target, key, params)

    monkeypatch.setattr(mock_adapter, "execute", execute)
    response = operator_client.post(url(waiting, action), json=body(waiting))
    assert response.status_code == 200
    assert response.json()["status"] == ("COMPLETED" if action == "approve" else "REJECTED")
    assert calls == ([waiting.parameters] if action == "approve" else [])
    history = operator_client.get(url(waiting, "audit-events"))
    assert history.status_code == 200
    events = history.json()
    expected = {
        "approval.requested",
        "approval.approved" if action == "approve" else "approval.rejected",
    }
    if action == "approve":
        expected.add("approval.consumed")
    assert {event["action"] for event in events} == expected
    decisions = [event for event in events if event["actor_type"] == "OPERATOR"]
    assert decisions and all(event["actor_id"] for event in decisions)
    assert all(event["details"]["action_hash"] == waiting.action_hash for event in decisions)
    assert operator_client.post(url(waiting, action), json=body(waiting)).status_code == 409
    assert len(calls) == (1 if action == "approve" else 0)


@pytest.mark.parametrize("action", ["approve", "reject", "execute"])
@pytest.mark.parametrize("field", ["action_hash", "agent_id", "environment_id"])
def test_changed_binding_cannot_authorize_a_decision(
    waiting: Execution,
    operator_client: TestClient,
    action: str,
    field: str,
    gateway: Gateway,
    operator: Operator,
) -> None:
    if action == "execute":
        gateway.approve(waiting.id, binding(waiting), operator.id, "binding-approval")
    payload = body(waiting)
    payload[field] = "0" * 64 if field == "action_hash" else str(uuid.uuid4())
    response = operator_client.post(url(waiting, action), json=payload)
    assert response.status_code == 409
    assert waiting.status is (S.APPROVED if action == "execute" else S.PENDING_APPROVAL)
    assert waiting.started_at is None


@pytest.mark.parametrize("action", ["approve", "reject", "execute"])
def test_decisions_reject_resupplied_parameters_and_cross_origin_calls(
    waiting: Execution, operator_client: TestClient, action: str
) -> None:
    payload: dict[str, Any] = body(waiting)
    payload["parameters"] = waiting.parameters
    assert operator_client.post(url(waiting, action), json=payload).status_code == 422
    assert (
        operator_client.post(
            url(waiting, action),
            json=body(waiting),
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )
    assert waiting.status is S.PENDING_APPROVAL


@pytest.mark.parametrize("action", ["approve", "reject", "execute"])
def test_expiry_is_enforced_without_waiting_for_the_worker(
    waiting: Execution,
    gateway: Gateway,
    operator: Operator,
    operator_client: TestClient,
    app: FastAPI,
    action: str,
) -> None:
    if action == "execute":
        gateway.approve(waiting.id, binding(waiting), operator.id, "pre-expiry-approval")
    assert waiting.approval_expires_at is not None
    app.state.clock = lambda: waiting.approval_expires_at
    assert operator_client.post(url(waiting, action), json=body(waiting)).status_code == 409
    assert waiting.status is S.EXPIRED and waiting.started_at is None
    events = operator_client.get(url(waiting, "audit-events")).json()
    expired = [event for event in events if event["action"] == "approval.expired"]
    assert len(expired) == 1 and expired[0]["actor_type"] == "SYSTEM"


@pytest.mark.parametrize(
    "state", [S.REJECTED, S.EXPIRED, S.DENIED, S.EXECUTING, S.COMPLETED, S.FAILED]
)
def test_terminal_or_running_records_cannot_be_approved(
    waiting: Execution, db: Session, operator_client: TestClient, state: S
) -> None:
    waiting.status = state
    db.commit()
    assert operator_client.post(url(waiting, "approve"), json=body(waiting)).status_code == 409
    assert waiting.status is state


@pytest.mark.parametrize("action", ["approve", "execute"])
@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "grant_removed",
        "agent_rebound",
        "production",
        "policy_denied",
        "adapter_missing",
        "unknown_operation",
        "invalid_parameters",
        "credential",
    ],
)
def test_approval_rechecks_current_eligibility(
    waiting: Execution,
    db: Session,
    world: World,
    operator_client: TestClient,
    change: str,
    action: str,
    gateway: Gateway,
    operator: Operator,
) -> None:
    if action == "execute":
        gateway.approve(waiting.id, binding(waiting), operator.id, "eligibility-approval")
    if change == "disabled":
        waiting.agent.status = AgentStatus.DISABLED
    elif change == "grant_removed":
        waiting.agent.granted_operations = []
    elif change == "agent_rebound":
        waiting.agent.environment_id = world.dev.id
    elif change == "production":
        waiting.environment.kind = EnvironmentKind.PRODUCTION
    elif change == "policy_denied":
        rule = db.scalar(
            select(PolicyRule).where(
                PolicyRule.operation_key == waiting.operation_key,
                PolicyRule.environment_kind == EnvironmentKind.TESTING,
            )
        )
        assert rule is not None
        rule.effect = PolicyEffect.DENY
    elif change == "adapter_missing":
        waiting.environment.adapter_key = "missing-adapter"
    elif change == "unknown_operation":
        waiting.operation_key = "salesforce.unknown"
    elif change == "invalid_parameters":
        waiting.parameters = {"class_name": "Invalid"}
    else:
        waiting.parameters = {**waiting.parameters, "body": new_agent_token()}
    if change in {"unknown_operation", "invalid_parameters", "credential"}:
        waiting.action_hash = action_fingerprint(
            agent_id=waiting.agent_id,
            environment_id=waiting.environment_id,
            operation_key=waiting.operation_key,
            parameters=waiting.parameters,
        )
    db.commit()
    assert operator_client.post(url(waiting, action), json=body(waiting)).status_code == 409
    assert waiting.status is (S.APPROVED if action == "execute" else S.PENDING_APPROVAL)
    assert waiting.started_at is None
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "approval.consumed")) is None


def test_execution_requires_a_matching_immutable_approval(
    waiting: Execution,
    gateway: Gateway,
    operator: Operator,
    operator_client: TestClient,
    db: Session,
) -> None:
    waiting.status = S.APPROVED
    db.commit()
    assert operator_client.post(url(waiting, "execute"), json=body(waiting)).status_code == 409
    waiting.status = S.PENDING_APPROVAL
    db.commit()
    gateway.approve(waiting.id, binding(waiting), operator.id, "stored-approval")
    waiting.parameters = {**waiting.parameters, "body": "public class ApprovedClass { }"}
    waiting.action_hash = action_fingerprint(
        agent_id=waiting.agent_id,
        environment_id=waiting.environment_id,
        operation_key=waiting.operation_key,
        parameters=waiting.parameters,
    )
    db.commit()
    assert operator_client.post(url(waiting, "execute"), json=body(waiting)).status_code == 409
    assert waiting.status is S.APPROVED and waiting.started_at is None


def test_stored_parameter_tampering_and_missing_deadline_are_refused(
    waiting: Execution, db: Session, operator_client: TestClient
) -> None:
    waiting.parameters = {**waiting.parameters, "body": "public class ApprovedClass { }"}
    db.commit()
    assert operator_client.post(url(waiting, "approve"), json=body(waiting)).status_code == 409
    waiting.approval_expires_at = None
    db.commit()
    assert operator_client.post(url(waiting, "approve"), json=body(waiting)).status_code == 409
    assert waiting.started_at is None


@pytest.mark.parametrize("action", ["approve", "reject", "execute", "audit-events"])
def test_missing_execution_returns_404(operator_client: TestClient, action: str) -> None:
    path = f"/api/v1/executions/{uuid.uuid4()}/{action}"
    response = (
        operator_client.get(path)
        if action == "audit-events"
        else operator_client.post(
            path,
            json={
                "action_hash": "0" * 64,
                "agent_id": str(uuid.uuid4()),
                "environment_id": str(uuid.uuid4()),
            },
        )
    )
    assert response.status_code == 404


@pytest.mark.parametrize("race", ["two_approvals", "approval_and_execute"])
def test_real_connections_consume_an_approval_at_most_once(
    isolated_engine: Engine, catalog: OperationCatalog, race: str
) -> None:
    calls = 0
    lock = Lock()

    class Adapter(MockSalesforceAdapter):
        def execute(
            self, target: TargetEnvironment, key: str, params: OperationParams
        ) -> dict[str, Any]:
            nonlocal calls
            with lock:
                calls += 1
            with Session(isolated_engine) as observer:
                stored = observer.get(Execution, execution_id)
                assert stored is not None and stored.status is S.EXECUTING
                assert (
                    observer.scalar(
                        select(AuditEvent).where(AuditEvent.action == "approval.consumed")
                    )
                    is not None
                )
            return super().execute(target, key, params)

    adapters = AdapterRegistry()
    adapters.register(Adapter())
    with Session(isolated_engine, expire_on_commit=False) as session:
        seed(session, catalog, Settings(operator_password=None, _env_file=None))  # type: ignore[call-arg]
        agent = session.scalar(select(Agent).where(Agent.name == "codex-uat"))
        operator = session.scalar(select(Operator))
        assert agent is not None and operator is not None
        execution = Gateway(session, catalog, adapters).submit(
            agent,
            sf.DEPLOY,
            {
                "components": [
                    {
                        "metadata_type": "ApexClass",
                        "full_name": "RaceClass",
                        "body": "public class RaceClass {}",
                    }
                ]
            },
            "approval-race",
        )
        execution_id, operator_id, bound = execution.id, operator.id, binding(execution)
    barrier, approved, consumed = Barrier(2), Event(), Event()

    def call(index: int) -> str:
        with Session(isolated_engine, expire_on_commit=False) as session:
            gateway = Gateway(session, catalog, adapters)
            barrier.wait(5)
            try:
                if race == "two_approvals" or index == 0:
                    gateway.approve(execution_id, bound, operator_id, f"race-{index}")
                    if race == "approval_and_execute":
                        approved.set()
                        assert consumed.wait(5)
                else:
                    assert approved.wait(5)
                return gateway.execute_approved(
                    execution_id, bound, operator_id, f"race-{index}"
                ).status.value
            except ApprovalError:
                return "CONFLICT"
            finally:
                if index == 1:
                    consumed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(call, [0, 1]))
    assert sorted(outcomes) == ["COMPLETED", "CONFLICT"] and calls == 1
    with Session(isolated_engine) as session:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.execution_id == execution_id))
        )
        assert sum(event.action == "approval.approved" for event in events) == 1
        consumed_events = [event for event in events if event.action == "approval.consumed"]
        assert len(consumed_events) == 1
        assert consumed_events[0].actor_type is AuditActorType.OPERATOR


def test_operator_can_resume_an_unused_approval_once(
    waiting: Execution, gateway: Gateway, operator: Operator, operator_client: TestClient
) -> None:
    gateway.approve(waiting.id, binding(waiting), operator.id, "resume-approval")
    assert waiting.status is S.APPROVED
    assert (
        operator_client.post(url(waiting, "execute"), json=body(waiting)).json()["status"]
        == "COMPLETED"
    )
    assert operator_client.post(url(waiting, "execute"), json=body(waiting)).status_code == 409


def test_changing_the_deadline_does_not_extend_the_original_request(
    waiting: Execution, db: Session, operator_client: TestClient
) -> None:
    assert waiting.approval_expires_at is not None
    waiting.approval_expires_at += timedelta(seconds=1)
    db.commit()
    assert operator_client.post(url(waiting, "approve"), json=body(waiting)).status_code == 409
    assert waiting.status is S.PENDING_APPROVAL and waiting.started_at is None
