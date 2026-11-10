from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_tower.core.enums import AuditActorType, EnvironmentKind, PolicyEffect
from control_tower.persistence.models import AuditEvent, Operator, PolicyRule
from control_tower.platforms.salesforce import operations as sf
from tests.conftest import World

API = "/api/v1"


def rule_for(db: Session, operation: str, kind: EnvironmentKind) -> PolicyRule:
    rule = db.scalar(
        select(PolicyRule).where(
            PolicyRule.operation_key == operation, PolicyRule.environment_kind == kind
        )
    )
    assert rule is not None
    return rule


def policy_events(db: Session) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == "policy.updated")))


@pytest.mark.parametrize("kind", list(EnvironmentKind))
@pytest.mark.parametrize("effect", list(PolicyEffect))
def test_operator_changes_policy_and_records_the_exact_change(
    operator_client: TestClient,
    operator: Operator,
    world: World,
    db: Session,
    kind: EnvironmentKind,
    effect: PolicyEffect,
) -> None:
    before = rule_for(db, sf.ORG_DESCRIBE, kind).effect.value
    response = operator_client.put(
        f"{API}/policies/{sf.ORG_DESCRIBE}/{kind.value}",
        json={"effect": effect.value},
        headers={"X-Correlation-ID": "policy-edit-test"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "environment_kind": kind.value,
        "configured": effect.value,
        "effective": effect.value,
        "reason": "RULE_MATCH",
    }
    rule = rule_for(db, sf.ORG_DESCRIBE, kind)
    assert rule.effect is effect and rule.updated_by == operator.username
    events = policy_events(db)
    assert len(events) == 1
    event = events[0]
    assert event.actor_type is AuditActorType.OPERATOR and event.actor_id == operator.id
    assert event.correlation_id == "policy-edit-test" and event.occurred_at == rule.updated_at
    assert event.details == {
        "operation_key": sf.ORG_DESCRIBE,
        "environment_kind": kind.value,
        "previous_effect": before,
        "effect": effect.value,
    }
    matrix = operator_client.get(f"{API}/policies").json()
    row = next(row for row in matrix["rows"] if row["operation"]["key"] == sf.ORG_DESCRIBE)
    cell = next(cell for cell in row["cells"] if cell["environment_kind"] == kind.value)
    assert cell == response.json()


def test_operator_can_create_a_missing_rule(
    operator_client: TestClient, world: World, db: Session
) -> None:
    db.delete(rule_for(db, sf.ORG_DESCRIBE, EnvironmentKind.DEVELOPMENT))
    db.commit()
    response = operator_client.put(
        f"{API}/policies/{sf.ORG_DESCRIBE}/DEVELOPMENT",
        json={"effect": "REQUIRE_APPROVAL"},
    )
    assert response.status_code == 200
    assert (
        rule_for(db, sf.ORG_DESCRIBE, EnvironmentKind.DEVELOPMENT).effect
        is PolicyEffect.REQUIRE_APPROVAL
    )
    assert policy_events(db)[0].details["previous_effect"] is None


@pytest.mark.parametrize(
    "operation", [sf.DEPLOY, sf.APEX_WRITE, sf.TESTS_RUN, sf.PERMISSION_SET_MODIFY]
)
@pytest.mark.parametrize("effect", list(PolicyEffect))
def test_production_guardrail_cannot_be_edited_even_to_deny(
    operator_client: TestClient,
    world: World,
    db: Session,
    operation: str,
    effect: PolicyEffect,
) -> None:
    response = operator_client.put(
        f"{API}/policies/{operation}/PRODUCTION", json={"effect": effect.value}
    )
    assert response.status_code == 403
    assert rule_for(db, operation, EnvironmentKind.PRODUCTION).effect is PolicyEffect.DENY
    assert policy_events(db) == []


@pytest.mark.parametrize(
    ("operation", "kind", "body"),
    [
        ("missing.operation", "DEVELOPMENT", {"effect": "ALLOW"}),
        (sf.ORG_DESCRIBE, "UNKNOWN", {"effect": "ALLOW"}),
        (sf.ORG_DESCRIBE, "DEVELOPMENT", {"effect": "allow"}),
        (sf.ORG_DESCRIBE, "DEVELOPMENT", {"effect": True}),
        (sf.ORG_DESCRIBE, "DEVELOPMENT", {"effect": "ALLOW", "operation": sf.DEPLOY}),
    ],
)
def test_invalid_policy_edits_do_not_change_configuration(
    operator_client: TestClient,
    world: World,
    db: Session,
    operation: str,
    kind: str,
    body: dict[str, object],
) -> None:
    response = operator_client.put(f"{API}/policies/{operation}/{kind}", json=body)
    assert response.status_code == 422
    assert rule_for(db, sf.ORG_DESCRIBE, EnvironmentKind.DEVELOPMENT).effect is PolicyEffect.ALLOW
    assert db.scalar(select(func.count()).select_from(PolicyRule)) == 24
    assert policy_events(db) == []


def test_cross_origin_policy_write_is_refused(
    operator_client: TestClient, world: World, db: Session
) -> None:
    response = operator_client.put(
        f"{API}/policies/{sf.ORG_DESCRIBE}/DEVELOPMENT",
        json={"effect": "DENY"},
        headers={"Origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert rule_for(db, sf.ORG_DESCRIBE, EnvironmentKind.DEVELOPMENT).effect is PolicyEffect.ALLOW
    assert policy_events(db) == []


def test_concurrent_insert_conflict_does_not_create_an_audit_event(
    operator_client: TestClient, world: World, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_scalar = db.scalar

    def stale_read(statement: Any, *args: Any, **kwargs: Any) -> Any:
        columns = getattr(statement, "column_descriptions", [])
        if columns and columns[0].get("entity") is PolicyRule:
            return None
        return real_scalar(statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(db, "scalar", stale_read)
        response = operator_client.put(
            f"{API}/policies/{sf.ORG_DESCRIBE}/DEVELOPMENT", json={"effect": "DENY"}
        )
    assert response.status_code == 409
    assert rule_for(db, sf.ORG_DESCRIBE, EnvironmentKind.DEVELOPMENT).effect is PolicyEffect.ALLOW
    assert policy_events(db) == []
