import { describe, expect, it } from "vitest";

import { CLAIM_LABEL, checkedOn, outcome, parseLink } from "./view";

const T = "0194f5a0-1111-7222-8333-444455556666";
const C = "0194f5a0-aaaa-7bbb-8ccc-ddddeeeeffff";
const G = "0194f5a0-9999-7888-8777-666655554444";

describe("parseLink", () => {
  it("accepts a tenant.check reference with a grant", () => {
    expect(parseLink(`${T}.${C}`, G)).toEqual({ ref: `${T}.${C}`, grant: G });
  });
  it("refuses anything else before a request is made", () => {
    for (const [ref, grant] of [
      [`${T}`, G],
      [`${T}.${C}.extra`, G],
      [`not-a-uuid.${C}`, G],
      [`${T}.${C}`, undefined],
      [`${T}.${C}`, "short"],
      ["", G],
    ] as const) {
      expect(parseLink(ref, grant)).toBeNull();
    }
  });
});

describe("outcome", () => {
  it("says confirmed only for a verified, primary-source result, and bounds what that means", () => {
    const o = outcome({ decision: "VERIFIED", assurance_tier: "PRIMARY_SOURCE_CONFIRMED" });
    expect(o.tone).toBe("confirmed");
    expect(o.detail).toMatch(/does not show anything after that time/);
  });
  it("never calls a signed-only credential confirmed by the source", () => {
    const o = outcome({ decision: "VERIFIED", assurance_tier: "ISSUER_AUTHENTICATED" });
    expect(o.tone).toBe("limited");
    expect(o.headline).not.toMatch(/^Confirmed/);
  });
  it("treats an unreachable source as not a finding about the person", () => {
    for (const decision of ["SOURCE_UNAVAILABLE", "UNVERIFIABLE", "INDETERMINATE"]) {
      const o = outcome({ decision, assurance_tier: "SELF_DECLARED" });
      expect(o.tone).toBe("unavailable");
      expect(o.detail).toMatch(/not a finding about the person/);
    }
  });
  it("says a replaced result is no longer valid, and an unknown one is not confirmed", () => {
    expect(outcome({ decision: "NO_LONGER_VALID" }).headline).toBe("No longer valid");
    expect(outcome({ decision: "NOT_VERIFIED" }).tone).toBe("not-confirmed");
    expect(outcome({}).tone).toBe("not-confirmed");
  });
  it("never claims a verified professional or any certification", () => {
    const words = ["VERIFIED", "NOT_VERIFIED", "REVIEW", "SOURCE_UNAVAILABLE", "NO_LONGER_VALID"].flatMap((decision) =>
      ["PRIMARY_SOURCE_CONFIRMED", "ISSUER_AUTHENTICATED", "SELF_DECLARED"].map((tier) => {
        const o = outcome({ decision, assurance_tier: tier });
        return `${o.headline} ${o.detail}`;
      }),
    );
    for (const w of words) expect(w).not.toMatch(/verified professional|certified|guarantee|licensed to practice/i);
  });
});

describe("labels and dates", () => {
  it("has a label for every claim class the platform checks", () => {
    for (const k of ["ACADEMIC_AWARD", "PROFESSIONAL_LICENSE", "INDUSTRY_CERTIFICATE", "CPD_RECORD", "EMPLOYMENT_ASSERTION", "INSURANCE_ASSERTION", "DISCIPLINARY_FINDING"]) {
      expect(CLAIM_LABEL[k]).toBeTruthy();
    }
  });
  it("formats a date in UTC and returns null for a bad one", () => {
    expect(checkedOn("2026-11-11T23:30:00Z")).toBe("November 11, 2026");
    expect(checkedOn("garbage")).toBeNull();
    expect(checkedOn(undefined)).toBeNull();
  });
});
