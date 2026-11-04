import type { DisputeCase, ReconBoard, ReconRow, RefundCase, RefundState } from "@zoiko/api-client";

/** Refunds, disputes and reconciliation (ZID-UX-COM-009 F03, F04; CR-058). The server owns every rule. These helpers word the facts the way the wireframe
 * demands (provider confirmed is not bank settled, a recorded hand-off is not an outcome) and decide which buttons are worth showing. */

type Tone = "info" | "ok" | "warn" | "danger" | "neutral";

export const REFUND_STATE: Record<RefundState, { label: string; tone: Tone }> = {
  REQUESTED: { label: "Waiting for approval", tone: "info" },
  APPROVED: { label: "Approved, not yet queued", tone: "ok" },
  EXECUTION_PENDING: { label: "Queued to send", tone: "info" },
  PROCESSOR_PENDING: { label: "With the processor", tone: "info" },
  PARTIAL: { label: "Partly refunded", tone: "warn" },
  SUCCEEDED: { label: "Provider confirmed", tone: "ok" },
  FAILED: { label: "Processor failed it", tone: "danger" },
  UNKNOWN: { label: "Outcome unknown", tone: "danger" },
  REJECTED: { label: "Rejected", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "warn" },
  CANCELED: { label: "Withdrawn", tone: "neutral" },
};

export const REFUND_CATEGORIES = [
  { id: "GOODWILL", label: "Goodwill" },
  { id: "DUPLICATE_PAYMENT", label: "Duplicate payment (evidence needed)" },
  { id: "CORRECTION", label: "Correction of an error (evidence needed)" },
  { id: "CANCELLATION", label: "Cancellation (evidence needed)" },
  { id: "CONTRACT_RIGHT", label: "Contract right (evidence needed)" },
] as const;

const NEEDS_EVIDENCE = new Set(["DUPLICATE_PAYMENT", "CORRECTION", "CANCELLATION", "CONTRACT_RIGHT"]);

export function money(minor: number, currency: string): string {
  const exp = ["JPY", "KRW", "VND"].includes(currency) ? 0 : currency === "KWD" || currency === "BHD" ? 3 : 2;
  return `${(minor / 10 ** exp).toLocaleString("en-US", { minimumFractionDigits: exp, maximumFractionDigits: exp })} ${currency}`;
}

export type RefundForm = { tenant: string; payment: string; amount: string; category: string; reason: string; evidence: string };
export const emptyRefundForm = (): RefundForm => ({ tenant: "", payment: "", amount: "", category: "GOODWILL", reason: "", evidence: "" });

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The same checks the server makes, so a mistake is said before the request goes. The server still decides. */
export function validateRefund(f: RefundForm): Partial<Record<keyof RefundForm, string>> {
  const e: Partial<Record<keyof RefundForm, string>> = {};
  if (!UUID.test(f.tenant.trim())) e.tenant = "Enter the organization's tenant ID.";
  if (!UUID.test(f.payment.trim())) e.payment = "Enter the payment's ID.";
  if (!/^\d{1,12}(\.\d{1,2})?$/.test(f.amount.trim()) || Number(f.amount) <= 0) e.amount = "An amount above zero, with at most two decimals.";
  if (!f.reason.trim()) e.reason = "Say why.";
  if (NEEDS_EVIDENCE.has(f.category) && !f.evidence.trim()) e.evidence = "This category names its evidence.";
  return e;
}

export function refundBody(f: RefundForm) {
  return {
    target_tenant_id: f.tenant.trim(),
    payment_attempt_id: f.payment.trim(),
    amount: f.amount.trim(),
    category: f.category as RefundCase["category"],
    reason: f.reason.trim(),
    evidence_ref: f.evidence.trim() || null,
  };
}

export const rolesStillNeeded = (r: RefundCase) => r.required_roles.filter((x) => !r.approvals.some((a) => a.role === x && a.decision === "APPROVE"));

/** Which buttons a case offers. Nothing here sends money: "queue" hands an approved refund to the worker. */
export function refundActions(r: RefundCase): ("sign" | "queue" | "cancel")[] {
  if (r.state === "REQUESTED") return ["sign", "cancel"];
  if (r.state === "APPROVED") return ["queue", "cancel"];
  return [];
}

export const DISPUTE_FINAL = new Set(["CLOSED_WON", "CLOSED_LOST", "CLOSED_ACCEPTED", "CLOSED_OTHER"]);

export const DISPUTE_STATE: Record<string, { label: string; tone: Tone }> = {
  OPEN: { label: "New", tone: "danger" },
  INVESTIGATING: { label: "Investigating", tone: "info" },
  DECISION_PENDING: { label: "Waiting for a decision", tone: "warn" },
  ACCEPTED: { label: "Accepted", tone: "neutral" },
  CONTESTING: { label: "Contesting: evidence to hand over", tone: "info" },
  SUBMISSION_RECORDED: { label: "Hand-off recorded, not verified", tone: "warn" },
  SUBMISSION_VERIFIED: { label: "Processor holds the evidence", tone: "info" },
  CLOSED_WON: { label: "Won (processor)", tone: "ok" },
  CLOSED_LOST: { label: "Lost (processor)", tone: "danger" },
  CLOSED_ACCEPTED: { label: "Accepted, closed (processor)", tone: "neutral" },
  CLOSED_OTHER: { label: "Closed (processor)", tone: "neutral" },
};

export const EVIDENCE_LABEL: Record<string, string> = {
  terms_or_consent: "Terms or consent",
  transaction_proof: "Transaction proof",
  usage_proof: "Usage proof",
  communications: "Communications",
  cancellation_refund_chronology: "Cancellation and refund chronology",
};

/** The processor's deadline as it gave it, in its own zone; never a window we made up. */
export function deadlineText(d: DisputeCase["deadline"]): string {
  if (d.status === "NO_DEADLINE_FROM_PROCESSOR" || !d.due_by) return "The processor gave no deadline";
  const when = new Date(d.due_by).toLocaleString("en-GB", { timeZone: d.zone, dateStyle: "medium", timeStyle: "short" });
  if (d.status === "PASSED") return `${when} ${d.zone}: passed`;
  const days = Math.floor((d.seconds_left ?? 0) / 86_400);
  const hours = Math.floor(((d.seconds_left ?? 0) % 86_400) / 3_600);
  return `${when} ${d.zone}: ${days} d ${hours} h left`;
}

export function disputeActions(d: DisputeCase): ("assign" | "evidence" | "decide" | "record" | "verify")[] {
  if (DISPUTE_FINAL.has(d.state)) return [];
  const out: ("assign" | "evidence" | "decide" | "record" | "verify")[] = [];
  if (!d.owner) out.push("assign");
  if (d.state !== "ACCEPTED" && d.state !== "SUBMISSION_VERIFIED") out.push("evidence");
  if (d.state === "OPEN" || d.state === "INVESTIGATING" || d.state === "DECISION_PENDING") out.push("decide");
  if (d.state === "CONTESTING") out.push("record");
  if (d.state === "SUBMISSION_RECORDED") out.push("verify");
  return out;
}

export const MATCH_LABEL: Record<ReconRow["state"], { label: string; tone: Tone }> = {
  MATCHED: { label: "Matched", tone: "ok" },
  PROVISIONALLY_MATCHED: { label: "Provisionally matched", tone: "warn" },
  UNMATCHED: { label: "Unmatched", tone: "danger" },
};

export const LEG_LABEL: Record<string, string> = { PAYOUT_LINE: "Processor payout", BANK_LINE: "Bank", GL_JOURNAL: "Ledger journal" };

export const PERIOD_STATE: Record<ReconBoard["state"], { label: string; tone: Tone }> = {
  OPEN: { label: "Open", tone: "info" },
  READY_FOR_CLOSE: { label: "Ready for close: waiting for signatures", tone: "warn" },
  CLOSED: { label: "Closed and locked", tone: "ok" },
  REOPEN_REQUESTED: { label: "Reopen requested", tone: "warn" },
};

export const validPeriod = (p: string) => /^\d{4}-(0[1-9]|1[0-2])$/.test(p);

/** The last calendar month, YYYY-MM, which is the period normally being closed. */
export function lastPeriod(now: Date): string {
  const y = now.getUTCMonth() === 0 ? now.getUTCFullYear() - 1 : now.getUTCFullYear();
  const m = now.getUTCMonth() === 0 ? 12 : now.getUTCMonth();
  return `${y}-${String(m).padStart(2, "0")}`;
}

/** Parse a pasted CSV of legs: leg_type,ref,link_ref,amount_minor,currency,occurred_on (no header needed). Whole minor units only. */
export function parseLegs(text: string): { legs: Record<string, unknown>[]; errors: string[] } {
  const legs: Record<string, unknown>[] = [];
  const errors: string[] = [];
  text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.toLowerCase().startsWith("leg_type"))
    .forEach((line, i) => {
      const [type, ref, link, amount, currency, day] = line.split(",").map((c) => c.trim());
      if (!["PAYOUT_LINE", "BANK_LINE", "GL_JOURNAL"].includes(type ?? "")) errors.push(`Line ${i + 1}: unknown leg type`);
      else if (!ref) errors.push(`Line ${i + 1}: a reference is needed`);
      else if (!/^-?\d+$/.test(amount ?? "")) errors.push(`Line ${i + 1}: the amount is whole minor units`);
      else if (!/^[A-Za-z]{3}$/.test(currency ?? "")) errors.push(`Line ${i + 1}: a three-letter currency`);
      else if (!/^\d{4}-\d{2}-\d{2}$/.test(day ?? "")) errors.push(`Line ${i + 1}: the date is YYYY-MM-DD`);
      else legs.push({ leg_type: type, ref, link_ref: link ?? "", amount_minor: Number(amount), currency: currency!.toUpperCase(), occurred_on: day });
    });
  return { legs, errors };
}
