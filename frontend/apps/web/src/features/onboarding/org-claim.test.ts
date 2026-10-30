import type { OrgClaimStatus } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { CHECKS, attemptId, buildScreening, claimKey, emptyOwner, nextStep, outcomeCopy, ownerRowErrors, pctValid, tidy } from "./org-claim";

const base: OrgClaimStatus = {
  claim_id: "c1",
  jurisdiction: "GB",
  registration_number: "00123456",
  outcome: "PROVISIONAL",
  checks: { company: "COMPLETED", person_assurance: "PENDING", authority: "PENDING", risk: "PENDING" },
  company: { status: "VERIFIED", review_reason: null, reasons: [], expires_at: null },
  screening: null,
};

describe("the four checks stay separate", () => {
  it("lists company, identity, authority and risk, each with its own help", () => {
    expect(CHECKS.map((c) => c.key)).toEqual(["company", "person_assurance", "authority", "risk"]);
    for (const c of CHECKS) expect(c.help.length).toBeGreaterThan(10);
  });
});

describe("outcome wording", () => {
  it("never calls a provisional claim verified or promises rights from it", () => {
    const p = outcomeCopy("PROVISIONAL");
    expect(p.title).toBe("Provisional");
    expect(p.body).toMatch(/not a verified organization/);
    expect(p.body).toMatch(/no owner or approver rights/);
  });
  it("names every outcome and promises no completion time", () => {
    for (const o of ["VERIFIED", "PROVISIONAL", "NEEDS_EVIDENCE", "IN_REVIEW", "RESTRICTED", "DECLINED", "STALE"] as const) {
      const c = outcomeCopy(o);
      expect(c.title).toBeTruthy();
      expect(c.body).not.toMatch(/\b\d+\s*(hours?|days?|minutes?)\b/i);
    }
  });
});

describe("holdings and owners", () => {
  it("accepts only percentages from 0 to 100", () => {
    for (const ok of ["0", "25", "12.5", "100", "33.3333"]) expect(pctValid(ok)).toBe(true);
    for (const bad of ["", "-1", "101", "abc", "1e2", "12.56789", "1,5"]) expect(pctValid(bad)).toBe(false);
  });
  it("needs a name and some stated interest, and a plausible birth year", () => {
    expect(ownerRowErrors(emptyOwner()).length).toBeGreaterThan(0);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", sharesPct: "60" })).toEqual([]);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", boardControl: true })).toEqual([]);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", sharesPct: "60", birthYear: "85" })).toEqual(["Enter a four-digit birth year."]);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", sharesPct: "160" })).toEqual(["Shares must be between 0 and 100."]);
  });
  it("tidies names and builds the request from what was typed", () => {
    expect(tidy("  Olivia   Owner ")).toBe("Olivia Owner");
    const body = buildScreening("  Alice  Smith", [
      { name: "Olivia Owner", birthYear: "1980", sharesPct: "60", votesPct: "", boardControl: false },
      { name: "Bo Board", birthYear: "", sharesPct: "", votesPct: "", boardControl: true },
    ]);
    expect(body.representative).toMatchObject({ ref: "rep", name: "Alice Smith", kind: "PERSON" });
    expect(body.owners?.map((o) => [o.ref, o.name, o.birth])).toEqual([
      ["owner1", "Olivia Owner", "1980"],
      ["owner2", "Bo Board", null],
    ]);
    expect(body.holdings).toEqual([
      { owner: "owner1", shares_pct: "60", votes_pct: null, controls: [] },
      { owner: "owner2", shares_pct: null, votes_pct: null, controls: ["APPOINT_BOARD_MAJORITY"] },
    ]);
  });
});

describe("keys and next steps", () => {
  it("makes a key per company and attempt, within the server's limit", () => {
    expect(claimKey("GB", "00123456", "a1")).toBe("claim-GB-00123456-a1");
    expect(claimKey("DE", "HRB 12345".repeat(30), "a1").length).toBeLessThanOrEqual(120);
  });
  it("gives each opening of the page its own attempt id", () => {
    expect(attemptId()).toMatch(/^[0-9a-z-]{6,8}$/);
    expect(attemptId()).not.toBe(attemptId());
  });
  it("asks for the authority details only once the company is found, then waits, then is done", () => {
    expect(nextStep(base)).toBe("authority");
    expect(nextStep({ ...base, checks: { ...base.checks, company: "PENDING" } })).toBe("wait");
    expect(nextStep({ ...base, screening: { status: "REVIEW_REQUIRED", next_action: "x", expires_at: null } })).toBe("wait");
    expect(nextStep({ ...base, outcome: "VERIFIED" })).toBe("done");
  });
});
