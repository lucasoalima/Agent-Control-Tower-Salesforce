"""Append an audit event to the caller's transaction without committing it."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from control_tower.core.enums import AuditActorType
from control_tower.core.redaction import redact
from control_tower.persistence.models import AuditEvent


def record_event(
    session: Session,
    action: str,
    *,
    actor_type: AuditActorType,
    actor_id: uuid.UUID | None,
    correlation_id: str,
    occurred_at: datetime,
    execution_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=uuid.uuid4(),
        occurred_at=occurred_at,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        execution_id=execution_id,
        details=redact(details or {})[0],
    )
    session.add(event)
    return event
