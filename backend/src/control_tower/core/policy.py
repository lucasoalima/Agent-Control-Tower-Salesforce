"""Policy decision function.

Pure: no I/O, no clock, no database. Everything it needs is passed in, which
makes the full decision table cheap to test exhaustively.

Evaluation order (first match wins):

1. Agent disabled                               -> DENY
2. Operation not granted to this agent          -> DENY
3. Non-read operation on a PRODUCTION target    -> DENY  (guardrail, not configurable)
4. Rule for (operation, environment kind)       -> that rule's effect
5. No rule                                      -> DENY  (default deny)
"""

from collections.abc import Mapping
from dataclasses import dataclass

from control_tower.core.enums import (
    DecisionReason,
    EnvironmentKind,
    PolicyEffect,
    RiskClass,
)

RuleKey = tuple[str, EnvironmentKind]
RuleSet = Mapping[RuleKey, PolicyEffect]


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    agent_active: bool
    agent_granted_operations: frozenset[str]
    operation_key: str
    risk: RiskClass
    environment_kind: EnvironmentKind


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    effect: PolicyEffect
    reason: DecisionReason
    detail: str


def violates_production_guardrail(risk: RiskClass, kind: EnvironmentKind) -> bool:
    """MVP invariant: nothing but reads may ever reach a production environment."""
    return kind is EnvironmentKind.PRODUCTION and risk is not RiskClass.READ


def decide(request: PolicyRequest, rules: RuleSet) -> PolicyDecision:
    if not request.agent_active:
        return PolicyDecision(
            PolicyEffect.DENY, DecisionReason.AGENT_DISABLED, "The agent is disabled."
        )

    if request.operation_key not in request.agent_granted_operations:
        return PolicyDecision(
            PolicyEffect.DENY,
            DecisionReason.OPERATION_NOT_GRANTED,
            f"The agent has not been granted '{request.operation_key}'.",
        )

    if violates_production_guardrail(request.risk, request.environment_kind):
        return PolicyDecision(
            PolicyEffect.DENY,
            DecisionReason.PRODUCTION_GUARDRAIL,
            f"{request.risk.value} operations are never permitted on production environments.",
        )

    effect = rules.get((request.operation_key, request.environment_kind))
    if effect is None:
        return PolicyDecision(
            PolicyEffect.DENY,
            DecisionReason.NO_MATCHING_RULE,
            f"No policy rule covers '{request.operation_key}' on "
            f"{request.environment_kind.value} environments, so the request is denied by default.",
        )

    return PolicyDecision(
        effect,
        DecisionReason.RULE_MATCH,
        f"Policy for '{request.operation_key}' on {request.environment_kind.value} "
        f"environments is {effect.value}.",
    )
