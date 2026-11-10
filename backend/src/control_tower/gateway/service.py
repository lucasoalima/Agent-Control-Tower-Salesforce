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
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.clock import utcnow
from control_tower.core import lifecycle
from control_tower.core.enums import (
    AgentStatus,
    AuditActorType,
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
from control_tower.persistence.audit import record_event
from control_tower.persistence.models import Agent, AuditEvent, Environment, Execution, PolicyRule
from control_tower.platforms.registry import AdapterRegistry

log = get_logger(__name__)

MAX_OPERATION_KEY_LENGTH = 120
MAX_REFUSED_PAYLOAD_BYTES = 16_384
MAX_DETAIL_LENGTH = 2_000


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    action_hash: str
    agent_id: uuid.UUID
    environment_id: uuid.UUID


class ApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class Gateway:
    def __init__(
        self,
        session: Session,
        catalog: OperationCatalog,
        adapters: AdapterRegistry,
        clock: Clock = utcnow,
        *,
        approval_ttl_seconds: int = 300,
    ) -> None:
        self._session = session
        self._catalog = catalog
        self._adapters = adapters
        self._clock = clock
        self._approval_ttl_seconds = approval_ttl_seconds

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
            deadline = execution.decided_at + timedelta(seconds=self._approval_ttl_seconds)
            execution.approval_expires_at = deadline
            self._session.add(execution)
            record_event(
                self._session,
                "approval.requested",
                actor_type=AuditActorType.AGENT,
                actor_id=agent.id,
                correlation_id=correlation_id,
                occurred_at=execution.decided_at,
                execution_id=execution.id,
                details={
                    "action_hash": execution.action_hash,
                    "expires_at": deadline.isoformat(),
                    "agent_id": str(agent.id),
                    "environment_id": str(environment.id),
                    "operation_key": spec.key,
                },
            )
            self._session.commit()
            self._log(execution)
            return execution

        return self._execute(execution, adapter, spec, params)

    def approve(
        self,
        execution_id: uuid.UUID,
        binding: ApprovalBinding,
        operator_id: uuid.UUID,
        correlation_id: str,
    ) -> Execution:
        execution = self._approval_row(execution_id, binding, ExecutionStatus.PENDING_APPROVAL)
        self._approval_context(execution)
        lifecycle.assert_transition(execution.status, ExecutionStatus.APPROVED)
        execution.status = ExecutionStatus.APPROVED
        self._approval_event("approval.approved", execution, operator_id, correlation_id)
        self._session.commit()
        return execution

    def reject(
        self,
        execution_id: uuid.UUID,
        binding: ApprovalBinding,
        operator_id: uuid.UUID,
        correlation_id: str,
    ) -> Execution:
        execution = self._approval_row(execution_id, binding, ExecutionStatus.PENDING_APPROVAL)
        lifecycle.assert_transition(execution.status, ExecutionStatus.REJECTED)
        execution.status = ExecutionStatus.REJECTED
        execution.finished_at = self._clock()
        self._approval_event("approval.rejected", execution, operator_id, correlation_id)
        self._session.commit()
        return execution

    def execute_approved(
        self,
        execution_id: uuid.UUID,
        binding: ApprovalBinding,
        operator_id: uuid.UUID,
        correlation_id: str,
    ) -> Execution:
        execution = self._approval_row(execution_id, binding, ExecutionStatus.APPROVED)
        approval = self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.execution_id == execution.id,
                AuditEvent.action == "approval.approved",
                AuditEvent.actor_type == AuditActorType.OPERATOR,
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )
        if approval is None or approval.details != self._approval_details(execution):
            raise ApprovalError("No approval matches this stored action.")
        adapter, spec, params = self._approval_context(execution)
        lifecycle.assert_transition(execution.status, ExecutionStatus.EXECUTING)
        self._approval_event("approval.consumed", execution, operator_id, correlation_id)
        return self._execute(execution, adapter, spec, params)

    def _approval_row(
        self,
        execution_id: uuid.UUID,
        binding: ApprovalBinding,
        expected_status: ExecutionStatus,
    ) -> Execution:
        execution = self._session.scalar(
            select(Execution)
            .where(Execution.id == execution_id)
            .with_for_update(of=Execution)
            .execution_options(populate_existing=True)
        )
        if execution is None:
            raise ApprovalError("Execution not found.", 404)
        if execution.status is not expected_status:
            raise ApprovalError("The execution is no longer in the required approval state.")
        deadline = execution.approval_expires_at
        now = self._clock()
        if deadline is None:
            raise ApprovalError("The request has no approval deadline.")
        if deadline <= now:
            lifecycle.assert_transition(execution.status, ExecutionStatus.EXPIRED)
            execution.status = ExecutionStatus.EXPIRED
            execution.finished_at = now
            record_event(
                self._session,
                "approval.expired",
                actor_type=AuditActorType.SYSTEM,
                actor_id=None,
                correlation_id=execution.correlation_id,
                occurred_at=now,
                execution_id=execution.id,
                details={"action_hash": execution.action_hash, "expires_at": deadline.isoformat()},
            )
            self._session.commit()
            raise ApprovalError("The approval request has expired.")
        fingerprint = action_fingerprint(
            agent_id=execution.agent_id,
            environment_id=execution.environment_id,
            operation_key=execution.operation_key,
            parameters=execution.parameters,
        )
        if (
            binding.action_hash != execution.action_hash
            or binding.action_hash != fingerprint
            or binding.agent_id != execution.agent_id
            or binding.environment_id != execution.environment_id
        ):
            raise ApprovalError("The supplied binding does not match the stored action.")
        requested = self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.execution_id == execution.id,
                AuditEvent.action == "approval.requested",
                AuditEvent.actor_type == AuditActorType.AGENT,
            )
            .limit(1)
        )
        details = self._approval_details(execution)
        if requested is None or any(
            requested.details.get(key) != value for key, value in details.items()
        ):
            raise ApprovalError("The stored action no longer matches its original request.")
        return execution

    def _approval_context(
        self, execution: Execution
    ) -> tuple[PlatformAdapter, OperationSpec, OperationParams]:
        agent = self._session.scalar(
            select(Agent)
            .where(Agent.id == execution.agent_id)
            .with_for_update(of=Agent)
            .execution_options(populate_existing=True)
        )
        assert agent is not None
        environment = execution.environment
        if agent.environment_id != execution.environment_id:
            raise ApprovalError("The agent is no longer bound to this environment.")
        spec = self._catalog.get(execution.operation_key)
        adapter = self._adapters.get(environment.adapter_key)
        refusal = self._intake_refusal(agent, environment, execution.operation_key, spec, adapter)
        if refusal is not None:
            raise ApprovalError("The stored action is no longer eligible for execution.")
        assert spec is not None and adapter is not None
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
            raise ApprovalError("Current policy refuses this stored action.")
        params, invalid = self._validate(spec, execution.parameters)
        if invalid is not None or params is None or params.canonical() != execution.parameters:
            raise ApprovalError("The stored parameters are no longer valid and canonical.")
        return adapter, spec, params

    @staticmethod
    def _approval_details(execution: Execution) -> dict[str, Any]:
        return {
            "action_hash": execution.action_hash,
            "agent_id": str(execution.agent_id),
            "environment_id": str(execution.environment_id),
            "operation_key": execution.operation_key,
            "expires_at": (
                execution.approval_expires_at.isoformat()
                if execution.approval_expires_at is not None
                else None
            ),
        }

    def _approval_event(
        self, action: str, execution: Execution, operator_id: uuid.UUID, correlation_id: str
    ) -> None:
        record_event(
            self._session,
            action,
            actor_type=AuditActorType.OPERATOR,
            actor_id=operator_id,
            correlation_id=correlation_id,
            occurred_at=self._clock(),
            execution_id=execution.id,
            details=self._approval_details(execution),
        )

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
        self._session.flush()
        self._session.refresh(execution, attribute_names=["reconciliation_required_at"])
        execution.reconciliation_required_at = None
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
