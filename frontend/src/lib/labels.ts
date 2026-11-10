import type {
  AgentProvider,
  DecisionReason,
  EnvironmentKind,
  ExecutionStatus,
  PolicyEffect,
  RiskClass,
} from "./types";

export type Tone = "allow" | "approval" | "deny" | "accent" | "muted";

export const EFFECT_LABEL: Record<PolicyEffect, string> = {
  ALLOW: "Allowed",
  REQUIRE_APPROVAL: "Needs approval",
  DENY: "Blocked",
};

export const EFFECT_TONE: Record<PolicyEffect, Tone> = {
  ALLOW: "allow",
  REQUIRE_APPROVAL: "approval",
  DENY: "deny",
};

export const STATUS_LABEL: Record<ExecutionStatus, string> = {
  PENDING_APPROVAL: "Waiting for approval",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  EXPIRED: "Expired",
  EXECUTING: "Running",
  COMPLETED: "Completed",
  FAILED: "Failed",
  DENIED: "Blocked",
};

export const STATUS_TONE: Record<ExecutionStatus, Tone> = {
  PENDING_APPROVAL: "approval",
  APPROVED: "accent",
  REJECTED: "muted",
  EXPIRED: "muted",
  EXECUTING: "accent",
  COMPLETED: "allow",
  FAILED: "deny",
  DENIED: "deny",
};

export const REASON_LABEL: Record<DecisionReason, string> = {
  RULE_MATCH: "Policy rule",
  NO_MATCHING_RULE: "No rule, blocked by default",
  PRODUCTION_GUARDRAIL: "Production guardrail",
  OPERATION_NOT_GRANTED: "Not granted to this agent",
  AGENT_DISABLED: "Agent is disabled",
  UNKNOWN_OPERATION: "Unknown operation",
  UNSUPPORTED_BY_ADAPTER: "Not supported by this environment",
  INVALID_PARAMETERS: "Invalid parameters",
  CREDENTIAL_IN_PAYLOAD: "Credential found in the request",
};

export const KIND_LABEL: Record<EnvironmentKind, string> = {
  DEVELOPMENT: "Development",
  TESTING: "Testing",
  PRODUCTION: "Production",
};

export const PROVIDER_LABEL: Record<AgentProvider, string> = {
  CLAUDE_CODE: "Claude Code",
  CODEX: "Codex",
  OTHER: "Other",
};

export const RISK_LABEL: Record<RiskClass, string> = {
  READ: "Reads",
  EXECUTE: "Runs",
  WRITE: "Changes",
  DEPLOY: "Deploys",
};

export const TONE_TEXT: Record<Tone, string> = {
  allow: "text-allow",
  approval: "text-approval",
  deny: "text-deny",
  accent: "text-accent",
  muted: "text-muted",
};

export const TONE_BG: Record<Tone, string> = {
  allow: "bg-allow",
  approval: "bg-approval",
  deny: "bg-deny",
  accent: "bg-accent",
  muted: "bg-muted",
};
