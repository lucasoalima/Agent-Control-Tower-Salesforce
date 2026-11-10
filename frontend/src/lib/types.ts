// Mirrors backend/src/control_tower/api/schemas.py. Keep the two in step.

export type EnvironmentKind = "DEVELOPMENT" | "TESTING" | "PRODUCTION";
export type PolicyEffect = "ALLOW" | "REQUIRE_APPROVAL" | "DENY";
export type RiskClass = "READ" | "EXECUTE" | "WRITE" | "DEPLOY";
export type AgentStatus = "ACTIVE" | "DISABLED";
export type AgentProvider = "CLAUDE_CODE" | "CODEX" | "OTHER";
export type ExecutionStatus =
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXPIRED"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED"
  | "DENIED";
export type DecisionReason =
  | "RULE_MATCH"
  | "NO_MATCHING_RULE"
  | "PRODUCTION_GUARDRAIL"
  | "OPERATION_NOT_GRANTED"
  | "AGENT_DISABLED"
  | "UNKNOWN_OPERATION"
  | "UNSUPPORTED_BY_ADAPTER"
  | "INVALID_PARAMETERS"
  | "CREDENTIAL_IN_PAYLOAD";

export interface Operator {
  id: string;
  username: string;
}

export interface Environment {
  id: string;
  name: string;
  platform: string;
  kind: EnvironmentKind;
  adapter_key: string;
  external_id: string;
  is_mock: boolean;
  created_at: string;
}

export interface Agent {
  id: string;
  name: string;
  provider: AgentProvider;
  status: AgentStatus;
  environment: Environment;
  granted_operations: string[];
  token_hint: string;
  created_at: string;
  last_seen_at: string | null;
}

export interface Operation {
  key: string;
  platform: string;
  title: string;
  description: string;
  risk: RiskClass;
  parameters_schema: Record<string, unknown>;
}

export interface PolicyCell {
  environment_kind: EnvironmentKind;
  configured: PolicyEffect | null;
  effective: PolicyEffect;
  reason: DecisionReason;
}

export interface PolicyMatrix {
  environment_kinds: EnvironmentKind[];
  rows: { operation: Operation; cells: PolicyCell[] }[];
}

export interface ExecutionSummary {
  id: string;
  correlation_id: string;
  agent: { id: string; name: string; provider: AgentProvider };
  environment: { id: string; name: string; kind: EnvironmentKind; platform: string };
  operation_key: string;
  operation_title: string | null;
  policy_effect: PolicyEffect;
  decision_reason: DecisionReason;
  status: ExecutionStatus;
  requested_at: string;
  decided_at: string;
  started_at: string | null;
  finished_at: string | null;
  approval_expires_at: string | null;
  reconciliation_required_at: string | null;
}

export interface ExecutionDetail extends ExecutionSummary {
  decision_detail: string;
  action_hash: string | null;
  parameters: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
}

export interface ExecutionPage {
  items: ExecutionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface Overview {
  agents_total: number;
  agents_active: number;
  executions_total: number;
  pending_approvals: number;
  failed_actions: number;
  policy_violations: number;
}
