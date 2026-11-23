import type { BillingOrder, CommitmentRequest } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { canDecide, commitmentStepUp, delegateGrantStepUp, requestableOrders, seesCommitmentRequests, signaturesLeft, validCap, waitingReason } from "./commitment-logic";

const NOW = Date.parse("2026-11-04T10:00:00Z");
const req = (o: Partial<CommitmentRequest> = {}): CommitmentRequest => ({
  request_id: "r1",
  order_id: "o1",
  currency: "USD",
  term_months: 12,
  monthly: "1000",
  exposure: "12000",
  exposure_usd_minor: 1200000,
  tier: "T2",
  needs: [],
  review_required: false,
  reviewed_at: null,
  snapshot_hash: "a".repeat(64),
  policy_version: "1",
  state: "PENDING_APPROVAL",
  requested_by: "p0",
  created_at: "2026-11-03T10:00:00Z",
  expires_at: "2026-11-10T10:00:00Z",
  approved_at: null,
  approval_expires_at: null,
  approvals: [],
  ...o,
});

describe("commitment approval", () => {
  it("lets a person who has not signed decide a waiting request", () => {
    expect(canDecide(req(), "p1", NOW)).toBe(true);
    expect(waitingReason(req(), "p1", NOW)).toBeNull();
  });

  it("does not let the same person sign twice, and says why", () => {
    const r = req({ approvals: [{ authority: "OWNER", principal_id: "p1", decision: "APPROVE", decided_at: "2026-11-03T11:00:00Z" }] });
    expect(canDecide(r, "p1", NOW)).toBe(false);
    expect(waitingReason(r, "p1", NOW)).toMatch(/already signed/);
    expect(canDecide(r, "p2", NOW)).toBe(true);
  });

  it("waits for the review mark and for the expiry", () => {
    expect(canDecide(req({ review_required: true }), "p1", NOW)).toBe(false);
    expect(waitingReason(req({ review_required: true }), "p1", NOW)).toMatch(/Controller or CFO/);
    expect(canDecide(req({ review_required: true, reviewed_at: "2026-11-04T09:00:00Z" }), "p1", NOW)).toBe(true);
    expect(canDecide(req({ expires_at: "2026-11-04T09:00:00Z" }), "p1", NOW)).toBe(false);
    expect(canDecide(req({ state: "APPROVED" }), "p1", NOW)).toBe(false);
  });

  it("counts the signatures still needed, two for the top tier", () => {
    expect(signaturesLeft(req())).toBe(1);
    expect(signaturesLeft(req({ tier: "T3" }))).toBe(2);
    expect(signaturesLeft(req({ tier: "T3", approvals: [{ authority: "OWNER", principal_id: "p1", decision: "APPROVE", decided_at: "x" }] }))).toBe(1);
  });

  it("offers only orders that have not started and are not already waiting", () => {
    const order = (id: string, activated: string | null): BillingOrder => ({ order_id: id, account_id: "a", tenant_id: "t", plan: "GROWTH", status: "SIGNED", pilot: false, pilot_ends_at: null, effective_from: "x", term_months: 12, amends_order_id: null, activated_at: activated, lines: [] });
    const out = requestableOrders([order("o1", null), order("o2", null), order("o3", "x")], [req({ order_id: "o2" })]);
    expect(out.map((o) => o.order_id)).toEqual(["o1"]);
  });

  it("names exactly what the server hashes for the step-up", () => {
    expect(commitmentStepUp("t1", req(), "approve")).toEqual({
      action: "execute-sensitive-approval-rejection",
      resource: "tenant:t1",
      change: { operation: "COMMITMENT_APPROVE", request_id: "r1", snapshot_hash: "a".repeat(64) },
    });
    expect(delegateGrantStepUp("t1", { principal_id: "p", kind: "BILLING_SPEND", cap_usd: "500" }).change).toEqual({
      operation: "COMMITMENT_DELEGATE",
      grantee: "p",
      kind: "BILLING_SPEND",
      cap_usd: "500",
    });
  });

  it("checks a limit like the server: positive, at most two decimals", () => {
    expect(validCap("2500")).toBe(true);
    expect(validCap("2500.50")).toBe(true);
    expect(validCap("0")).toBe(false);
    expect(validCap("1.234")).toBe(false);
    expect(validCap("-5")).toBe(false);
  });
});

describe("who sees the commitment requests (CR-140)", () => {
  const grant = (over: Partial<{ grantee_principal_id: string; permissions: string[]; revoked_at: string | null; expires_at: string | null }> = {}) => ({
    grantee_principal_id: "p1",
    permissions: ["billing.overview.read", "billing.invoice.read"],
    revoked_at: null,
    expires_at: null,
    ...over,
  });
  it("an Owner always does", () => {
    expect(seesCommitmentRequests("o", "OWNER", [])).toBe(true);
  });
  it("a person with a limit and no Billing grant does not", () => {
    expect(seesCommitmentRequests("p1", "APPROVER", [])).toBe(false);
  });
  it("a live Billing grant with the overview permission does", () => {
    expect(seesCommitmentRequests("p1", "APPROVER", [grant()])).toBe(true);
  });
  it("a revoked, expired, other person's or invoice-only grant does not", () => {
    const now = new Date("2026-11-20T00:00:00Z");
    expect(seesCommitmentRequests("p1", "MEMBER", [grant({ revoked_at: "2026-11-01T00:00:00Z" })], now)).toBe(false);
    expect(seesCommitmentRequests("p1", "MEMBER", [grant({ expires_at: "2026-11-10T00:00:00Z" })], now)).toBe(false);
    expect(seesCommitmentRequests("p1", "MEMBER", [grant({ grantee_principal_id: "p2" })], now)).toBe(false);
    expect(seesCommitmentRequests("p1", "MEMBER", [grant({ permissions: ["billing.invoice.read"] })], now)).toBe(false);
  });
});
