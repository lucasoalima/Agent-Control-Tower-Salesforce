from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from control_tower.cli import alembic_config
from control_tower.persistence.models import Base
from tests.conftest import TEST_DATABASE_URL


def _config():  # type: ignore[no-untyped-def]
    config = alembic_config(TEST_DATABASE_URL)
    config.attributes["configure_logging"] = False
    return config


def test_migrations_produce_exactly_the_model_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_expected_tables_exist(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {
        "agents",
        "environments",
        "executions",
        "operator_sessions",
        "operators",
        "policy_rules",
    } <= tables


def test_downgrade_and_upgrade_round_trip(engine: Engine) -> None:
    command.downgrade(_config(), "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    command.upgrade(_config(), "head")
    assert "executions" in inspect(engine).get_table_names()
