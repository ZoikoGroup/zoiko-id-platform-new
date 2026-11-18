import { describe, expect, it } from "vitest";

import { LEVEL_WORDS, methodName, ordered } from "./method-logic";

describe("method certification board", () => {
  it("names a known method plainly and a new one without crashing", () => {
    expect(methodName("CHIP_CLONE_CHECK")).toBe("Passport chip: clone check");
    expect(methodName("SOMETHING_NEW")).toBe("something new");
  });
  it("shows what blocks a launch first and approved last", () => {
    const rows = [{ level: "APPROVED" as const }, { level: "BUILT_UNPROVEN" as const }, { level: "NOT_BUILT" as const }, { level: "EVIDENCE_INCOMPLETE" as const }];
    expect(ordered(rows).map((r) => r.level)).toEqual(["NOT_BUILT", "EVIDENCE_INCOMPLETE", "BUILT_UNPROVEN", "APPROVED"]);
  });
  it("only an approved method reads as ok, and built-not-proven never does", () => {
    expect(LEVEL_WORDS.APPROVED.tone).toBe("ok");
    expect(LEVEL_WORDS.BUILT_UNPROVEN.tone).not.toBe("ok");
    expect(LEVEL_WORDS.BUILT_UNPROVEN.meaning).toMatch(/never met a real document/);
  });
});
