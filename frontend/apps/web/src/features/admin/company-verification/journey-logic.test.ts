import { describe, expect, it } from "vitest";

import { BACK_TO, companyVerified, identityApproved, journey, nextStep } from "./journey-logic";

const states = (steps: ReturnType<typeof journey>) => steps.map((s) => `${s.id}:${s.state}`);

describe("the path to a verified company", () => {
  it("starts with the person's own identity, and locks the rest", () => {
    const s = journey({ identityDone: false, companyVerified: false });
    expect(states(s)).toEqual(["identity:current", "company:locked", "onboarding:locked"]);
    expect(s[0]?.href).toBe(`/account/identity-proofing?next=${encodeURIComponent(BACK_TO)}`);
    expect(s[1]?.href).toBeNull(); // the company check cannot be started first
    expect(s[2]?.href).toBeNull();
  });

  it("opens the company step once identity is verified", () => {
    const s = journey({ identityDone: true, companyVerified: false });
    expect(states(s)).toEqual(["identity:done", "company:current", "onboarding:locked"]);
    expect(s[0]?.href).toBeNull();
    expect(s[1]?.href).toBe("/admin/organization/claim");
    expect(nextStep(s)?.id).toBe("company");
  });

  it("opens onboarding once the company is verified", () => {
    const s = journey({ identityDone: true, companyVerified: true });
    expect(states(s)).toEqual(["identity:done", "company:done", "onboarding:current"]);
    expect(s[2]?.href).toBe("/admin/onboarding");
    expect(nextStep(s)?.id).toBe("onboarding");
  });

  it("never opens onboarding on a verified company whose person has no verified identity", () => {
    // an owner who joined an already-verified company still verifies themselves first; the company stays done
    const s = journey({ identityDone: false, companyVerified: true });
    expect(states(s)[0]).toBe("identity:current");
    expect(nextStep(s)?.id).toBe("identity");
  });

  it("counts an identity check only when the server approved it", () => {
    expect(identityApproved([])).toBe(false);
    expect(identityApproved([{ decision: "REVIEW_REQUIRED" }, { decision: "INDETERMINATE" }])).toBe(false);
    expect(identityApproved([{ decision: "REJECTED" }, { decision: "APPROVED" }])).toBe(true);
  });

  it("reads the company as verified only once the workspace has moved past company verification", () => {
    for (const s of ["DRAFT", "ORGANIZATION_VERIFYING", undefined, null, "", "BLOCKED", "SUSPENDED", "CLOSED", "OFFBOARDING"]) expect(companyVerified(s as string)).toBe(false);
    for (const s of ["CONFIGURING", "INTEGRATING", "SHADOW", "PILOT", "ACTIVE"]) expect(companyVerified(s)).toBe(true);
  });
});
