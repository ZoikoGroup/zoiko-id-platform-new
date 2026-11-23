import type { BillingOrder, CommitmentRequest, CommitmentState, CommitmentTier } from "@zoiko/api-client";

/** The commitment approval screen (ZID-UX-COM-008 B05, ZID-COM-CL-004 section 04). The server derives the amount, the tier and who must
 * sign; this only words it and decides which buttons to show. */

export const TIER_COPY: Record<CommitmentTier, { label: string; help: string }> = {
  T1: { label: "Tier 1", help: "Below the first threshold. One authorised approver signs." },
  T2: { label: "Tier 2", help: "Between the thresholds. One authorised approver signs." },
  T3: { label: "Tier 3", help: "Above the top threshold. Two different people sign." },
};

export const STATE_COPY: Record<CommitmentState, { label: string; tone: "info" | "ok" | "danger" | "neutral" | "warn" }> = {
  PENDING_APPROVAL: { label: "Waiting for approval", tone: "info" },
  APPROVED: { label: "Approved", tone: "ok" },
  REJECTED: { label: "Rejected", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "warn" },
  CANCELED: { label: "Withdrawn", tone: "neutral" },
};

export const AUTHORITY_LABEL: Record<string, string> = {
  OWNER: "Owner",
  BILLING_SPEND: "Billing Admin with a spending limit",
  PROCUREMENT_APPROVER: "Procurement Approver",
};

export const authorityLabel = (a: string) => AUTHORITY_LABEL[a] ?? a;

export const DELEGATE_KIND_LABEL = {
  BILLING_SPEND: "Billing Admin spending limit",
  PROCUREMENT_APPROVER: "Procurement Approver",
} as const;

/** Orders that can be put forward: signed and not yet running, and not already waiting on a request. */
export function requestableOrders(orders: BillingOrder[], requests: CommitmentRequest[]): BillingOrder[] {
  const open = new Set(requests.filter((r) => r.state === "PENDING_APPROVAL" || r.state === "APPROVED").map((r) => r.order_id));
  return orders.filter((o) => !open.has(o.order_id) && o.activated_at === null && o.status !== "CANCELED");
}

/** A person who already signed does not sign again, and a request that is no longer waiting cannot be decided. */
export function canDecide(r: CommitmentRequest, me: string | undefined, nowMs: number): boolean {
  if (r.state !== "PENDING_APPROVAL") return false;
  if (Date.parse(r.expires_at) <= nowMs) return false;
  if (r.review_required && !r.reviewed_at) return false;
  return !r.approvals.some((a) => a.principal_id === me);
}

/** Why Approve is off, so the page can say it instead of greying it out silently. */
export function waitingReason(r: CommitmentRequest, me: string | undefined, nowMs: number): string | null {
  if (r.state !== "PENDING_APPROVAL") return null;
  if (Date.parse(r.expires_at) <= nowMs) return "This request has passed its expiry. Ask for a new one.";
  if (r.review_required && !r.reviewed_at) return "Non-standard terms: a Zoiko Controller or CFO review has to be recorded before anyone can sign.";
  if (r.approvals.some((a) => a.principal_id === me)) return "You have already signed this request. A different person has to sign next.";
  return null;
}

/** How many signatures are still missing (Tier 3 needs two different people). */
export function signaturesLeft(r: CommitmentRequest): number {
  const need = r.needs.length > 0 ? r.needs.length : r.tier === "T3" ? 2 : 1;
  const have = r.approvals.filter((a) => a.decision === "APPROVE").length;
  return Math.max(0, need - have);
}

/** The step-up has to name exactly what the server hashes (tenant/api/commitments.py `_decide`). */
export function commitmentStepUp(tenantId: string, r: Pick<CommitmentRequest, "request_id" | "snapshot_hash">, decision: "approve" | "reject") {
  return {
    action: "execute-sensitive-approval-rejection",
    resource: `tenant:${tenantId}`,
    change: { operation: `COMMITMENT_${decision.toUpperCase()}`, request_id: r.request_id, snapshot_hash: r.snapshot_hash },
  };
}

export const delegateStepUp = {
  action: "grant-expand-transfer-or-revoke-high-impact-delegated-authority",
} as const;

export function delegateGrantStepUp(tenantId: string, v: { principal_id: string; kind: string; cap_usd: string }) {
  return {
    action: delegateStepUp.action,
    resource: `tenant:${tenantId}`,
    change: { operation: "COMMITMENT_DELEGATE", grantee: v.principal_id, kind: v.kind, cap_usd: v.cap_usd },
  };
}

export function delegateRevokeStepUp(tenantId: string, delegateId: string) {
  return {
    action: delegateStepUp.action,
    resource: `tenant:${tenantId}`,
    change: { operation: "COMMITMENT_DELEGATE_REVOKE", delegate_id: delegateId },
  };
}

/** The server takes Decimal(14,2) and refuses zero. */
export function validCap(v: string): boolean {
  return /^\d{1,12}(\.\d{1,2})?$/.test(v.trim()) && Number(v) > 0;
}

/** Can this person see the commitment requests they are meant to sign? Listing them needs the billing overview permission, which comes from an Owner role or a live Billing grant.
 * A limit alone does not give it, so the Owner is told when a person holding a limit would find nothing to approve. */
export function seesCommitmentRequests(
  principalId: string,
  role: string | undefined,
  grants: { grantee_principal_id: string; permissions: string[]; revoked_at: string | null; expires_at: string | null }[],
  now: Date = new Date(),
): boolean {
  if (role === "OWNER") return true;
  return grants.some(
    (g) =>
      g.grantee_principal_id === principalId &&
      g.revoked_at === null &&
      (g.expires_at === null || new Date(g.expires_at).getTime() > now.getTime()) &&
      g.permissions.includes("billing.overview.read"),
  );
}
