import type { VerifyApiCase } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { canWithdraw, CASE_STATE, draftProblem, EMPTY_DRAFT, kindLabel, outcomeText, toRequest } from "./cases-logic";

const SESSION = "vfs_" + "a".repeat(32);
const REASON = "The applicant was verified in person.";
const c = (over: Partial<VerifyApiCase> = {}): VerifyApiCase => ({
  case_id: "c1",
  kind: "DISPUTE",
  status: "OPEN",
  session_id: SESSION,
  reason: REASON,
  note: null,
  sessions_erased: null,
  created_at: "2026-11-25T10:00:00Z",
  decided_at: null,
  ...over,
});

describe("the case form", () => {
  it("asks for a session for a dispute or appeal and a reference for an erasure", () => {
    expect(draftProblem(EMPTY_DRAFT)).toContain("session ID");
    expect(draftProblem({ kind: "ERASURE", target: "", reason: REASON })).toContain("customer reference");
    expect(draftProblem({ kind: "DISPUTE", target: "not-a-session", reason: REASON })).toContain("vfs_");
    expect(draftProblem({ kind: "DISPUTE", target: SESSION, reason: REASON })).toBeNull();
    expect(draftProblem({ kind: "ERASURE", target: "customer-48201", reason: REASON })).toBeNull();
    expect(draftProblem({ kind: "ERASURE", target: "x".repeat(129), reason: REASON })).toContain("128");
  });
  it("needs a real reason", () => {
    expect(draftProblem({ kind: "DISPUTE", target: SESSION, reason: "too short" })).toContain("at least 10");
    expect(draftProblem({ kind: "DISPUTE", target: SESSION, reason: "x".repeat(1001) })).toContain("at most 1000");
  });
  it("sends a session or a reference, never both, with the reason tidied", () => {
    expect(toRequest({ kind: "APPEAL", target: ` ${SESSION} `, reason: "  The  applicant   passed. " })).toEqual({ kind: "APPEAL", reason: "The applicant passed.", session_id: SESSION });
    expect(toRequest({ kind: "ERASURE", target: " customer-1 ", reason: REASON })).toEqual({ kind: "ERASURE", reason: REASON, customer_reference: "customer-1" });
  });
});

describe("how a case reads", () => {
  it("has plain words for every state and kind", () => {
    for (const s of ["OPEN", "IN_REVIEW", "RESOLVED", "REJECTED", "WITHDRAWN"]) expect(CASE_STATE[s]!.label).toBeTruthy();
    expect(kindLabel("ERASURE")).toBe("Erase a customer reference");
    expect(kindLabel("UNKNOWN")).toBe("UNKNOWN");
  });
  it("lets the company withdraw only what is not decided", () => {
    expect(canWithdraw(c())).toBe(true);
    expect(canWithdraw(c({ status: "IN_REVIEW" }))).toBe(true);
    for (const status of ["RESOLVED", "REJECTED", "WITHDRAWN"] as const) expect(canWithdraw(c({ status }))).toBe(false);
  });
  it("says how many sessions an erasure cleaned, and nothing for other cases", () => {
    expect(outcomeText(c({ kind: "ERASURE", status: "RESOLVED", sessions_erased: 2, session_id: null }))).toContain("2 sessions were");
    expect(outcomeText(c({ kind: "ERASURE", status: "RESOLVED", sessions_erased: 1, session_id: null }))).toContain("1 session was");
    expect(outcomeText(c({ kind: "ERASURE", status: "OPEN", session_id: null }))).toBeNull();
    expect(outcomeText(c({ status: "RESOLVED" }))).toBeNull();
  });
});
