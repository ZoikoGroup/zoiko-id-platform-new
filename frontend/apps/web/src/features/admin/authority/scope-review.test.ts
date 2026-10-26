/** UX-07: the grant form does not let a broad scope through without an explicit review. */
import { describe, expect, it } from "vitest";

import { reviewRequired, scopeBreadth } from "./scope-review";

const narrow = { actions: ["invoice.read"], resources: ["invoice:inv_1"], hasValueLimit: true, hasCountLimit: false, delegate: false, depth: 0 };

describe("UX-07 grant scope", () => {
  it("lets a narrow, limited grant through without a review", () => {
    expect(scopeBreadth(narrow)).toEqual([]);
    expect(reviewRequired(scopeBreadth(narrow), false)).toBeNull();
  });

  it.each([
    ["a wildcard action", { ...narrow, actions: ["invoice.*"] }, "WILDCARD_ACTION"],
    ["every resource of a type", { ...narrow, resources: ["invoice:*"] }, "ALL_OF_A_TYPE"],
    ["every resource of every type", { ...narrow, resources: ["*"] }, "EVERY_RESOURCE"],
    ["no limits", { ...narrow, hasValueLimit: false, hasCountLimit: false }, "NO_LIMIT"],
    ["deep delegation", { ...narrow, delegate: true, depth: 3 }, "DEEP_DELEGATION"],
  ])("flags %s and refuses to submit until it is reviewed", (_label, input, code) => {
    const findings = scopeBreadth(input);
    expect(findings.map((f) => f.code)).toContain(code);
    expect(reviewRequired(findings, false)).not.toBeNull(); // blocked
    expect(reviewRequired(findings, true)).toBeNull(); // allowed once the person confirms
  });

  it("explains every finding in words", () => {
    for (const f of scopeBreadth({ ...narrow, actions: ["*"], resources: ["*"], hasValueLimit: false, hasCountLimit: false, delegate: true, depth: 5 })) {
      expect(f.message.length).toBeGreaterThan(20);
    }
  });
});
