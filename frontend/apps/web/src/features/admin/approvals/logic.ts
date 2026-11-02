import type { ApprovalCase, ApprovalCaseState, GovernanceReviewer, ReviewerAppointIn } from "@zoiko/api-client";

/** The step-up action the server classifies every approval vote under (S1; S2 for a critical A2 case). */
export const VOTE_STEP_UP_ACTION = "execute-sensitive-approval-rejection";

/** What the passkey is asked to approve: this case, this exact request hash, this vote. The server re-derives it, so a proof for
 * another case, person or vote is refused there; this only builds the draft. */
export function voteDraft(c: Pick<ApprovalCase, "case_id" | "request_hash" | "tier">, vote: "APPROVE" | "REJECT") {
  return {
    action: VOTE_STEP_UP_ACTION,
    resource: `approval-case:${c.case_id}`,
    change: { case_id: c.case_id, request_hash: c.request_hash, vote },
    critical: c.tier === "A2",
  };
}

/** A fresh key for each intention to run a case, so a double click is one attempt and a retry after a failure is a new one. */
export function executionKey(caseId: string, attempt: string): string {
  return `exec-${caseId}-${attempt}`.slice(0, 120);
}

export function attempt(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36);
}

export const OPEN_STATES: ApprovalCaseState[] = ["PENDING_APPROVAL", "PARTIALLY_APPROVED", "APPROVED_READY", "FAILED"];

export const STATE_WORDS: Record<ApprovalCaseState, string> = {
  PENDING_APPROVAL: "Waiting for approval",
  PARTIALLY_APPROVED: "Partly approved",
  APPROVED_READY: "Approved, not yet applied",
  EXECUTED: "Applied",
  FAILED: "Failed, can be run again",
  REJECTED: "Rejected",
  EXPIRED: "Expired",
  INVALIDATED: "No longer valid",
  CANCELLED: "Withdrawn",
  NEEDS_CHANGES: "Needs changes",
};

export function stateTone(s: ApprovalCaseState): "ok" | "warn" | "danger" | "info" | "neutral" {
  if (s === "EXECUTED") return "ok";
  if (s === "FAILED" || s === "REJECTED") return "danger";
  if (s === "EXPIRED" || s === "INVALIDATED" || s === "CANCELLED") return "neutral";
  if (s === "NEEDS_CHANGES") return "warn";
  if (s === "APPROVED_READY") return "info";
  return "warn";
}

export const isOpen = (c: Pick<ApprovalCase, "state">) => OPEN_STATES.includes(c.state);
export const canVote = (c: ApprovalCase) => c.you?.may_vote === true && (c.state === "PENDING_APPROVAL" || c.state === "PARTIALLY_APPROVED");
export const canExecute = (c: Pick<ApprovalCase, "state">) => c.state === "APPROVED_READY" || c.state === "FAILED";
export const canCancel = (c: ApprovalCase) => c.you?.is_requester === true && isOpen(c);
export const canRevise = (c: ApprovalCase) => c.you?.is_requester === true && isOpen(c);

/** The request's own fields as label and value rows for the review dialog (ids and codes only; the server sends no secrets). */
export function payloadRows(payload: Record<string, unknown>): { label: string; value: string }[] {
  return Object.entries(payload)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => ({ label: k.replace(/_/g, " "), value: typeof v === "string" ? v : JSON.stringify(v) }));
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** One account id per line or comma: the people the reviewer declares a relationship with. */
export function parseConflicts(text: string): { ids: string[]; invalid: string[] } {
  const parts = text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return { ids: [...new Set(parts.filter((p) => UUID.test(p)))], invalid: parts.filter((p) => !UUID.test(p)) };
}

export type ReviewerForm = {
  principal: string;
  relationship: ReviewerAppointIn["relationship"] | "";
  basis: string;
  declaration: string;
  conflicts: string;
};

export const emptyReviewerForm: ReviewerForm = { principal: "", relationship: "", basis: "", declaration: "", conflicts: "" };

export function reviewerFormErrors(f: ReviewerForm): Partial<Record<keyof ReviewerForm, string>> {
  const e: Partial<Record<keyof ReviewerForm, string>> = {};
  if (!UUID.test(f.principal.trim())) e.principal = "Enter the reviewer's account id.";
  if (!f.relationship) e.relationship = "Say how this person relates to the organization.";
  if (!f.basis.trim()) e.basis = "Describe the documented authority that makes them able to review.";
  if (!f.declaration.trim()) e.declaration = "Declare their independence from the people who will make requests.";
  if (parseConflicts(f.conflicts).invalid.length) e.conflicts = "Use account ids, one per line.";
  return e;
}

export function toAppointment(f: ReviewerForm): ReviewerAppointIn {
  return {
    principal_id: f.principal.trim(),
    relationship: f.relationship as ReviewerAppointIn["relationship"],
    authority_basis: f.basis.trim(),
    independence_declaration: f.declaration.trim(),
    declared_conflicts: parseConflicts(f.conflicts).ids,
  };
}

export const RELATIONSHIPS: { id: ReviewerAppointIn["relationship"]; label: string }[] = [
  { id: "EXTERNAL_PROFESSIONAL", label: "External professional (for example an accountant or lawyer)" },
  { id: "CO_DIRECTOR", label: "Co-director" },
  { id: "TRUSTEE", label: "Trustee" },
  { id: "AUTHORIZED_DELEGATE", label: "Authorized delegate" },
];

/** A reviewer is none of these, and the screen says so wherever a reviewer appears. */
export const REVIEWER_IS_NOT =
  "A reviewer is not an employee, a legal representative, a recovery contact or a paid seat. They only vote on requests they are independent of.";

export const reviewerStatusTone = (r: Pick<GovernanceReviewer, "status">): "ok" | "warn" | "neutral" =>
  r.status === "ACTIVE" ? "ok" : r.status === "PROPOSED" ? "warn" : "neutral";

export const ACTION_WORDS: Record<string, string> = {
  "ownership-transfer-or-replacement": "Add an Owner",
  "removal-of-last-owner-or-control-chain": "Remove an Owner",
  "privileged-owner-or-admin-recovery": "Recover a privileged account",
  "privileged-role-grant-or-elevation": "Grant a privileged role",
  "reinstate-suspended-tenant-access": "Reinstate suspended access",
  "appoint-independent-reviewer": "Appoint an independent reviewer",
};
export const actionWords = (id: string) => ACTION_WORDS[id] ?? id.replace(/-/g, " ");
