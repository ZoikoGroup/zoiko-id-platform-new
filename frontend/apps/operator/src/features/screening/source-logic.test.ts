import { describe, expect, it } from "vitest";

import { actionsFor, approvalCounts, codeText, flagsToAcknowledge, shortDigest } from "./source-logic";

const approval = (approver: string, kind: "PUBLISH" | "ROLLBACK") => ({ approver, at: "2026-11-18T12:00:00Z", acknowledged: [], kind });

describe("sanctions source release", () => {
  it("says every validation code in words and an unknown code as itself", () => {
    for (const c of ["LEGACY_SOURCE_REJECTED", "HOST_NOT_THE_PUBLISHERS", "SCHEMA_INVALID", "ZERO_ROWS", "COUNT_DRIFT_ABOVE_LIMIT", "TWO_APPROVERS_REQUIRED"]) {
      expect(codeText(c)).not.toBe(c);
    }
    expect(codeText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(codeText("FLAG_NOT_ACKNOWLEDGED:ZERO_ROWS")).toContain("The file holds no entries");
    expect(codeText("STATE_QUARANTINED")).toContain("quarantined");
  });

  it("counts publish and rollback approvals separately, by distinct person", () => {
    const r = { approvals: [approval("a", "PUBLISH"), approval("a", "PUBLISH"), approval("b", "PUBLISH"), approval("c", "ROLLBACK")] };
    expect(approvalCounts(r)).toEqual({ publish: 2, rollback: 1 });
    expect(approvalCounts({ approvals: [] })).toEqual({ publish: 0, rollback: 0 });
  });

  it("offers only what the state allows", () => {
    expect(actionsFor({ state: "VALIDATED", publishable: [] })).toEqual({ approve: true, publish: true, rollback: false });
    expect(actionsFor({ state: "VALIDATED", publishable: ["TWO_APPROVERS_REQUIRED"] })).toEqual({ approve: true, publish: false, rollback: false });
    expect(actionsFor({ state: "QUARANTINED", publishable: [] })).toEqual({ approve: false, publish: false, rollback: false });
    expect(actionsFor({ state: "ACTIVE", publishable: [] })).toEqual({ approve: false, publish: false, rollback: false });
    expect(actionsFor({ state: "SUPERSEDED", publishable: [] })).toEqual({ approve: false, publish: false, rollback: true });
  });

  it("lists the flags to acknowledge and shortens a digest", () => {
    expect(flagsToAcknowledge({ flags: ["ZERO_ROWS", "COUNT_DRIFT_ABOVE_LIMIT"] })).toEqual(["ZERO_ROWS", "COUNT_DRIFT_ABOVE_LIMIT"]);
    expect(shortDigest("a".repeat(64))).toBe("aaaaaaaaaaaa…");
    expect(shortDigest("abc")).toBe("abc");
  });
});
