import { describe, expect, it } from "vitest";
import { elapsedMs, formatDuration, plural, relativeTime, shortHash } from "./format";

describe("formatDuration", () => {
  it.each([
    [null, ""],
    [0, "under 1 ms"],
    [12.4, "12 ms"],
    [999, "999 ms"],
    [1500, "1.5 s"],
    [61_000, "1 min 1 s"],
  ])("%s -> %s", (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });
});

describe("elapsedMs", () => {
  it("is null when a timestamp is missing", () => {
    expect(elapsedMs(null, "2026-01-01T00:00:00Z")).toBeNull();
    expect(elapsedMs("2026-01-01T00:00:00Z", null)).toBeNull();
  });
  it("subtracts timestamps", () => {
    expect(elapsedMs("2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.250Z")).toBe(250);
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-01-01T12:00:00Z");
  it.each([
    [null, "never"],
    ["2026-01-01T11:59:58Z", "just now"],
    ["2026-01-01T11:59:30Z", "30 seconds ago"],
    ["2026-01-01T11:59:00Z", "1 minute ago"],
    ["2026-01-01T11:15:00Z", "45 minutes ago"],
    ["2026-01-01T09:00:00Z", "3 hours ago"],
    ["2025-12-30T12:00:00Z", "2 days ago"],
  ])("%s -> %s", (iso, expected) => {
    expect(relativeTime(iso, now)).toBe(expected);
  });
});

describe("shortHash", () => {
  it("keeps both ends of a long hash", () => {
    expect(shortHash("b72ce6e40a9f4af6aba0de895d63090b85d2cd83e3268e0a6b91b702ddab29a0")).toBe(
      "b72ce6e4…ab29a0",
    );
  });
  it("handles missing and short values", () => {
    expect(shortHash(null)).toBe("none");
    expect(shortHash("abc")).toBe("abc");
  });
});

describe("plural", () => {
  it("picks the right form", () => {
    expect(plural(1, "agent")).toBe("1 agent");
    expect(plural(0, "agent")).toBe("0 agents");
    expect(plural(2, "policy", "policies")).toBe("2 policies");
  });
});
