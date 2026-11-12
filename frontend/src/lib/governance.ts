import { api, ApiError } from "@/lib/api";
import type { ExecutionDetail, PolicyCell, PolicyEffect, EnvironmentKind } from "@/lib/types";

export type ApprovalAction = "approve" | "reject" | "execute";

export function decideExecution(execution: ExecutionDetail, action: ApprovalAction) {
  if (!execution.action_hash) throw new ApiError(0, "missing_hash", "This record has no action hash.");
  return api<ExecutionDetail>(`/executions/${encodeURIComponent(execution.id)}/${action}`, {
    method: "POST",
    body: JSON.stringify({
      action_hash: execution.action_hash,
      agent_id: execution.agent.id,
      environment_id: execution.environment.id,
    }),
  });
}

export function savePolicy(operation: string, kind: EnvironmentKind, effect: PolicyEffect) {
  return api<PolicyCell>(`/policies/${encodeURIComponent(operation)}/${kind}`, {
    method: "PUT",
    body: JSON.stringify({ effect }),
  });
}

export function governanceKey(key: unknown): boolean {
  return typeof key === "string" &&
    (key.startsWith("/executions") || key === "/overview" || key === "/policies");
}
