"""``act`` command line: database migrations, reference data and demo traffic."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from control_tower.settings import get_settings


def alembic_config(database_url: str | None = None) -> Config:
    root = Path(__file__).resolve().parents[2]  # backend/
    ini = root / "alembic.ini"
    if not ini.exists():  # installed without the source tree
        raise SystemExit(f"alembic.ini not found at {ini}")
    config = Config(str(ini))
    if database_url:
        config.attributes["database_url"] = database_url
    return config


def _cmd_migrate(_: argparse.Namespace) -> int:
    command.upgrade(alembic_config(), "head")
    print("Database is at the latest migration.")
    return 0


def _cmd_seed(_: argparse.Namespace) -> int:
    from control_tower.bootstrap import seed
    from control_tower.persistence.session import get_sessionmaker
    from control_tower.platforms.registry import build_catalog

    with get_sessionmaker()() as session:
        report = seed(session, build_catalog(), get_settings())

    if not report.created:
        print("Nothing to do: reference data already exists.")
        return 0
    print(f"Created {len(report.created)} records.")
    if report.operator_username:
        print(f"\nOperator username: {report.operator_username}")
        if report.operator_password:
            print(f"Operator password: {report.operator_password}")
            print("  (generated; shown once. Set ACT_OPERATOR_PASSWORD to choose your own.)")
        else:
            print("Operator password: the value of ACT_OPERATOR_PASSWORD")
    if report.agent_tokens:
        print("\nAgent tokens (shown once; only their hashes are stored):")
        for name, token in report.agent_tokens.items():
            print(f"  {name}: {token}")
    return 0


def _cmd_simulate(_: argparse.Namespace) -> int:
    from control_tower.logging import configure_logging
    from control_tower.persistence.session import get_sessionmaker
    from control_tower.platforms.registry import build_adapters, build_catalog
    from control_tower.simulate import simulate

    configure_logging("WARNING", "console")
    with get_sessionmaker()() as session:
        executions = simulate(session, build_catalog(), build_adapters())
        for e in executions:
            print(
                f"{e.agent.name:<18} {e.operation_key:<34} "
                f"{e.policy_effect.value:<17} {e.status.value:<17} {e.decision_reason.value}"
            )
    print(f"\n{len(executions)} requests sent through the gateway.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="act", description="Agent Control Tower")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=_cmd_migrate)
    sub.add_parser(
        "seed", help="create environments, default policies, demo agents and an operator"
    ).set_defaults(func=_cmd_seed)
    sub.add_parser(
        "simulate", help="send a fixed set of demo tool requests through the gateway"
    ).set_defaults(func=_cmd_simulate)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
