import type { DisputeCase, RefundCase } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { deadlineText, disputeActions, emptyRefundForm, lastPeriod, money, parseLegs, refundActions, rolesStillNeeded, validateRefund, validPeriod } from "./payments-logic";

const T = "11111111-1111-4111-8111-111111111111";

describe("refund form (WF09-OP-05, WF09-OP-08)", () => {
  it("says what is missing before the server does", () => {
    const e = validateRefund(emptyRefundForm());
    expect(Object.keys(e).sort()).toEqual(["amount", "payment", "reason", "tenant"]);
  });
  it("asks for evidence on every category except goodwill", () => {
    const f = { ...emptyRefundForm(), tenant: T, payment: T, amount: "10.00", reason: "x", category: "CORRECTION" };
    expect(validateRefund(f).evidence).toBeDefined();
    expect(validateRefund({ ...f, category: "GOODWILL" })).toEqual({});
  });
  it("refuses an amount with more than two decimals or below one cent", () => {
    const f = { ...emptyRefundForm(), tenant: T, payment: T, reason: "x" };
    expect(validateRefund({ ...f, amount: "1.005" }).amount).toBeDefined();
    expect(validateRefund({ ...f, amount: "0" }).amount).toBeDefined();
  });
});

describe("what a case offers (WF09-OP-05, WF09-OP-06)", () => {
  const base = { required_roles: ["CONTROLLER", "CFO"], approvals: [{ role: "CONTROLLER", decision: "APPROVE" }] } as unknown as RefundCase;
  it("offers a signature, then queueing, and nothing after", () => {
    expect(refundActions({ ...base, state: "REQUESTED" })).toEqual(["sign", "cancel"]);
    expect(refundActions({ ...base, state: "APPROVED" })).toEqual(["queue", "cancel"]);
    for (const s of ["EXECUTION_PENDING", "PROCESSOR_PENDING", "UNKNOWN", "SUCCEEDED", "FAILED"] as const) expect(refundActions({ ...base, state: s })).toEqual([]);
  });
  it("lists the roles still needed", () => {
    expect(rolesStillNeeded(base)).toEqual(["CFO"]);
  });
});

describe("disputes (WF09-OP-07)", () => {
  const d = (state: string, owner: string | null = "x") => ({ state, owner }) as unknown as DisputeCase;
  it("never offers an outcome: a person decides a strategy, the processor closes", () => {
    expect(disputeActions(d("CLOSED_WON"))).toEqual([]);
    expect(disputeActions(d("SUBMISSION_RECORDED"))).toEqual(["evidence", "verify"]);
    expect(disputeActions(d("OPEN", null))).toEqual(["assign", "evidence", "decide"]);
  });
  it("shows the processor's own deadline and says when there is none", () => {
    expect(deadlineText({ due_by: null, zone: "UTC", seconds_left: null, status: "NO_DEADLINE_FROM_PROCESSOR" })).toMatch(/no deadline/);
    expect(deadlineText({ due_by: "2026-11-10T12:00:00Z", zone: "UTC", seconds_left: 90_000, status: "OK" })).toMatch(/1 d 1 h left/);
    expect(deadlineText({ due_by: "2026-11-01T12:00:00Z", zone: "UTC", seconds_left: 0, status: "PASSED" })).toMatch(/passed/);
  });
});

describe("reconciliation (WF09-OP-09)", () => {
  it("picks last month as the period to close, across a year end", () => {
    expect(lastPeriod(new Date("2026-11-04T00:00:00Z"))).toBe("2026-10");
    expect(lastPeriod(new Date("2027-01-02T00:00:00Z"))).toBe("2026-12");
    expect(validPeriod("2026-13")).toBe(false);
  });
  it("reads pasted legs and names every bad line", () => {
    const { legs, errors } = parseLegs("leg_type,ref,link_ref,amount_minor,currency,occurred_on\nPAYOUT_LINE,po_1,pi_1,5000,usd,2026-03-12\nBANK_LINE,po_1,,50.5,USD,2026-03-13\nNOPE,x,,1,USD,2026-03-01");
    expect(legs).toEqual([{ leg_type: "PAYOUT_LINE", ref: "po_1", link_ref: "pi_1", amount_minor: 5000, currency: "USD", occurred_on: "2026-03-12" }]);
    expect(errors).toHaveLength(2);
  });
  it("words money in minor units with the right decimals", () => {
    expect(money(12345, "USD")).toBe("123.45 USD");
    expect(money(500, "JPY")).toBe("500 JPY");
  });
});
