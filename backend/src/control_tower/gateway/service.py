"""The gateway: the single entry point for agent tool requests.

Every request, whatever its outcome, leaves exactly one execution record.

    request ──> known operation? ──> policy ──> parameters valid? ──> secret scan
                     │ no              │ DENY         │ no                │ found
                     ▼                 ▼              ▼                   ▼
                  DENIED            DENIED         DENIED              DENIED

    then, by policy effect:
        REQUIRE_APPROVAL ──> PENDING_APPROVAL   (nothing is executed)
        ALLOW            ──> EXECUTING ──> COMPLETED | FAILED

The EXECUTING record is committed *before* the adapter is called, so a crash in
the middle of an operation still leaves evidence that it was started.
"""

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.clock import utcnow
from control_tower.core import lifecycle
from control_tower.core.enums import (
    AgentStatus,
    DecisionReason,
    ExecutionStatus,
    PolicyEffect,
)
from control_tower.core.fingerprint import action_fingerprint, canonical_json
from control_tower.core.operations import OperationCatalog, OperationParams, OperationSpec
from control_tower.core.policy import PolicyDecision, PolicyRequest, decide
from control_tower.core.ports import AdapterError, Clock, PlatformAdapter, TargetEnvironment
from control_tower.core.redaction import contains_secret, redact
from control_tower.logging import get_logger
from control_tower.persistence.models import Agent, Environment, Execution, PolicyRule
from control_tower.platforms.registry import AdapterRegistry

log = get_logger(__name__)

MAX_OPERATION_KEY_LENGTH = 120
MAX_REFUSED_PAYLOAD_BYTES = 16_384
MAX_DETAIL_LENGTH = 2_000


class Gateway:
    def __init__(
        self,
        session: Session,
        catalog: OperationCatalog,
        adapters: AdapterRegistry,
        clock: Clock = utcnow,
    ) -> None:
        self._session = session
        self._catalog = catalog
        self._adapters = adapters
        self._clock = clock

    def submit(
        self,
        agent: Agent,
        operation_key: str,
        raw_parameters: Any,
        correlation_id: str,
    ) -> Execution:
        requested_at = self._clock()
        environment = agent.environment
        agent.last_seen_at = requested_at

        execution = Execution(
            id=uuid.uuid4(),
            correlation_id=correlation_id,
            agent_id=agent.id,
            environment_id=environment.id,
            operation_key=operation_key[:MAX_OPERATION_KEY_LENGTH],
            requested_at=requested_at,
        )
        execution.agent = agent
        execution.environment = environment

        spec = self._catalog.get(operation_key)
        adapter = self._adapters.get(environment.adapter_key)

        refusal = self._intake_refusal(agent, environment, operation_key, spec, adapter)
        if refusal is None:
            assert spec is not None and adapter is not None  # narrowed by _intake_refusal
            decision = decide(
                PolicyRequest(
                    agent_active=agent.status is AgentStatus.ACTIVE,
                    agent_granted_operations=frozenset(agent.granted_operations),
                    operation_key=spec.key,
                    risk=spec.risk,
                    environment_kind=environment.kind,
                ),
                self._rules_for(spec.key),
            )
            if decision.effect is PolicyEffect.DENY:
                refusal = decision

        params: OperationParams | None = None
        if refusal is None:
            assert spec is not None
            params, refusal = self._validate(spec, raw_parameters)

        if refusal is not None:
            return self._record_denied(execution, refusal, raw_parameters)

        assert spec is not None and adapter is not None and params is not None
        canonical = params.canonical()
        execution.parameters = canonical
        execution.action_hash = action_fingerprint(
            agent_id=agent.id,
            environment_id=environment.id,
            operation_key=spec.key,
            parameters=canonical,
        )
        execution.policy_effect = decision.effect
        execution.decision_reason = decision.reason
        execution.decision_detail = decision.detail
        execution.decided_at = self._clock()

        if decision.effect is PolicyEffect.REQUIRE_APPROVAL:
            execution.status = ExecutionStatus.PENDING_APPROVAL
            self._session.add(execution)
            self._session.commit()
            self._log(execution)
            return execution

        return self._execute(execution, adapter, spec, params)

    # -- intake ------------------------------------------------------------

    def _intake_refusal(
        self,
        agent: Agent,
        environment: Environment,
        operation_key: str,
        spec: OperationSpec | None,
        adapter: PlatformAdapter | None,
    ) -> PolicyDecision | None:
        if agent.status is not AgentStatus.ACTIVE:
            return PolicyDecision(
                PolicyEffect.DENY, DecisionReason.AGENT_DISABLED, "The agent is disabled."
            )
        if spec is None:
            return PolicyDecision(
                PolicyEffect.DENY,
                DecisionReason.UNKNOWN_OPERATION,
                "The requested operation does not exist.",
            )
        if (
            adapter is None
            or spec.platform != environment.platform
            or not adapter.supports(operation_key)
        ):
            return PolicyDecision(
                PolicyEffect.DENY,
                DecisionReason.UNSUPPORTED_BY_ADAPTER,
                f"'{spec.key}' cannot run against environment '{environment.name}'.",
            )
        return None

    def _rules_for(self, operation_key: str) -> dict[tuple[str, Any], PolicyEffect]:
        rows = self._session.scalars(
            select(PolicyRule).where(PolicyRule.operation_key == operation_key)
        )
        return {(row.operation_key, row.environment_kind): row.effect for row in rows}

    @staticmethod
    def _validate(
        spec: OperationSpec, raw_parameters: Any
    ) -> tuple[OperationParams | None, PolicyDecision | None]:
        try:
            params = spec.params_model.model_validate(raw_parameters)
        except ValidationError as exc:
            problems = [
                f"{'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}"
                for err in exc.errors(include_input=False, include_url=False)[:10]
            ]
            return None, PolicyDecision(
                PolicyEffect.DENY,
                DecisionReason.INVALID_PARAMETERS,
                ("Parameters are not valid for this operation. " + "; ".join(problems))[
                    :MAX_DETAIL_LENGTH
                ],
            )
        if contains_secret(params.canonical()):
            return None, PolicyDecision(
                PolicyEffect.DENY,
                DecisionReason.CREDENTIAL_IN_PAYLOAD,
                "The parameters contain a value that looks like a credential. "
                "Credentials must never be sent through the gateway.",
            )
        return params, None

    # -- outcomes ----------------------------------------------------------

    def _record_denied(
        self, execution: Execution, decision: PolicyDecision, raw_parameters: Any
    ) -> Execution:
        execution.parameters = _safe_copy_of_refused_payload(raw_parameters)
        execution.action_hash = None
        execution.policy_effect = PolicyEffect.DENY
        execution.decision_reason = decision.reason
        execution.decision_detail = decision.detail
        execution.decided_at = self._clock()
        execution.status = ExecutionStatus.DENIED
        self._session.add(execution)
        self._session.commit()
        self._log(execution)
        return execution

    def _execute(
        self,
        execution: Execution,
        adapter: PlatformAdapter,
        spec: OperationSpec,
        params: OperationParams,
    ) -> Execution:
        environment = execution.environment
        execution.status = ExecutionStatus.EXECUTING
        execution.started_at = self._clock()
        self._session.add(execution)
        self._session.commit()  # durable before the side effect happens

        target = TargetEnvironment(
            id=environment.id,
            name=environment.name,
            platform=environment.platform,
            kind=environment.kind,
            external_id=environment.external_id,
        )
        try:
            result = adapter.execute(target, spec.key, params)
        except AdapterError as exc:
            self._finish(
                execution,
                ExecutionStatus.FAILED,
                error={"code": exc.code, "message": exc.message, "details": exc.details},
            )
        except Exception as exc:
            log.exception("gateway.adapter_crash", execution_id=str(execution.id))
            self._finish(
                execution,
                ExecutionStatus.FAILED,
                error={
                    "code": "ADAPTER_CRASH",
                    "message": "The adapter raised an unexpected error.",
                    "details": {"exception_type": type(exc).__name__},
                },
            )
        else:
            self._finish(execution, ExecutionStatus.COMPLETED, result=result)
        return execution

    def _finish(
        self,
        execution: Execution,
        status: ExecutionStatus,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        lifecycle.assert_transition(execution.status, status)
        execution.status = status
        execution.result = redact(result)[0] if result is not None else None
        execution.error = redact(error)[0] if error is not None else None
        execution.finished_at = self._clock()
        self._session.commit()
        self._log(execution)

    @staticmethod
    def _log(execution: Execution) -> None:
        log.info(
            "gateway.request_recorded",
            execution_id=str(execution.id),
            agent_id=str(execution.agent_id),
            environment_id=str(execution.environment_id),
            operation=execution.operation_key,
            policy_effect=execution.policy_effect.value,
            decision_reason=execution.decision_reason.value,
            status=execution.status.value,
            action_hash=execution.action_hash,
        )


def _safe_copy_of_refused_payload(raw_parameters: Any) -> dict[str, Any]:
    """What we keep of a payload that was refused: redacted, bounded, always a dict."""
    if not isinstance(raw_parameters, dict):
        return {"_note": f"parameters were not an object ({type(raw_parameters).__name__})"}
    redacted, _ = redact(raw_parameters)
    try:
        size = len(canonical_json(redacted).encode("utf-8"))
    except (TypeError, ValueError):
        return {"_note": "parameters could not be serialised"}
    if size > MAX_REFUSED_PAYLOAD_BYTES:
        return {"_note": f"parameters omitted: {size} bytes exceeds the limit for refused requests"}
    return redacted  # type: ignore[no-any-return]
