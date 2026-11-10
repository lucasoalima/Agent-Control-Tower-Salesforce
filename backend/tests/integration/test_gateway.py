"""Gateway behaviour against a real database and the mock Salesforce adapter.

The mock organisation is inspected directly to prove what did and did not
happen: an execution record saying "denied" is a claim, an unchanged
organisation is evidence.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_tower.core.enums import (
    AgentStatus,
    DecisionReason,
    ExecutionStatus,
    PolicyEffect,
)
from control_tower.core.fingerprint import action_fingerprint
from control_tower.core.ports import AdapterError, TargetEnvironment
from control_tower.gateway.service import Gateway
from control_tower.persistence.models import Environment, Execution
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import MockOrg, MockSalesforceAdapter
from tests.conftest import AgentFactory, World

SF_TOKEN = "00D5g00000ABCDE!AQEAQPw4x.Zt8Q2y7mN1bVc3Xz5Lk9Jh7Gf4Ds2Aq"
DEPLOY_PARAMS = {
    "components": [
        {"metadata_type": "ApexClass", "full_name": "Billing", "body": "public class Billing {}"}
    ]
}


def org_of(adapter: MockSalesforceAdapter, env: Environment) -> MockOrg:
    return adapter.org(TargetEnvironment(env.id, env.name, env.platform, env.kind, env.external_id))


def stored(db: Session, execution_id: uuid.UUID) -> Execution:
    db.expire_all()
    row = db.get(Execution, execution_id)
    assert row is not None
    return row


# -- ALLOW -----------------------------------------------------------------


def test_allowed_read_executes_and_is_fully_recorded(
    gateway: Gateway, db: Session, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    params = {"metadata_type": "ApexClass", "full_name": "AccountService"}

    execution = gateway.submit(agent, sf.METADATA_READ, params, "corr-allowed-1")

    row = stored(db, execution.id)
    assert row.status is ExecutionStatus.COMPLETED
    assert row.policy_effect is PolicyEffect.ALLOW
    assert row.decision_reason is DecisionReason.RULE_MATCH
    assert row.correlation_id == "corr-allowed-1"
    assert row.agent_id == agent.id and row.environment_id == world.dev.id
    assert row.parameters == params
    assert row.action_hash == action_fingerprint(
        agent_id=agent.id,
        environment_id=world.dev.id,
        operation_key=sf.METADATA_READ,
        parameters=params,
    )
    assert row.result is not None and "class AccountService" in row.result["body"]
    assert row.error is None
    assert row.requested_at <= row.decided_at <= row.started_at <= row.finished_at  # type: ignore[operator]
    assert agent.last_seen_at == row.requested_at


def test_stored_parameters_are_canonical_with_defaults(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    execution = gateway.submit(
        agent, sf.DATA_QUERY, {"fields": ["Id"], "sobject": "Account"}, "corr-canon-1"
    )
    assert execution.parameters == {"sobject": "Account", "fields": ["Id"], "limit": 50}


def test_adapter_failure_is_recorded_as_failed(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    execution = gateway.submit(
        agent,
        sf.METADATA_READ,
        {"metadata_type": "ApexClass", "full_name": "Missing"},
        "corr-failed-1",
    )
    assert execution.status is ExecutionStatus.FAILED
    assert execution.result is None
    assert execution.error is not None and execution.error["code"] == "NOT_FOUND"
    assert execution.finished_at is not None


def test_adapter_crash_is_contained_and_does_not_leak_internals(
    gateway: Gateway, world: World, make_agent: AgentFactory, mock_adapter: MockSalesforceAdapter
) -> None:
    agent, _ = make_agent(world.dev)
    mock_adapter.faults[sf.ORG_DESCRIBE] = RuntimeError(f"connection string with {SF_TOKEN}")

    execution = gateway.submit(agent, sf.ORG_DESCRIBE, {}, "corr-crash-1")

    assert execution.status is ExecutionStatus.FAILED
    assert execution.error == {
        "code": "ADAPTER_CRASH",
        "message": "The adapter raised an unexpected error.",
        "details": {"exception_type": "RuntimeError"},
    }


def test_secrets_in_adapter_output_are_redacted_before_storage(
    gateway: Gateway, world: World, make_agent: AgentFactory, mock_adapter: MockSalesforceAdapter
) -> None:
    agent, _ = make_agent(world.dev)
    mock_adapter.faults[sf.ORG_DESCRIBE] = AdapterError(
        "AUTH", f"session {SF_TOKEN} expired", details={"access_token": "abc", "hint": "re-auth"}
    )
    execution = gateway.submit(agent, sf.ORG_DESCRIBE, {}, "corr-redact-1")
    assert execution.error == {
        "code": "AUTH",
        "message": "session [REDACTED] expired",
        "details": {"access_token": "[REDACTED]", "hint": "re-auth"},
    }


# -- REQUIRE_APPROVAL ---------------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "params"),
    [
        (sf.DEPLOY, DEPLOY_PARAMS),
        (sf.APEX_WRITE, {"class_name": "Billing", "body": "public class Billing {}"}),
        (
            sf.PERMISSION_SET_MODIFY,
            {
                "permission_set": "Sales_Operations",
                "object_permissions": [{"sobject": "Account", "allow_delete": True}],
            },
        ),
    ],
)
def test_sensitive_operation_waits_for_approval_and_nothing_is_executed(
    gateway: Gateway,
    world: World,
    make_agent: AgentFactory,
    mock_adapter: MockSalesforceAdapter,
    operation: str,
    params: dict[str, object],
) -> None:
    """Scenario A: an agent asks to change a sandbox without approval."""
    agent, _ = make_agent(world.uat)
    org = org_of(mock_adapter, world.uat)
    before = (dict(org.components), dict(org.object_permissions))

    execution = gateway.submit(agent, operation, params, "corr-pending-1")

    assert execution.status is ExecutionStatus.PENDING_APPROVAL
    assert execution.policy_effect is PolicyEffect.REQUIRE_APPROVAL
    assert execution.action_hash is not None
    assert execution.started_at is None and execution.finished_at is None
    assert execution.result is None and execution.error is None
    # Evidence, not claims: the organisation is untouched.
    assert (org.components, org.object_permissions) == before
    assert org.deployments == []


# -- DENY --------------------------------------------------------------------


def assert_denied(execution: Execution, reason: DecisionReason) -> None:
    assert execution.status is ExecutionStatus.DENIED
    assert execution.policy_effect is PolicyEffect.DENY
    assert execution.decision_reason is reason
    assert execution.action_hash is None
    assert execution.started_at is None and execution.finished_at is None
    assert execution.result is None


def test_production_deployment_is_denied_and_does_not_happen(
    gateway: Gateway, world: World, make_agent: AgentFactory, mock_adapter: MockSalesforceAdapter
) -> None:
    agent, _ = make_agent(world.prod)
    execution = gateway.submit(agent, sf.DEPLOY, DEPLOY_PARAMS, "corr-prod-1")
    assert_denied(execution, DecisionReason.PRODUCTION_GUARDRAIL)
    org = org_of(mock_adapter, world.prod)
    assert org.deployments == [] and ("ApexClass", "Billing") not in org.components


def test_operation_not_granted_to_the_agent_is_denied(
    gateway: Gateway, world: World, make_agent: AgentFactory, mock_adapter: MockSalesforceAdapter
) -> None:
    """Evaluation test 3: permission escalation."""
    agent, _ = make_agent(world.uat, granted=[sf.METADATA_READ])
    execution = gateway.submit(
        agent,
        sf.PERMISSION_SET_MODIFY,
        {
            "permission_set": "Sales_Operations",
            "object_permissions": [{"sobject": "Account", "allow_delete": True}],
        },
        "corr-escalate-1",
    )
    assert_denied(execution, DecisionReason.OPERATION_NOT_GRANTED)
    assert org_of(mock_adapter, world.uat).object_permissions == {}


def test_disabled_agent_is_denied_even_for_reads(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev, status=AgentStatus.DISABLED)
    assert_denied(
        gateway.submit(agent, sf.ORG_DESCRIBE, {}, "corr-disabled-1"),
        DecisionReason.AGENT_DISABLED,
    )


def test_disabled_agent_learns_nothing_about_which_operations_exist(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    """The disabled check runs first, so the answer is the same for real and made-up operations."""
    agent, _ = make_agent(world.dev, status=AgentStatus.DISABLED)
    assert_denied(
        gateway.submit(agent, "salesforce.does.not.exist", {}, "corr-disabled-2"),
        DecisionReason.AGENT_DISABLED,
    )


def test_unknown_operation_is_denied_and_recorded(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    execution = gateway.submit(
        agent, "salesforce.shell.exec", {"command": "sf org delete"}, "corr-unknown-1"
    )
    assert_denied(execution, DecisionReason.UNKNOWN_OPERATION)
    assert execution.operation_key == "salesforce.shell.exec"
    assert execution.parameters == {"command": "sf org delete"}


def test_operation_without_a_policy_rule_is_denied_by_default(
    gateway: Gateway, db: Session, world: World, make_agent: AgentFactory
) -> None:
    from control_tower.persistence.models import PolicyRule

    for rule in db.scalars(select(PolicyRule).where(PolicyRule.operation_key == sf.ORG_DESCRIBE)):
        db.delete(rule)
    db.commit()
    agent, _ = make_agent(world.dev)
    assert_denied(
        gateway.submit(agent, sf.ORG_DESCRIBE, {}, "corr-norule-1"),
        DecisionReason.NO_MATCHING_RULE,
    )


def test_environment_bound_to_an_unregistered_adapter_is_denied(
    gateway: Gateway, db: Session, world: World, make_agent: AgentFactory
) -> None:
    world.dev.adapter_key = "salesforce"  # the real adapter is not registered in this build
    db.commit()
    agent, _ = make_agent(world.dev)
    assert_denied(
        gateway.submit(agent, sf.ORG_DESCRIBE, {}, "corr-noadapter-1"),
        DecisionReason.UNSUPPORTED_BY_ADAPTER,
    )


@pytest.mark.parametrize(
    "params",
    [
        {"metadata_type": "ApexClass", "full_name": "../../etc/passwd"},
        {"metadata_type": "ApexClass", "full_name": "A", "target_org": "production"},
        {"metadata_type": "ApexClass"},
        "not-an-object",
        None,
    ],
)
def test_invalid_parameters_are_denied_before_reaching_the_adapter(
    gateway: Gateway,
    world: World,
    make_agent: AgentFactory,
    mock_adapter: MockSalesforceAdapter,
    params: object,
) -> None:
    agent, _ = make_agent(world.dev)
    mock_adapter.faults[sf.METADATA_READ] = AssertionError("adapter must not be called")
    execution = gateway.submit(agent, sf.METADATA_READ, params, "corr-invalid-1")
    assert_denied(execution, DecisionReason.INVALID_PARAMETERS)
    assert isinstance(execution.parameters, dict)


def test_validation_messages_do_not_echo_the_submitted_value(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    execution = gateway.submit(
        agent,
        sf.METADATA_READ,
        {"metadata_type": "ApexClass", "full_name": "very-secret-looking-value!"},
        "corr-echo-1",
    )
    assert "full_name" in execution.decision_detail
    assert "very-secret-looking-value" not in execution.decision_detail


def test_credential_in_a_rejected_payload_is_never_stored(
    gateway: Gateway, db: Session, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    leaked = gateway.submit(
        agent,
        sf.METADATA_READ,
        {"metadata_type": "ApexClass", "full_name": "A", "session": SF_TOKEN},
        "corr-leak-0",
    )
    assert_denied(leaked, DecisionReason.INVALID_PARAMETERS)
    row = stored(db, leaked.id)
    assert SF_TOKEN not in str(row.parameters)
    assert row.parameters["session"] == "[REDACTED]"


def test_credential_inside_valid_source_is_refused(
    gateway: Gateway,
    db: Session,
    world: World,
    make_agent: AgentFactory,
    mock_adapter: MockSalesforceAdapter,
) -> None:
    # Make the write ALLOWed so that only the secret scan stands in the way.
    from control_tower.persistence.models import PolicyRule

    rule = db.scalar(
        select(PolicyRule).where(
            PolicyRule.operation_key == sf.APEX_WRITE,
            PolicyRule.environment_kind == world.dev.kind,
        )
    )
    assert rule is not None
    rule.effect = PolicyEffect.ALLOW
    db.commit()

    agent, _ = make_agent(world.dev)
    body = f"public class Leak {{ String t = '{SF_TOKEN}'; }}"
    execution = gateway.submit(
        agent, sf.APEX_WRITE, {"class_name": "Leak", "body": body}, "corr-leak-1"
    )

    assert_denied(execution, DecisionReason.CREDENTIAL_IN_PAYLOAD)
    row = stored(db, execution.id)
    assert SF_TOKEN not in str(row.parameters)
    assert row.parameters["body"] == "public class Leak { String t = '[REDACTED]'; }"
    assert ("ApexClass", "Leak") not in org_of(mock_adapter, world.dev).components


def test_oversized_refused_payload_is_not_stored(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.dev)
    execution = gateway.submit(agent, "nope", {"blob": "x" * 50_000}, "corr-big-1")
    assert_denied(execution, DecisionReason.UNKNOWN_OPERATION)
    assert list(execution.parameters) == ["_note"]


# -- invariants ----------------------------------------------------------------


def test_every_request_leaves_exactly_one_record(
    gateway: Gateway, db: Session, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.uat)
    requests: list[tuple[str, object]] = [
        (sf.ORG_DESCRIBE, {}),
        (sf.DEPLOY, DEPLOY_PARAMS),
        ("unknown.op", {}),
        (sf.METADATA_READ, {"bad": True}),
        (sf.METADATA_READ, {"metadata_type": "ApexClass", "full_name": "Missing"}),
    ]
    ids = [
        gateway.submit(agent, op, params, f"corr-count-{i}").id
        for i, (op, params) in enumerate(requests)
    ]
    assert len(set(ids)) == len(requests)
    assert db.scalar(select(func.count()).select_from(Execution)) == len(requests)


def test_identical_requests_share_a_fingerprint_but_not_a_record(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    agent, _ = make_agent(world.uat)
    first = gateway.submit(agent, sf.DEPLOY, DEPLOY_PARAMS, "corr-dup-1")
    second = gateway.submit(agent, sf.DEPLOY, DEPLOY_PARAMS, "corr-dup-2")
    assert first.id != second.id
    assert first.action_hash == second.action_hash


def test_same_payload_from_another_agent_or_environment_has_a_different_fingerprint(
    gateway: Gateway, world: World, make_agent: AgentFactory
) -> None:
    a, _ = make_agent(world.uat)
    b, _ = make_agent(world.uat)
    c, _ = make_agent(world.dev)
    hashes = {
        gateway.submit(x, sf.DEPLOY, DEPLOY_PARAMS, "corr-fp-1").action_hash for x in (a, b, c)
    }
    assert len(hashes) == 3
