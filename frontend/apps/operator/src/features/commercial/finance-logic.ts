import type { FinanceKind, FinanceOpenIn, FinanceRequest, FinanceState } from "@zoiko/api-client";

/** The Finance and RevOps console (ZID-UX-COM-009, F01 to F04). The server chooses the tier, the approvers and who may run it; these
 * helpers only word the facts, check the form the way the server will, and decide which buttons are worth showing. */

export const ROLE_LABEL: Record<string, string> = {
  AE: "Account executive",
  CCO: "Chief commercial officer",
  CFO: "CFO",
  FOUNDER: "Founder",
  GROUP_CFO: "Group CFO",
  REVOPS: "Revenue operations",
  FINANCE_OPS_LEAD: "Finance operations lead",
  CONTROLLER: "Controller",
  DISPUTES_SPECIALIST: "Disputes specialist",
  FINANCE_EXECUTOR: "Finance executor",
};
export const roleLabel = (r: string) => ROLE_LABEL[r] ?? r;

export const KIND_LABEL: Record<FinanceKind, string> = { CREDIT_NOTE: "Credit note", PILOT_CREDIT: "Pilot credit" };

export const STATE_LABEL: Record<FinanceState, { label: string; tone: "info" | "ok" | "warn" | "danger" | "neutral" }> = {
  PENDING_APPROVAL: { label: "Waiting for approval", tone: "info" },
  APPROVED: { label: "Approved, not yet run", tone: "ok" },
  APPLYING: { label: "Being applied", tone: "info" },
  APPLIED: { label: "Applied", tone: "ok" },
  APPLICATION_FAILED: { label: "Application failed", tone: "danger" },
  REJECTED: { label: "Rejected", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "warn" },
  CANCELED: { label: "Withdrawn", tone: "neutral" },
};

export const DISPOSITIONS = [
  { id: "", label: "Not needed (the invoice is unpaid)" },
  { id: "REFUND", label: "Refund the money already taken" },
  { id: "CUSTOMER_BALANCE", label: "Hold as a customer balance" },
  { id: "OUT_OF_BAND", label: "Settled outside the platform" },
];

export const CATEGORIES: Record<FinanceKind, { id: FinanceOpenIn["category"]; label: string }[]> = {
  CREDIT_NOTE: [
    { id: "CORRECTION", label: "Correction of an error" },
    { id: "GOODWILL", label: "Goodwill" },
  ],
  PILOT_CREDIT: [{ id: "PILOT", label: "Pilot benefit" }],
};

export type OpenForm = {
  kind: FinanceKind;
  tenant: string;
  invoice: string;
  amount: string;
  currency: string;
  category: FinanceOpenIn["category"];
  reason: string;
  endsAt: string;
  evidence: string;
  disposition: string;
  program: string;
  sponsor: string;
};

export const emptyForm = (kind: FinanceKind = "CREDIT_NOTE"): OpenForm => ({
  kind,
  tenant: "",
  invoice: "",
  amount: "",
  currency: "USD",
  category: CATEGORIES[kind][0]!.id,
  reason: "",
  endsAt: "",
  evidence: "",
  disposition: "",
  program: "",
  sponsor: "",
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** First problem per field, in the words the server's own refusals use. The server checks all of it again. */
export function validateOpen(f: OpenForm, now: number, maxDays: number | null): Partial<Record<keyof OpenForm, string>> {
  const e: Partial<Record<keyof OpenForm, string>> = {};
  if (!UUID.test(f.tenant.trim())) e.tenant = "Enter the organization's tenant ID.";
  if (!/^\d{1,12}(\.\d{1,2})?$/.test(f.amount.trim()) || Number(f.amount) <= 0) e.amount = "An amount above zero with at most two decimals.";
  if (!/^[A-Z]{3}$/.test(f.currency.trim())) e.currency = "A three-letter currency code, for example USD.";
  if (!f.reason.trim()) e.reason = "Say why.";
  if (f.kind === "CREDIT_NOTE") {
    if (!UUID.test(f.invoice.trim())) e.invoice = "A credit note corrects one issued invoice: enter its ID.";
    if (f.category === "CORRECTION" && !f.evidence.trim()) e.evidence = "A correction names the records that show the error.";
  } else {
    if (!f.program.trim()) e.program = "Name the approved pilot program.";
    if (!f.sponsor.trim()) e.sponsor = "Name the sponsor.";
    const end = Date.parse(f.endsAt);
    if (!Number.isFinite(end) || end <= now) e.endsAt = "Choose an end date in the future.";
    else if (maxDays !== null && end > now + maxDays * 86_400_000) e.endsAt = `At most ${maxDays} days from now.`;
  }
  return e;
}

export function openBody(f: OpenForm): FinanceOpenIn {
  const credit = f.kind === "CREDIT_NOTE";
  return {
    kind: f.kind,
    target_tenant_id: f.tenant.trim().toLowerCase(),
    invoice_id: credit ? f.invoice.trim().toLowerCase() : null,
    amount: f.amount.trim(),
    currency: f.currency.trim(),
    category: f.category,
    reason: f.reason.trim(),
    ends_at: credit ? null : new Date(f.endsAt).toISOString(),
    evidence_ref: credit ? f.evidence.trim() || null : null,
    disposition: credit && f.disposition ? (f.disposition as FinanceOpenIn["disposition"]) : null,
    program_ref: credit ? null : f.program.trim(),
    sponsor: credit ? null : f.sponsor.trim(),
  };
}

/** Roles the request still needs, in the order the policy names them. */
export function rolesStillNeeded(r: Pick<FinanceRequest, "required_roles" | "approvals">): string[] {
  const done = new Set(r.approvals.filter((a) => a.decision === "APPROVE").map((a) => a.role));
  return r.required_roles.filter((x) => !done.has(x));
}

export type RequestAction = "sign" | "execute" | "cancel";

/** What the page offers for a request. The server still refuses a self-approval, a repeat signature, a lapsed approval or an executor who
 * signed; this only avoids offering what is plainly closed. */
export function offeredActions(r: FinanceRequest, nowMs: number): RequestAction[] {
  if (r.state === "PENDING_APPROVAL") {
    if (Date.parse(r.expires_at) <= nowMs) return [];
    return rolesStillNeeded(r).length ? ["sign", "cancel"] : ["cancel"];
  }
  if (r.state === "APPROVED" || r.state === "APPLICATION_FAILED") {
    if (r.approval_expires_at && Date.parse(r.approval_expires_at) <= nowMs) return [];
    return ["execute"];
  }
  return [];
}

export function daysLeft(iso: string | null, nowMs: number): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso) - nowMs;
  return Number.isFinite(ms) ? Math.max(0, Math.ceil(ms / 86_400_000)) : null;
}

export const shortHash = (h: string) => `${h.slice(0, 12)}…${h.slice(-6)}`;

/** One key per intention to run, so a double click or a retry is one execution. */
export function executionKey(requestId: string, attempt: string): string {
  return `exec-${requestId}-${attempt}`.slice(0, 120);
}

export const GATE_STATE_LABEL = {
  NOT_ELIGIBLE: { label: "Not eligible", tone: "danger" },
  ELIGIBLE: { label: "Eligible", tone: "ok" },
  AWAITING_APPROVAL: { label: "Awaiting approval", tone: "info" },
  ACTIVE: { label: "Active", tone: "ok" },
} as const;

export const CLASSIFICATION_LABEL = {
  FREE: "Free",
  COMMERCIAL: "Commercial",
  PILOT_NON_BILLABLE: "Pilot, not billable",
  INTERNAL: "Internal",
} as const;
