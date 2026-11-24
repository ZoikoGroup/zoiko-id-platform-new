import type { VerifyApiDecision } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { checkLabel, cleanReference, historyLines, outcomeLabel, STATUS_FILTERS } from "./results-logic";

const d = (n: number, outcome: VerifyApiDecision["outcome"], sup: string | null): VerifyApiDecision => ({
  decision_id: `dec_${n}`,
  decision_version: n,
  supersedes_decision_id: sup,
  outcome,
  checks: [],
  reason_codes: [],
  policy_version: "p",
  completed_at: "2026-11-24T10:00:00Z",
  sandbox: true,
  assurance: null,
});

describe("results card", () => {
  it("shows the newest decision first and says what it replaced", () => {
    const lines = historyLines([d(1, "review_required", null), d(2, "verified", "dec_1")]);
    expect(lines.map((l) => l.id)).toEqual(["dec_2", "dec_1"]);
    expect(lines[0]).toMatchObject({ current: true });
    expect(lines[0]!.text).toBe("Version 2: Verified (replaced version 1)");
    expect(lines[1]!.current).toBe(false);
  });
  it("names outcomes and checks, with a fallback that never hides an unknown one", () => {
    expect(outcomeLabel("not_verified").label).toBe("Not verified");
    expect(outcomeLabel("something")).toEqual({ label: "something", tone: "neutral" });
    expect(checkLabel("face_match")).toBe("Face match");
    expect(checkLabel("new_check")).toBe("new check");
  });
  it("matches a reference exactly as typed and ignores a blank one", () => {
    expect(cleanReference("  cust-1 ")).toBe("cust-1");
    expect(cleanReference("   ")).toBeUndefined();
  });
  it("offers the states a session can be searched by", () => {
    expect(STATUS_FILTERS[0]).toEqual({ id: "", label: "Any state" });
    expect(STATUS_FILTERS.map((s) => s.id)).toContain("manual_review");
  });
});
