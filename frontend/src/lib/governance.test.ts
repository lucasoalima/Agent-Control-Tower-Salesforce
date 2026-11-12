import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApprovalReview, AuditHistory, PolicyEditor } from "@/components/governance";
import { ApiError } from "./api";
import { decideExecution, governanceKey, savePolicy } from "./governance";
import type { AuditEvent, ExecutionDetail, PolicyCell } from "./types";

afterEach(() => vi.unstubAllGlobals());

const execution: ExecutionDetail = {
  id: "execution-1", correlation_id: "review-1",
  agent: { id: "agent-1", name: "Review agent", provider: "CODEX" },
  environment: { id: "environment-1", name: "Review sandbox", kind: "TESTING", platform: "salesforce" },
  operation_key: "salesforce.apex.write", operation_title: "Write Apex",
  policy_effect: "REQUIRE_APPROVAL", decision_reason: "RULE_MATCH",
  decision_detail: "Needs approval", status: "PENDING_APPROVAL",
  requested_at: "2026-11-11T12:00:00Z", decided_at: "2026-11-11T12:00:00Z",
  started_at: null, finished_at: null, approval_expires_at: "2026-11-11T12:05:00Z",
  reconciliation_required_at: null, action_hash: "a".repeat(64),
  parameters: { class_name: "ReviewClass", body: "public class ReviewClass {}" },
  result: null, error: null,
};

describe("approval requests", () => {
  it.each(["approve", "reject", "execute"] as const)("sends only the displayed binding for %s", async (action) => {
    const fetch = vi.fn().mockResolvedValue(Response.json(execution));
    vi.stubGlobal("fetch", fetch);
    await decideExecution(execution, action);
    expect(fetch).toHaveBeenCalledOnce();
    const [path, init] = fetch.mock.calls[0]!;
    expect(path).toBe("/api/v1/executions/execution-1/" + action);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(init.body)).toEqual({
      action_hash: execution.action_hash, agent_id: "agent-1", environment_id: "environment-1",
    });
  });

  it("refuses a missing hash without sending a request", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    expect(() => decideExecution({ ...execution, action_hash: null }, "approve")).toThrow(ApiError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([401, 409, 422, 500])("does not retry a refused or failed decision (%s)", async (status) => {
    const fetch = vi.fn().mockResolvedValue(Response.json(
      { error: { code: "conflict", message: "Refresh the record." } }, { status },
    ));
    vi.stubGlobal("fetch", fetch);
    await expect(decideExecution(execution, "approve")).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("does not retry after an uncertain network outcome", async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError("Connection lost"));
    vi.stubGlobal("fetch", fetch);
    await expect(decideExecution(execution, "approve")).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe("review controls", () => {
  it("shows stored parameters, identities, target, hash and expiry before enabling decisions", () => {
    const html = renderToStaticMarkup(createElement(ApprovalReview, { execution }));
    for (const value of ["ReviewClass", "environment-1", "agent-1", "execution-1",
      "Review sandbox", execution.action_hash!, "Approve and run", "Reject request"]) {
      expect(html).toContain(value);
    }
    expect(html).toContain('type="checkbox"');
    expect(html.match(/<button\b[^>]*\sdisabled=""/g) ?? []).toHaveLength(2);
    expect(html).not.toContain("checked=");
  });

  it("offers explicit resume for an unused approval", () => {
    const html = renderToStaticMarkup(createElement(ApprovalReview, {
      execution: { ...execution, status: "APPROVED" },
    }));
    expect(html).toContain("Run approved action");
    expect(html).not.toContain("Approve and run");
    expect(html).not.toContain("Reject request");
  });

  it.each(["DENIED", "EXPIRED", "REJECTED", "EXECUTING", "COMPLETED", "FAILED"] as const)(
    "hides decision controls for %s", (status) => {
      expect(renderToStaticMarkup(createElement(ApprovalReview, {
        execution: { ...execution, status },
      }))).toBe("");
    },
  );

  it("renders immutable history with actor, timestamp, correlation and binding", () => {
    const event: AuditEvent = {
      id: "audit-1", occurred_at: "2026-11-11T12:01:00Z", action: "approval.approved",
      actor_type: "OPERATOR", actor_id: "operator-1", correlation_id: "decision-1",
      execution_id: execution.id, details: { action_hash: execution.action_hash },
    };
    const html = renderToStaticMarkup(createElement(AuditHistory, { events: [event] }));
    for (const value of [event.action, event.occurred_at, "operator-1", "decision-1", execution.action_hash!]) {
      expect(html).toContain(value);
    }
  });
});

describe("policy editing", () => {
  const cell: PolicyCell = {
    environment_kind: "TESTING", configured: "REQUIRE_APPROVAL",
    effective: "REQUIRE_APPROVAL", reason: "RULE_MATCH",
  };

  it.each(["ALLOW", "REQUIRE_APPROVAL", "DENY"] as const)("sends a policy effect only (%s)", async (effect) => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ ...cell, configured: effect }));
    vi.stubGlobal("fetch", fetch);
    await savePolicy("salesforce.apex.write", "TESTING", effect);
    const [path, init] = fetch.mock.calls[0]!;
    expect(path).toBe("/api/v1/policies/salesforce.apex.write/TESTING");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ effect });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("shows the server's fixed guardrail without editing controls", () => {
    const html = renderToStaticMarkup(createElement(PolicyEditor, {
      operation: execution.operation_key, title: "Write Apex",
      cell: { ...cell, environment_kind: "PRODUCTION", effective: "DENY", reason: "PRODUCTION_GUARDRAIL" },
    }));
    expect(html).toContain("Fixed guardrail");
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<button");
  });

  it("renders an accessible policy selector and save control for an editable cell", () => {
    const html = renderToStaticMarkup(createElement(PolicyEditor, {
      operation: execution.operation_key, title: "Write Apex", cell,
    }));
    expect(html).toContain("<label");
    expect(html).toContain("<select");
    expect(html).toContain("Save policy");
    expect(html).toMatch(/<button\b[^>]*\sdisabled=""/);
  });
});

describe("governance refresh", () => {
  it.each(["/executions", "/executions?status=PENDING_APPROVAL",
    "/executions/execution-1/audit-events", "/overview", "/policies"])("refreshes %s", (key) => {
    expect(governanceKey(key)).toBe(true);
  });

  it.each([null, "/auth/me", "/agents"])("preserves unrelated cache entries (%s)", (key) => {
    expect(governanceKey(key)).toBe(false);
  });
});
