import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from control_tower.bootstrap import seed
from control_tower.clock import utcnow
from control_tower.core.enums import AuditActorType
from control_tower.core.operations import OperationCatalog
from control_tower.core.redaction import REDACTED
from control_tower.gateway.service import Gateway
from control_tower.persistence.audit import record_event
from control_tower.persistence.models import AuditEvent, Operator
from control_tower.platforms.salesforce import operations as sf
from control_tower.security.tokens import new_agent_token
from control_tower.settings import Settings
from tests.conftest import OPERATOR_USERNAME, AgentFactory, World

API = "/api/v1"


def test_event_retains_identity_time_and_correlation_and_redacts_details(db: Session) -> None:
    actor_id, execution_id = uuid.uuid4(), uuid.uuid4()
    now = utcnow()
    token = new_agent_token()
    event = record_event(
        db,
        "test.recorded",
        actor_type=AuditActorType.OPERATOR,
        actor_id=actor_id,
        correlation_id="audit-redaction-test",
        occurred_at=now,
        execution_id=execution_id,
        details={"token": token, "message": token, "safe": "kept"},
    )
    db.flush()
    db.expire(event)
    assert event.actor_type is AuditActorType.OPERATOR
    assert event.actor_id == actor_id and event.execution_id == execution_id
    assert event.occurred_at == now and event.correlation_id == "audit-redaction-test"
    assert event.details == {"token": REDACTED, "message": REDACTED, "safe": "kept"}
    assert token not in json.dumps(event.details)


def test_event_is_rolled_back_with_its_transaction(db: Session) -> None:
    with db.begin_nested() as transaction:
        event = record_event(
            db,
            "test.rollback",
            actor_type=AuditActorType.SYSTEM,
            actor_id=None,
            correlation_id="audit-rollback-test",
            occurred_at=utcnow(),
        )
        event_id = event.id
        db.flush()
        transaction.rollback()
    assert db.get(AuditEvent, event_id) is None


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'changed'",
        "DELETE FROM audit_events",
        "TRUNCATE TABLE audit_events",
    ],
)
def test_database_refuses_audit_mutation(db: Session, statement: str) -> None:
    event = record_event(
        db,
        "test.immutable",
        actor_type=AuditActorType.SYSTEM,
        actor_id=None,
        correlation_id="audit-immutable-test",
        occurred_at=utcnow(),
    )
    db.flush()
    with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
        db.execute(text(statement))
    db.expire(event)
    assert event.action == "test.immutable"


def test_successful_login_and_logout_have_operator_audit_events(
    operator_client: TestClient, operator: Operator, db: Session
) -> None:
    login_event = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login"))
    assert login_event is not None
    assert login_event.actor_id == operator.id
    assert login_event.actor_type is AuditActorType.OPERATOR
    assert login_event.details == {}
    response = operator_client.post(
        f"{API}/auth/logout", headers={"X-Correlation-ID": "audit-logout-test"}
    )
    assert response.status_code == 204
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.logout"))
    assert event is not None and event.actor_id == operator.id
    assert event.correlation_id == "audit-logout-test"
    assert event.details == {}
    assert operator_client.get(f"{API}/auth/me").status_code == 401


def test_failed_and_throttled_logins_do_not_store_supplied_credentials(
    client: TestClient, operator: Operator, db: Session
) -> None:
    password = new_agent_token()
    for _ in range(6):
        response = client.post(
            f"{API}/auth/login",
            json={"username": OPERATOR_USERNAME, "password": password},
            headers={"X-Correlation-ID": "audit-failed-login-test"},
        )
    assert response.status_code == 429
    events = list(db.scalars(select(AuditEvent)))
    assert sum(event.action == "auth.login_failed" for event in events) == 5
    assert sum(event.action == "auth.login_throttled" for event in events) == 1
    assert all(event.actor_type is AuditActorType.ANONYMOUS for event in events)
    assert all(event.actor_id is None and event.details == {} for event in events)
    assert all(event.correlation_id == "audit-failed-login-test" for event in events)
    assert password not in json.dumps([event.details for event in events])


def test_registration_and_status_are_audited_without_the_issued_token(
    operator_client: TestClient, operator: Operator, world: World, db: Session
) -> None:
    body = {
        "name": "audit-agent",
        "provider": "CODEX",
        "environment_id": str(world.dev.id),
        "granted_operations": [sf.ORG_DESCRIBE],
    }
    response = operator_client.post(
        f"{API}/agents", json=body, headers={"X-Correlation-ID": "audit-register-test"}
    )
    assert response.status_code == 201
    created = response.json()
    agent_id, token = created["agent"]["id"], created["token"]
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "registry.agent_created"))
    assert event is not None and event.actor_id == operator.id
    assert event.actor_type is AuditActorType.OPERATOR
    assert event.correlation_id == "audit-register-test"
    assert event.details == {
        "agent_id": agent_id,
        "environment_id": str(world.dev.id),
        "granted_operations": [sf.ORG_DESCRIBE],
    }
    assert token not in json.dumps(event.details)
    assert operator_client.post(f"{API}/agents", json=body).status_code == 409
    count = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "registry.agent_created")
    )
    assert count == 1
    response = operator_client.patch(
        f"{API}/agents/{agent_id}",
        json={"status": "DISABLED"},
        headers={"X-Correlation-ID": "audit-status-test"},
    )
    assert response.status_code == 200
    status_event = db.scalar(
        select(AuditEvent).where(AuditEvent.action == "registry.agent_status_changed")
    )
    assert status_event is not None and status_event.actor_id == operator.id
    assert status_event.correlation_id == "audit-status-test"
    assert status_event.details == {"agent_id": agent_id, "status": "DISABLED"}


def test_seed_audits_only_new_policies_and_agents(db: Session, catalog: OperationCatalog) -> None:
    settings = Settings(operator_password=None, _env_file=None)  # type: ignore[call-arg]
    report = seed(db, catalog, settings)
    events = list(db.scalars(select(AuditEvent)))
    assert sum(event.action == "policy.created" for event in events) == 24
    assert sum(event.action == "registry.agent_created" for event in events) == 3
    assert len(events) == 27
    serialized = json.dumps([event.details for event in events])
    assert all(token not in serialized for token in report.agent_tokens.values())
    assert report.operator_password is not None and report.operator_password not in serialized
    assert all(event.actor_type is AuditActorType.SYSTEM for event in events)
    assert all(event.actor_id is None for event in events)
    assert len({event.correlation_id for event in events}) == 1
    seed(db, catalog, settings)
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 27


def test_pending_approval_audit_binds_the_stored_action(
    gateway: Gateway, world: World, make_agent: AgentFactory, db: Session
) -> None:
    agent, _ = make_agent(world.uat)
    execution = gateway.submit(
        agent,
        sf.DEPLOY,
        {
            "components": [
                {
                    "metadata_type": "ApexClass",
                    "full_name": "AuditClass",
                    "body": "public class AuditClass {}",
                }
            ]
        },
        "audit-pending-test",
    )
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "approval.requested"))
    assert event is not None
    assert event.actor_type is AuditActorType.AGENT and event.actor_id == agent.id
    assert event.execution_id == execution.id and event.correlation_id == execution.correlation_id
    assert event.details == {
        "action_hash": execution.action_hash,
        "expires_at": execution.approval_expires_at.isoformat(),
        "agent_id": str(agent.id),
        "environment_id": str(world.uat.id),
        "operation_key": sf.DEPLOY,
    }
