"""Management API used by the dashboard."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from control_tower.gateway.service import Gateway
from control_tower.persistence.models import Agent
from control_tower.platforms.salesforce import operations as sf
from tests.conftest import AgentFactory, World, bearer

API = "/api/v1"
DEPLOY_PARAMS = {
    "components": [
        {"metadata_type": "ApexClass", "full_name": "Billing", "body": "public class Billing {}"}
    ]
}


def traffic(gateway: Gateway, world: World, make_agent: AgentFactory) -> dict[str, Agent]:
    dev, _ = make_agent(world.dev, name="alpha-dev")
    uat, _ = make_agent(world.uat, name="bravo-uat")
    prod, _ = make_agent(world.prod, name="charlie-prod")
    gateway.submit(dev, sf.ORG_DESCRIBE, {}, "corr-traffic-01")  # COMPLETED
    gateway.submit(dev, sf.TESTS_RUN, {}, "corr-traffic-02")  # COMPLETED
    gateway.submit(
        dev,
        sf.METADATA_READ,
        {"metadata_type": "ApexClass", "full_name": "Nope"},
        "corr-traffic-03",
    )  # FAILED
    gateway.submit(uat, sf.DEPLOY, DEPLOY_PARAMS, "corr-traffic-04")  # PENDING_APPROVAL
    gateway.submit(prod, sf.DEPLOY, DEPLOY_PARAMS, "corr-traffic-05")  # DENIED
    gateway.submit(prod, "bogus.op", {}, "corr-traffic-06")  # DENIED
    return {"dev": dev, "uat": uat, "prod": prod}


def test_overview_counts_come_from_recorded_executions(
    operator_client: TestClient, gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    empty = operator_client.get(f"{API}/overview").json()
    assert empty == {
        "agents_total": 0,
        "agents_active": 0,
        "executions_total": 0,
        "pending_approvals": 0,
        "failed_actions": 0,
        "policy_violations": 0,
    }
    agents = traffic(gateway, world, make_agent)
    operator_client.patch(f"{API}/agents/{agents['prod'].id}", json={"status": "DISABLED"})

    assert operator_client.get(f"{API}/overview").json() == {
        "agents_total": 3,
        "agents_active": 2,
        "executions_total": 6,
        "pending_approvals": 1,
        "failed_actions": 1,
        "policy_violations": 2,
    }


def test_execution_list_is_newest_first_and_paginated(
    operator_client: TestClient, gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    traffic(gateway, world, make_agent)
    page = operator_client.get(f"{API}/executions", params={"limit": 4}).json()
    assert (page["total"], page["limit"], page["offset"]) == (6, 4, 0)
    assert [e["correlation_id"] for e in page["items"]] == [
        f"corr-traffic-0{n}" for n in (6, 5, 4, 3)
    ]
    rest = operator_client.get(f"{API}/executions", params={"limit": 4, "offset": 4}).json()
    assert [e["correlation_id"] for e in rest["items"]] == ["corr-traffic-02", "corr-traffic-01"]
    first = page["items"][2]
    assert first["agent"]["name"] == "bravo-uat"
    assert first["environment"]["kind"] == "TESTING"
    assert first["operation_title"] == "Deploy metadata"
    assert "parameters" not in first, "payloads are only in the detail view"


def test_execution_filters(
    operator_client: TestClient, gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agents = traffic(gateway, world, make_agent)

    def ids(**params: object) -> list[str]:
        response = operator_client.get(f"{API}/executions", params=params)  # type: ignore[arg-type]
        assert response.status_code == 200, response.text
        return sorted(e["correlation_id"][-2:] for e in response.json()["items"])

    assert ids(status="DENIED") == ["05", "06"]
    assert ids(status=["FAILED", "PENDING_APPROVAL"]) == ["03", "04"]
    assert ids(agent_id=str(agents["dev"].id)) == ["01", "02", "03"]
    assert ids(environment_id=str(world.prod.id)) == ["05", "06"]
    assert ids(operation=sf.DEPLOY) == ["04", "05"]
    assert ids(q="bravo") == ["04"]
    assert ids(q="TRAFFIC-03") == ["03"]
    assert ids(q="tests.run") == ["02"]
    assert ids(q="100%") == [], "wildcards in the search term are literal"
    assert ids(status="DENIED", q="bogus") == ["06"]


def test_search_wildcards_are_escaped(
    operator_client: TestClient, gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    traffic(gateway, world, make_agent)
    for term in ("%", "c_rr", "\\"):
        response = operator_client.get(f"{API}/executions", params={"q": term})
        assert response.status_code == 200
        assert response.json()["total"] == 0, term


def test_invalid_filters_are_rejected(operator_client: TestClient) -> None:
    for params in ({"status": "DONE"}, {"limit": 101}, {"offset": -1}, {"agent_id": "abc"}):
        assert operator_client.get(f"{API}/executions", params=params).status_code == 422


def test_execution_detail_shows_the_complete_record(
    operator_client: TestClient, gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.uat)
    execution = gateway.submit(agent, sf.DEPLOY, DEPLOY_PARAMS, "corr-detail-01")

    body = operator_client.get(f"{API}/executions/{execution.id}").json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["action_hash"] == execution.action_hash
    assert body["parameters"]["components"][0]["full_name"] == "Billing"
    assert body["parameters"]["test_level"] == "RunLocalTests"
    assert body["decision_detail"].startswith("Policy for 'salesforce.deploy' on TESTING")
    assert body["result"] is None and body["error"] is None

    assert operator_client.get(f"{API}/executions/{uuid.uuid4()}").status_code == 404


def test_agents_are_listed_without_credentials(
    operator_client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    _, token = make_agent(world.dev, name="alpha-dev")
    response = operator_client.get(f"{API}/agents")
    [agent] = response.json()
    assert agent["name"] == "alpha-dev"
    assert agent["environment"]["name"] == "dev-env" and agent["environment"]["is_mock"] is True
    assert "token_hash" not in agent and token not in response.text
    assert agent["token_hint"].startswith("act_agt_") and len(agent["token_hint"]) < 16


def test_register_agent_issues_a_working_token_once(
    operator_client: TestClient, client: TestClient, world: World
) -> None:
    response = operator_client.post(
        f"{API}/agents",
        json={
            "name": "codex-dev",
            "provider": "CODEX",
            "environment_id": str(world.dev.id),
            "granted_operations": [sf.ORG_DESCRIBE, sf.ORG_DESCRIBE, sf.METADATA_READ],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["agent"]["granted_operations"] == [sf.METADATA_READ, sf.ORG_DESCRIBE]
    token = created["token"]

    assert token not in operator_client.get(f"{API}/agents/{created['agent']['id']}").text

    allowed = client.post(
        f"{API}/gateway/tool-requests", json={"operation": sf.ORG_DESCRIBE}, headers=bearer(token)
    )
    assert allowed.json()["status"] == "COMPLETED"
    not_granted = client.post(
        f"{API}/gateway/tool-requests", json={"operation": sf.TESTS_RUN}, headers=bearer(token)
    )
    assert not_granted.json()["decision_reason"] == "OPERATION_NOT_GRANTED"


def test_register_agent_validation(operator_client: TestClient, world: World) -> None:
    def create(**overrides: object) -> int:
        body = {
            "name": "codex-dev",
            "provider": "CODEX",
            "environment_id": str(world.dev.id),
            "granted_operations": [sf.ORG_DESCRIBE],
            **overrides,
        }
        return operator_client.post(f"{API}/agents", json=body).status_code

    assert create(granted_operations=["salesforce.everything"]) == 422
    assert create(environment_id=str(uuid.uuid4())) == 422
    assert create(name="Not A Slug") == 422
    assert create(provider="GEMINI") == 422
    assert create(token="act_agt_chosen-by-caller") == 422
    assert create() == 201
    assert create() == 409


def test_disabling_an_agent_takes_effect_on_its_next_request(
    operator_client: TestClient, client: TestClient, world: World, make_agent: AgentFactory
) -> None:
    agent, token = make_agent(world.dev)
    url = f"{API}/gateway/tool-requests"

    assert (
        client.post(url, json={"operation": sf.ORG_DESCRIBE}, headers=bearer(token)).json()[
            "status"
        ]
        == "COMPLETED"
    )
    patched = operator_client.patch(f"{API}/agents/{agent.id}", json={"status": "DISABLED"})
    assert patched.json()["status"] == "DISABLED"
    assert (
        client.post(url, json={"operation": sf.ORG_DESCRIBE}, headers=bearer(token)).json()[
            "decision_reason"
        ]
        == "AGENT_DISABLED"
    )


def test_operations_catalogue_exposes_parameter_schemas(operator_client: TestClient) -> None:
    operations = {o["key"]: o for o in operator_client.get(f"{API}/operations").json()}
    assert len(operations) == 8
    deploy = operations[sf.DEPLOY]
    assert deploy["risk"] == "DEPLOY" and deploy["platform"] == "salesforce"
    assert deploy["parameters_schema"]["additionalProperties"] is False
    assert "components" in deploy["parameters_schema"]["required"]


def test_policy_matrix_reports_what_the_gateway_enforces(
    operator_client: TestClient, db: Session, world: World
) -> None:
    from sqlalchemy import select

    from control_tower.core.enums import EnvironmentKind, PolicyEffect
    from control_tower.persistence.models import PolicyRule

    # Misconfigure on purpose: someone sets production deployments to ALLOW.
    rule = db.scalar(
        select(PolicyRule).where(
            PolicyRule.operation_key == sf.DEPLOY,
            PolicyRule.environment_kind == EnvironmentKind.PRODUCTION,
        )
    )
    assert rule is not None
    rule.effect = PolicyEffect.ALLOW
    db.commit()

    matrix = operator_client.get(f"{API}/policies").json()
    assert matrix["environment_kinds"] == ["DEVELOPMENT", "TESTING", "PRODUCTION"]
    rows = {row["operation"]["key"]: row["cells"] for row in matrix["rows"]}
    assert len(rows) == 8

    dev, uat, prod = rows[sf.DEPLOY]
    assert (dev["effective"], uat["effective"]) == ("REQUIRE_APPROVAL", "REQUIRE_APPROVAL")
    assert prod == {
        "environment_kind": "PRODUCTION",
        "configured": "ALLOW",
        "effective": "DENY",
        "reason": "PRODUCTION_GUARDRAIL",
    }
    assert [c["effective"] for c in rows[sf.METADATA_READ]] == ["ALLOW", "ALLOW", "ALLOW"]
    assert [c["effective"] for c in rows[sf.DATA_QUERY]] == ["ALLOW", "ALLOW", "DENY"]


def test_environments_are_listed(operator_client: TestClient, world: World) -> None:
    envs = operator_client.get(f"{API}/environments").json()
    assert [(e["name"], e["kind"]) for e in envs] == [
        ("dev-env", "DEVELOPMENT"),
        ("prod-env", "PRODUCTION"),
        ("uat-env", "TESTING"),
    ]
