import itertools

import pytest

from control_tower.core.enums import (
    DecisionReason,
    EnvironmentKind,
    PolicyEffect,
    RiskClass,
)
from control_tower.core.policy import PolicyRequest, decide

OP = "salesforce.deploy"


def request(
    *,
    active: bool = True,
    granted: bool = True,
    risk: RiskClass = RiskClass.DEPLOY,
    kind: EnvironmentKind = EnvironmentKind.TESTING,
) -> PolicyRequest:
    return PolicyRequest(
        agent_active=active,
        agent_granted_operations=frozenset({OP}) if granted else frozenset(),
        operation_key=OP,
        risk=risk,
        environment_kind=kind,
    )


@pytest.mark.parametrize("effect", list(PolicyEffect))
def test_matching_rule_decides(effect: PolicyEffect) -> None:
    decision = decide(request(), {(OP, EnvironmentKind.TESTING): effect})
    assert decision.effect is effect
    assert decision.reason is DecisionReason.RULE_MATCH


def test_no_rule_means_deny() -> None:
    decision = decide(request(), {})
    assert decision.effect is PolicyEffect.DENY
    assert decision.reason is DecisionReason.NO_MATCHING_RULE


def test_rule_for_another_environment_kind_does_not_apply() -> None:
    decision = decide(request(), {(OP, EnvironmentKind.DEVELOPMENT): PolicyEffect.ALLOW})
    assert decision.effect is PolicyEffect.DENY
    assert decision.reason is DecisionReason.NO_MATCHING_RULE


def test_disabled_agent_is_denied_even_when_rule_allows() -> None:
    decision = decide(request(active=False), {(OP, EnvironmentKind.TESTING): PolicyEffect.ALLOW})
    assert decision.effect is PolicyEffect.DENY
    assert decision.reason is DecisionReason.AGENT_DISABLED


def test_ungranted_operation_is_denied_even_when_rule_allows() -> None:
    decision = decide(request(granted=False), {(OP, EnvironmentKind.TESTING): PolicyEffect.ALLOW})
    assert decision.effect is PolicyEffect.DENY
    assert decision.reason is DecisionReason.OPERATION_NOT_GRANTED


@pytest.mark.parametrize("risk", [RiskClass.EXECUTE, RiskClass.WRITE, RiskClass.DEPLOY])
@pytest.mark.parametrize("configured", [PolicyEffect.ALLOW, PolicyEffect.REQUIRE_APPROVAL])
def test_production_guardrail_overrides_permissive_rules(
    risk: RiskClass, configured: PolicyEffect
) -> None:
    decision = decide(
        request(risk=risk, kind=EnvironmentKind.PRODUCTION),
        {(OP, EnvironmentKind.PRODUCTION): configured},
    )
    assert decision.effect is PolicyEffect.DENY
    assert decision.reason is DecisionReason.PRODUCTION_GUARDRAIL


def test_reads_on_production_follow_the_rule() -> None:
    req = request(risk=RiskClass.READ, kind=EnvironmentKind.PRODUCTION)
    assert decide(req, {(OP, EnvironmentKind.PRODUCTION): PolicyEffect.ALLOW}).effect is (
        PolicyEffect.ALLOW
    )
    assert decide(req, {}).effect is PolicyEffect.DENY


def test_decision_table_is_fail_closed() -> None:
    """Exhaustive: a non-DENY outcome requires every precondition to hold."""
    combos = itertools.product(
        [True, False],  # agent active
        [True, False],  # operation granted
        list(RiskClass),
        list(EnvironmentKind),
        [None, *PolicyEffect],  # configured rule
    )
    checked = 0
    for active, granted, risk, kind, rule in combos:
        rules = {} if rule is None else {(OP, kind): rule}
        decision = decide(request(active=active, granted=granted, risk=risk, kind=kind), rules)
        permitted = (
            active
            and granted
            and rule in (PolicyEffect.ALLOW, PolicyEffect.REQUIRE_APPROVAL)
            and not (kind is EnvironmentKind.PRODUCTION and risk is not RiskClass.READ)
        )
        if permitted:
            assert decision.effect is rule
        else:
            assert decision.effect is PolicyEffect.DENY
        checked += 1
    assert checked == 2 * 2 * 4 * 3 * 4
