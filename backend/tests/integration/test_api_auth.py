"""Who can call what. The separation between operators and agents is the
property the rest of the security model rests on."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.clock import utcnow
from control_tower.persistence.models import Operator, OperatorSession
from control_tower.platforms.salesforce import operations as sf
from tests.conftest import OPERATOR_PASSWORD, OPERATOR_USERNAME, AgentFactory, World, bearer

API = "/api/v1"
OPERATOR_ENDPOINTS = [
    ("GET", "/auth/me"),
    ("GET", "/overview"),
    ("GET", "/agents"),
    ("POST", "/agents"),
    ("GET", "/agents/00000000-0000-0000-0000-000000000000"),
    ("PATCH", "/agents/00000000-0000-0000-0000-000000000000"),
    ("GET", "/environments"),
    ("GET", "/operations"),
    ("GET", "/policies"),
    ("PUT", "/policies/{operation_key}/{environment_kind}"),
    ("GET", "/executions"),
    ("GET", "/executions/00000000-0000-0000-0000-000000000000"),
    ("POST", "/auth/logout"),
]
AGENT_ENDPOINTS = [
    ("POST", "/gateway/tool-requests"),
    ("GET", "/gateway/tool-requests/00000000-0000-0000-0000-000000000000"),
]


def login(client: TestClient, password: str = OPERATOR_PASSWORD, **kwargs: object):  # type: ignore[no-untyped-def]
    return client.post(
        f"{API}/auth/login", json={"username": OPERATOR_USERNAME, "password": password}, **kwargs
    )


@pytest.mark.parametrize(("method", "path"), OPERATOR_ENDPOINTS + AGENT_ENDPOINTS)
def test_every_endpoint_requires_authentication(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, API + path, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_openapi_lists_no_unprotected_endpoint_we_did_not_expect(app: FastAPI) -> None:
    """Guards against a new route being added without an auth dependency."""
    public = {"/healthz", "/readyz", "/auth/login"}
    documented = {
        (method.upper(), path.removeprefix(API))
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    }
    covered = set(OPERATOR_ENDPOINTS + AGENT_ENDPOINTS)
    uncovered = (
        {(m, p) for m, p in documented if p not in public}
        - {(m, p.replace("00000000-0000-0000-0000-000000000000", "{agent_id}")) for m, p in covered}
        - {
            (m, p.replace("00000000-0000-0000-0000-000000000000", "{execution_id}"))
            for m, p in covered
        }
    )
    assert uncovered == set(), f"endpoints missing from the auth test matrix: {uncovered}"


@pytest.mark.parametrize(("method", "path"), OPERATOR_ENDPOINTS)
def test_agent_token_cannot_use_the_management_api(
    client: TestClient, world: World, make_agent: AgentFactory, method: str, path: str
) -> None:
    _, token = make_agent(world.dev)
    response = client.request(method, API + path, json={}, headers=bearer(token))
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), AGENT_ENDPOINTS)
def test_operator_session_cannot_use_the_gateway(
    operator_client: TestClient, method: str, path: str
) -> None:
    response = operator_client.request(
        method, API + path, json={"operation": sf.ORG_DESCRIBE, "parameters": {}}
    )
    assert response.status_code == 401


def test_login_sets_a_hardened_cookie_and_stores_only_a_hash(
    client: TestClient, operator: Operator, db: Session
) -> None:
    response = login(client)
    assert response.status_code == 200
    assert response.json() == {"id": str(operator.id), "username": OPERATOR_USERNAME}

    cookie = response.headers["set-cookie"]
    assert "act_session=act_ses_" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie

    token = client.cookies["act_session"]
    session_row = db.scalar(select(OperatorSession))
    assert session_row is not None
    assert session_row.token_hash != token and len(session_row.token_hash) == 64
    assert client.get(f"{API}/auth/me").status_code == 200


@pytest.mark.parametrize(
    ("username", "password"),
    [(OPERATOR_USERNAME, "wrong-password-123"), ("nobody", OPERATOR_PASSWORD)],
)
def test_wrong_credentials_give_the_same_answer(
    client: TestClient, operator: Operator, username: str, password: str
) -> None:
    response = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Incorrect username or password."
    assert "set-cookie" not in response.headers


def test_inactive_operator_cannot_sign_in(
    client: TestClient, operator: Operator, db: Session
) -> None:
    operator.is_active = False
    db.commit()
    assert login(client).status_code == 401


def test_repeated_failures_are_throttled(client: TestClient, operator: Operator) -> None:
    for _ in range(5):
        assert login(client, "wrong-password-123").status_code == 401
    assert login(client, "wrong-password-123").status_code == 429
    assert login(client).status_code == 429, "the correct password is throttled too"


def test_logout_revokes_the_session_server_side(operator_client: TestClient) -> None:
    token = operator_client.cookies["act_session"]
    assert operator_client.post(f"{API}/auth/logout").status_code == 204
    operator_client.cookies.set("act_session", token)  # replay the old cookie
    assert operator_client.get(f"{API}/auth/me").status_code == 401


def test_expired_session_is_rejected(app: FastAPI, operator_client: TestClient) -> None:
    app.state.clock = lambda: utcnow() + timedelta(hours=13)
    assert operator_client.get(f"{API}/auth/me").status_code == 401


def test_forged_session_cookie_is_rejected(client: TestClient, operator: Operator) -> None:
    client.cookies.set("act_session", "act_ses_" + "A" * 43)
    assert client.get(f"{API}/auth/me").status_code == 401


def test_cross_origin_state_change_is_refused(operator_client: TestClient) -> None:
    evil = operator_client.post(f"{API}/auth/logout", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert operator_client.get(f"{API}/auth/me").status_code == 200, "session must survive"
    ok = operator_client.post(f"{API}/auth/logout", headers={"Origin": "http://dashboard.test"})
    assert ok.status_code == 204


def test_cross_origin_login_is_refused(client: TestClient, operator: Operator) -> None:
    assert login(client, headers={"Origin": "https://evil.example"}).status_code == 403


def test_validation_errors_do_not_echo_the_password(client: TestClient) -> None:
    response = client.post(
        f"{API}/auth/login", json={"username": "operator", "password": "p" * 300}
    )
    assert response.status_code == 422
    assert "ppp" not in response.text
    assert response.json()["error"]["details"][0]["field"] == "body.password"


@pytest.mark.parametrize(
    "header",
    ["", "Basic abc", "Bearer", "Bearer not-an-agent-token", "Bearer act_agt_" + "x" * 43],
)
def test_bad_agent_credentials_are_rejected(client: TestClient, header: str) -> None:
    response = client.post(
        f"{API}/gateway/tool-requests",
        json={"operation": sf.ORG_DESCRIBE},
        headers={"Authorization": header} if header else {},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_endpoints_are_public(client: TestClient) -> None:
    assert client.get(f"{API}/healthz").json() == {"status": "ok"}
    assert client.get(f"{API}/readyz").json() == {"status": "ok"}
