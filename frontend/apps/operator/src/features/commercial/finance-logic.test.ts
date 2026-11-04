import type { FinanceRequest } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { emptyForm, executionKey, offeredActions, openBody, rolesStillNeeded, validateOpen } from "./finance-logic";

const NOW = Date.parse("2026-11-04T10:00:00Z");
const UUID = "0194a2b3-1111-7222-8333-444455556666";
const req = (o: Partial<FinanceRequest> = {}): FinanceRequest => ({
  request_id: UUID,
  kind: "CREDIT_NOTE",
  tenant_id: UUID,
  invoice_id: UUID,
  currency: "USD",
  amount: "900.00",
  usd_minor: 90000,
  exposure_usd_minor: 90000,
  category: "CORRECTION",
  reason: "billed twice",
  snapshot_hash: "b".repeat(64),
  policy_version: "1",
  required_roles: ["FINANCE_OPS_LEAD", "CFO"],
  state: "PENDING_APPROVAL",
  requested_by: "p0",
  created_at: "2026-11-03T10:00:00Z",
  expires_at: "2026-11-10T10:00:00Z",
  approved_at: null,
  approval_expires_at: null,
  approvals: [],
  result: null,
  ...o,
});

describe("finance request form", () => {
  it("asks for the tenant, invoice, amount, reason and evidence of a correction", () => {
    const e = validateOpen(emptyForm(), NOW, null);
    expect(Object.keys(e).sort()).toEqual(["amount", "evidence", "invoice", "reason", "tenant"]);
  });

  it("does not ask for evidence for goodwill", () => {
    const f = { ...emptyForm(), category: "GOODWILL" as const, tenant: UUID, invoice: UUID, amount: "10", reason: "x" };
    expect(validateOpen(f, NOW, null)).toEqual({});
  });

  it("asks a pilot for program, sponsor and an end date inside the policy window", () => {
    const f = { ...emptyForm("PILOT_CREDIT"), tenant: UUID, amount: "100", reason: "x" };
    expect(Object.keys(validateOpen(f, NOW, 90)).sort()).toEqual(["endsAt", "program", "sponsor"]);
    const ok = { ...f, program: "P-1", sponsor: "Ana", endsAt: "2026-12-01T00:00:00Z" };
    expect(validateOpen(ok, NOW, 90)).toEqual({});
    expect(validateOpen({ ...ok, endsAt: "2027-06-01T00:00:00Z" }, NOW, 90).endsAt).toMatch(/90 days/);
  });

  it("sends only what belongs to the kind", () => {
    const credit = openBody({ ...emptyForm(), tenant: UUID.toUpperCase(), invoice: UUID, amount: " 5 ", reason: " r ", evidence: " e ", disposition: "REFUND" });
    expect(credit).toMatchObject({ kind: "CREDIT_NOTE", target_tenant_id: UUID, invoice_id: UUID, amount: "5", reason: "r", evidence_ref: "e", disposition: "REFUND", ends_at: null, program_ref: null });
    const pilot = openBody({ ...emptyForm("PILOT_CREDIT"), tenant: UUID, amount: "5", reason: "r", program: "P", sponsor: "S", endsAt: "2026-12-01T00:00:00Z" });
    expect(pilot).toMatchObject({ kind: "PILOT_CREDIT", invoice_id: null, evidence_ref: null, program_ref: "P", sponsor: "S" });
    expect(pilot.ends_at).toBe("2026-12-01T00:00:00.000Z");
  });
});

describe("finance request actions", () => {
  it("lists the roles that have not signed yet", () => {
    const r = req({ approvals: [{ role: "CFO", principal_id: "p1", decision: "APPROVE", decided_at: "x" }] });
    expect(rolesStillNeeded(r)).toEqual(["FINANCE_OPS_LEAD"]);
  });

  it("offers signing while roles are missing and nothing after expiry", () => {
    expect(offeredActions(req(), NOW)).toEqual(["sign", "cancel"]);
    expect(offeredActions(req({ expires_at: "2026-11-04T09:00:00Z" }), NOW)).toEqual([]);
  });

  it("offers running an approved request and a retry of a failed one, until the approval lapses", () => {
    expect(offeredActions(req({ state: "APPROVED", approval_expires_at: "2026-11-20T00:00:00Z" }), NOW)).toEqual(["execute"]);
    expect(offeredActions(req({ state: "APPLICATION_FAILED" }), NOW)).toEqual(["execute"]);
    expect(offeredActions(req({ state: "APPROVED", approval_expires_at: "2026-11-04T09:00:00Z" }), NOW)).toEqual([]);
    expect(offeredActions(req({ state: "APPLIED" }), NOW)).toEqual([]);
  });

  it("builds one key per attempt", () => {
    expect(executionKey("r", "a1")).toBe("exec-r-a1");
    expect(executionKey("r", "a1")).not.toBe(executionKey("r", "a2"));
  });
});
