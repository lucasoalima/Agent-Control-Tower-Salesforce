import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from control_tower.core.enums import AgentStatus
from control_tower.persistence.models import Execution
from control_tower.platforms.salesforce import operations as sf
from tests.conftest import AgentFactory, World, bearer

URL = "/api/v1/gateway/tool-requests"
DEPLOY = {
    "operation": sf.DEPLOY,
    "parameters": {
        "components": [
            {
                "metadata_type": "ApexClass",
                "full_name": "Billing",
                "body": "public class Billing {}",
            }
        ]
    },
}


def test_allowed_request_returns_the_result(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev)
    response = client.post(URL, json={"operation": sf.ORG_DESCRIBE}, headers=bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["policy_effect"] == "ALLOW"
    assert body["result"]["org_id"] == "MOCK-DEV"
    assert body["error"] is None
    assert len(body["action_hash"]) == 64


def test_request_needing_approval_returns_202_and_no_result(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.uat)
    response = client.post(URL, json=DEPLOY, headers=bearer(token))
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["policy_effect"] == "REQUIRE_APPROVAL"
    assert body["result"] is None


def test_denied_request_is_a_processed_request(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.prod)
    response = client.post(URL, json=DEPLOY, headers=bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DENIED"
    assert body["decision_reason"] == "PRODUCTION_GUARDRAIL"
    assert body["action_hash"] is None


def test_disabled_agent_is_recorded_and_denied_not_ignored(
    client: TestClient, db: Session, world: World, make_agent: AgentFactory
) -> None:
    agent, token = make_agent(world.dev, status=AgentStatus.DISABLED)
    response = client.post(URL, json={"operation": sf.ORG_DESCRIBE}, headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["decision_reason"] == "AGENT_DISABLED"
    assert db.get(Execution, uuid.UUID(response.json()["execution_id"])).agent_id == agent.id  # type: ignore[union-attr]


def test_correlation_id_is_propagated_to_header_body_and_record(
    client: TestClient, db: Session, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev)
    response = client.post(
        URL,
        json={"operation": sf.ORG_DESCRIBE},
        headers={**bearer(token), "X-Correlation-ID": "trace-abc-12345"},
    )
    assert response.headers["x-correlation-id"] == "trace-abc-12345"
    assert response.json()["correlation_id"] == "trace-abc-12345"
    row = db.get(Execution, uuid.UUID(response.json()["execution_id"]))
    assert row is not None and row.correlation_id == "trace-abc-12345"


def test_unsafe_correlation_id_is_replaced(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev)
    response = client.post(
        URL,
        json={"operation": sf.ORG_DESCRIBE},
        headers={**bearer(token), "X-Correlation-ID": 'x"; DROP TABLE executions; --'},
    )
    generated = response.headers["x-correlation-id"]
    assert len(generated) == 32 and generated.isalnum()
    assert response.json()["correlation_id"] == generated


def test_request_envelope_is_strict(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev)
    response = client.post(
        URL,
        json={"operation": sf.ORG_DESCRIBE, "parameters": {}, "environment": "production"},
        headers=bearer(token),
    )
    assert response.status_code == 422, "an agent cannot choose its target environment"


def test_oversized_body_is_rejected(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev)
    response = client.post(
        URL,
        json={
            "operation": sf.APEX_WRITE,
            "parameters": {"class_name": "A", "body": "x" * 1_100_000},
        },
        headers=bearer(token),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_agent_can_poll_its_own_request_but_not_another_agents(
    client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token_a = make_agent(world.uat)
    _, token_b = make_agent(world.uat)
    execution_id = client.post(URL, json=DEPLOY, headers=bearer(token_a)).json()["execution_id"]

    own = client.get(f"{URL}/{execution_id}", headers=bearer(token_a))
    assert own.status_code == 200 and own.json()["status"] == "PENDING_APPROVAL"

    other = client.get(f"{URL}/{execution_id}", headers=bearer(token_b))
    missing = client.get(f"{URL}/{uuid.uuid4()}", headers=bearer(token_b))
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json(), "existence of other agents' requests must not leak"
