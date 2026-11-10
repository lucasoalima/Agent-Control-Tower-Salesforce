"""Shared fixtures.

Integration tests run against a real PostgreSQL database whose schema is built
by the Alembic migrations, so the migrations themselves are exercised by every
integration test. Each test runs inside a transaction that is rolled back.

If no database is reachable the integration tests are skipped, unless
``ACT_REQUIRE_DB=1`` is set (as it is in CI), in which case they fail.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from control_tower.api.app import create_app
from control_tower.bootstrap import DEFAULT_POLICIES
from control_tower.cli import alembic_config
from control_tower.clock import utcnow
from control_tower.core.enums import AgentProvider, AgentStatus, EnvironmentKind
from control_tower.core.operations import OperationCatalog
from control_tower.gateway.service import Gateway
from control_tower.persistence.models import Agent, Environment, Operator, PolicyRule
from control_tower.persistence.session import session_scope
from control_tower.platforms.registry import AdapterRegistry, build_catalog
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter
from control_tower.security.passwords import hash_password
from control_tower.security.tokens import hash_token, new_agent_token, token_hint
from control_tower.settings import Settings

TEST_DATABASE_URL = os.environ.get(
    "ACT_TEST_DATABASE_URL", "postgresql+psycopg://act:act_dev_password@localhost:5432/act_test"
)
OPERATOR_USERNAME = "operator"
OPERATOR_PASSWORD = "correct-horse-battery"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/integration/" in str(item.path).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        message = f"PostgreSQL is not reachable at ACT_TEST_DATABASE_URL: {exc.orig}"
        if os.environ.get("ACT_REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(message)

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = alembic_config(TEST_DATABASE_URL)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session whose work, including commits, is discarded after the test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def catalog() -> OperationCatalog:
    return build_catalog()


@pytest.fixture
def mock_adapter() -> MockSalesforceAdapter:
    return MockSalesforceAdapter()


@pytest.fixture
def adapters(mock_adapter: MockSalesforceAdapter) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(mock_adapter)
    return registry


@pytest.fixture
def gateway(db: Session, catalog: OperationCatalog, adapters: AdapterRegistry) -> Gateway:
    return Gateway(db, catalog, adapters)


@dataclass
class World:
    """Reference data most integration tests need."""

    dev: Environment
    uat: Environment
    prod: Environment


@pytest.fixture
def world(db: Session) -> World:
    now = utcnow()
    envs = {}
    for key, kind in (
        ("dev", EnvironmentKind.DEVELOPMENT),
        ("uat", EnvironmentKind.TESTING),
        ("prod", EnvironmentKind.PRODUCTION),
    ):
        envs[key] = Environment(
            name=f"{key}-env",
            platform="salesforce",
            kind=kind,
            adapter_key=MockSalesforceAdapter.adapter_key,
            external_id=f"MOCK-{key.upper()}",
            created_at=now,
        )
        db.add(envs[key])
    for operation_key, effects in DEFAULT_POLICIES.items():
        for kind, effect in zip(EnvironmentKind, effects, strict=True):
            db.add(
                PolicyRule(
                    operation_key=operation_key,
                    environment_kind=kind,
                    effect=effect,
                    updated_at=now,
                    updated_by="test",
                )
            )
    db.commit()
    return World(**envs)


AgentFactory = Callable[..., tuple[Agent, str]]


@pytest.fixture
def make_agent(db: Session, catalog: OperationCatalog) -> AgentFactory:
    def _make(
        environment: Environment,
        *,
        granted: list[str] | None = None,
        status: AgentStatus = AgentStatus.ACTIVE,
        name: str | None = None,
    ) -> tuple[Agent, str]:
        token = new_agent_token()
        agent = Agent(
            name=name or f"agent-{uuid.uuid4().hex[:8]}",
            provider=AgentProvider.CLAUDE_CODE,
            status=status,
            environment_id=environment.id,
            granted_operations=sorted(granted if granted is not None else catalog.keys()),
            token_hash=hash_token(token),
            token_hint=token_hint(token),
            created_at=utcnow(),
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        return agent, token

    return _make


@pytest.fixture
def operator(db: Session) -> Operator:
    op = Operator(
        username=OPERATOR_USERNAME,
        password_hash=hash_password(OPERATOR_PASSWORD),
        created_at=utcnow(),
    )
    db.add(op)
    db.commit()
    return op


@pytest.fixture
def app(db: Session, catalog: OperationCatalog, adapters: AdapterRegistry) -> FastAPI:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        log_level="CRITICAL",
        allowed_origins="http://dashboard.test",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings, catalog, adapters)

    def _session() -> Iterator[Session]:
        yield db

    app.dependency_overrides[session_scope] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """An unauthenticated client."""
    return TestClient(app)


@pytest.fixture
def operator_client(app: FastAPI, operator: Operator) -> TestClient:
    """A client signed in as the operator."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login", json={"username": OPERATOR_USERNAME, "password": OPERATOR_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_engine(engine: Engine) -> Iterator[Engine]:
    """A disposable migrated schema for tests using independent connections."""
    assert engine.url.database == "act_test"
    schema = "act_test_" + uuid.uuid4().hex
    assert schema.startswith("act_test_") and schema != "public"
    with engine.begin() as connection:
        raw = connection.connection.driver_connection
        assert raw is not None
        with raw.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    url = engine.url.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=1000"})
    isolated = create_engine(url, pool_pre_ping=True)
    try:
        config = alembic_config(url.render_as_string(hide_password=False))
        config.attributes["configure_logging"] = False
        command.upgrade(config, "head")
        yield isolated
    finally:
        isolated.dispose()
        with engine.begin() as connection:
            raw = connection.connection.driver_connection
            assert raw is not None
            with raw.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
