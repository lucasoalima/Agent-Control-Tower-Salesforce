import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReconciliationNotice } from "./reconciliation-notice";

describe("reconciliation notice", () => {
  it("shows the unknown outcome, no retry and detection timestamp", () => {
    const at = "2026-11-10T12:00:00Z";
    const html = renderToStaticMarkup(createElement(ReconciliationNotice, { requiredAt: at }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("Outcome unknown");
    expect(html).toContain("will not be retried automatically");
    expect(html).toContain(at);
  });

  it("does not warn when no reconciliation is required", () => {
    expect(renderToStaticMarkup(createElement(ReconciliationNotice, { requiredAt: null }))).toBe("");
  });
});
