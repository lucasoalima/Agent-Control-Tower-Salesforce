"""Register an explicitly configured environment without contacting Salesforce."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.clock import utcnow
from control_tower.core.enums import AuditActorType
from control_tower.persistence.audit import record_event
from control_tower.persistence.models import Environment
from control_tower.platforms.salesforce.read_adapter import (
    SalesforceConnection,
    SalesforceReadAdapter,
)


def register_environment(session: Session, connection: SalesforceConnection) -> bool:
    expected = {
        "name": connection.name,
        "kind": connection.kind,
        "platform": SalesforceReadAdapter.platform,
        "adapter_key": SalesforceReadAdapter.adapter_key,
        "external_id": connection.org_id,
    }
    existing = session.get(Environment, connection.environment_id)
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in expected.items()):
            raise ValueError("Existing environment binding differs; no row was changed.")
        return False
    if (
        session.scalar(select(Environment.id).where(Environment.name == connection.name))
        is not None
    ):
        raise ValueError("Environment name already exists; no row was changed.")
    now = utcnow()
    session.add(Environment(id=connection.environment_id, created_at=now, **expected))
    session.flush()
    record_event(
        session,
        "registry.environment_registered",
        actor_type=AuditActorType.SYSTEM,
        actor_id=None,
        correlation_id="salesforce-environment-" + connection.environment_id.hex,
        occurred_at=now,
        details={
            "environment_id": str(connection.environment_id),
            "adapter_key": SalesforceReadAdapter.adapter_key,
            "environment_kind": connection.kind.value,
        },
    )
    session.commit()
    return True
