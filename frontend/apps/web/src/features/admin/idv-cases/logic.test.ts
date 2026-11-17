import { describe, expect, it } from "vitest";

import { canReview, claimText, DECISION, explainReason, problemText, SIGNAL_LABEL } from "./logic";

describe("decisions", () => {
  it("never names an assurance level and says missing evidence is not a finding", () => {
    for (const d of Object.values(DECISION)) expect(d.meaning).not.toMatch(/IAL|AAL|eIDAS|assurance/i);
    expect(DECISION.INDETERMINATE.meaning).toMatch(/not a finding/);
    expect(Object.keys(DECISION)).toEqual(["APPROVED", "REVIEW_REQUIRED", "REJECTED", "INDETERMINATE", "ABANDONED"]);
  });
});

describe("reasons and claims", () => {
  it("says a missing trust root means the chip is read but not verified", () => {
    expect(explainReason("CHIP_TRUST_ROOT_MISSING")).toMatch(/cannot be called verified/);
    expect(explainReason("PRESENTATION_ATTACK_DETECTED")).toMatch(/does not cancel it/);
  });
  it("words the biometric switch and leaves unknown codes visible", () => {
    expect(explainReason("BIOMETRICS_OFF:NO_IMPACT_ASSESSMENT")).toBe("Biometric steps are off for this market: no impact assessment.");
    expect(explainReason("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(claimText("CHIP_NOT_CLONED")).toBe("The chip is not a copy");
  });
  it("labels all six signals", () => {
    expect(Object.keys(SIGNAL_LABEL)).toHaveLength(6);
  });
  it("words each refusal", () => {
    expect(problemText("REVIEWER_IS_THE_APPLICANT")).toMatch(/own case/);
  });
});

describe("review", () => {
  it("offers a signature only on an open alternative-route case waiting for a person", () => {
    const base = { state: "OPEN", route: "PASSPORT_OPTICAL_REVIEW", reviewers: 0, decision: "REVIEW_REQUIRED" as const };
    expect(canReview(base)).toBe(true);
    expect(canReview({ ...base, route: "CHIP" })).toBe(false);
    expect(canReview({ ...base, state: "CLOSED" })).toBe(false);
    expect(canReview({ ...base, decision: "INDETERMINATE" })).toBe(false);
  });
});
