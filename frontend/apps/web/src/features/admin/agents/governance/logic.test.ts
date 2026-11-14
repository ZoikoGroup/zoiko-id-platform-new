/** The wording and arithmetic of the agent governance screens (ZID-AG-WF-002). T44 and T45: states are never overstated, money is never a float. */
import type { GovRequest } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { CODE_TEXT, KIND_LABEL, LEVEL, STATE_LABEL, codeText, money, requestStateText, reviewAvailability, signatureProgress, usagePercent, usedAmount, validAmount } from "./logic";

const req = (over: Partial<GovRequest> = {}): GovRequest => ({
  request_id: "r1",
  agent_id: "a1",
  kind: "PUBLISH",
  state: "PENDING",
  requested_by: "p1",
  policy_hash: `sha256:${"a".repeat(64)}`,
  base_policy_hash: null,
  delta_hash: `sha256:${"b".repeat(64)}`,
  required_slots: ["SECURITY", "AUTHORITY"],
  open_slots: ["SECURITY", "AUTHORITY"],
  signatures: [],
  created_at: "2026-11-14T10:00:00Z",
  expires_at: "2026-11-15T10:00:00Z",
  expired: false,
  ...over,
});

describe("what a request is called", () => {
  it("never shows an approved request as live (T45)", () => {
    expect(requestStateText(req({ state: "APPROVED" })).label).toBe("Approved, not yet live");
    expect(requestStateText(req({ state: "APPROVED" })).label).not.toMatch(/^live$/i);
    expect(requestStateText(req({ state: "APPLIED" })).label).toBe("Live");
  });
  it("shows a request past its expiry as expired even if the list was loaded before", () => {
    expect(requestStateText(req({ expired: true })).label).toBe("Expired");
  });
  it("names every state and every kind", () => {
    for (const s of ["PENDING", "CLARIFICATION", "APPROVED", "REJECTED", "EXPIRED", "INVALIDATED", "APPLIED"] as const) expect(STATE_LABEL[s].label).toBeTruthy();
    for (const k of Object.keys(KIND_LABEL)) expect(KIND_LABEL[k as GovRequest["kind"]]).toBeTruthy();
    expect(KIND_LABEL.PRIVILEGE_MUTATION).toMatch(/never approvable/);
  });
});

describe("who may review", () => {
  it("lets a qualified, independent reviewer act", () => {
    expect(reviewAvailability(req({ can_review: true }))).toEqual({ canReview: true, why: null });
  });
  it("tells the requester why they cannot (GOV01-04, GOV04)", () => {
    const r = reviewAvailability(req({ review_unavailable: "SEPARATION_OF_DUTIES" }));
    expect(r.canReview).toBe(false);
    expect(r.why).toMatch(/You requested this change/);
  });
  it("explains a closed request by its state and an unqualified viewer by the slot", () => {
    expect(reviewAvailability(req({ state: "REJECTED" })).why).toBe("Rejected");
    expect(reviewAvailability(req()).why).toMatch(/signer slot/);
  });
  it("counts signatures", () => {
    expect(signatureProgress(req({ open_slots: ["AUTHORITY"] }))).toBe("1 of 2 signatures");
  });
});

describe("money (GOV03-02)", () => {
  it("keeps decimals as strings and shows the currency's own places", () => {
    expect(money("0.100000", "USD")).toBe("USD 0.10");
    expect(money("20.000000", "USD")).toBe("USD 20.00");
    expect(money("5", "JPY")).toBe("JPY 5");
    expect(money("0.5", "EUR")).toBe("EUR 0.50");
  });
  it("adds committed and reserved without a float (0.1 + 0.2 is exactly 0.3)", () => {
    expect(usedAmount({ committed: "0.1", reserved: "0.2" })).toBe("0.30");
    expect(usedAmount({ committed: "19.999999", reserved: "0.000001" })).toBe("20.000000");
  });
  it("validates what a person types before the server does", () => {
    expect(validAmount("5", "USD")).toBeNull();
    expect(validAmount("0.10", "USD")).toBeNull();
    expect(validAmount("0.001", "USD")).toMatch(/2 decimal places/);
    expect(validAmount("1.5", "JPY")).toMatch(/no decimal places/);
    for (const bad of ["", "-1", "1e3", "abc", "1,5", " "]) expect(validAmount(bad, "USD")).not.toBeNull();
  });
  it("keeps a bar's width in range", () => {
    expect(usagePercent("0.00", "20")).toBe(0);
    expect(usagePercent("10", "20")).toBe(50);
    expect(usagePercent("30", "20")).toBe(100);
    expect(usagePercent("1", "0")).toBe(100);
  });
});

describe("levels are text, never colour alone (GOV03-04)", () => {
  it("has a label and a hint for each level, and says hard stop at the cap", () => {
    for (const l of Object.values(LEVEL)) {
      expect(l.label.length).toBeGreaterThanOrEqual(3);
      expect(l.hint.length).toBeGreaterThan(3);
    }
    expect(LEVEL.EXHAUSTED.label).toMatch(/Hard stop/);
    expect(LEVEL.WARN_70.hint).toMatch(/warning only/i);
  });
});

describe("refusal wording", () => {
  it("explains every section 13 decision code and falls back safely", () => {
    for (const c of ["AGENT_INACTIVE", "TENANT_SCOPE_DENIED", "SCOPE_PROHIBITED", "APPROVAL_REQUIRED", "SPONSOR_INVALID", "BUDGET_EXCEEDED", "USAGE_LIMIT", "POLICY_UNAVAILABLE", "EMERGENCY_FROZEN"]) {
      expect(CODE_TEXT[c]).toBeTruthy();
    }
    expect(codeText("SOMETHING_NEW")).toBe("The platform refused this request.");
    expect(codeText(null)).toBe("");
  });
  it("says a prohibition cannot be lifted by an approval, and a failed read is a refusal", () => {
    expect(CODE_TEXT.SCOPE_PROHIBITED).toMatch(/No approval can change that/);
    expect(CODE_TEXT.POLICY_UNAVAILABLE).toMatch(/fail closed/);
  });
});
