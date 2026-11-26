import type { ReviewCase, ReviewCaseState } from "@zoiko/api-client";

/** The plain-language side of case review and four-eyes control (CR-167). The cell checks every rule again. */

export const REVIEW_STATE: Record<ReviewCaseState, { label: string; tone: "info" | "warn" | "ok" | "danger" | "neutral" }> = {
  QUEUED: { label: "Waiting for a reviewer", tone: "info" },
  CLAIMED: { label: "Taken", tone: "warn" },
  INVESTIGATING: { label: "Being looked at", tone: "warn" },
  AWAITING_APPLICANT: { label: "Waiting for the person", tone: "neutral" },
  ESCALATED: { label: "Escalated", tone: "warn" },
  SPECIALIST_REVIEW: { label: "With a specialist", tone: "warn" },
  AWAITING_SECOND_REVIEW: { label: "Waiting for a second reviewer", tone: "info" },
  RETURNED: { label: "Sent back", tone: "neutral" },
  APPROVED_FINAL: { label: "Decided", tone: "ok" },
};

export const OUTCOMES = [
  { id: "verified", label: "Verified" },
  { id: "not_verified", label: "Not verified" },
  { id: "inconclusive", label: "Inconclusive" },
] as const;

export const REASONS: { id: string; label: string }[] = [
  { id: "CHECKS_CONFIRMED", label: "Every required check is confirmed" },
  { id: "CHECK_FAILED", label: "A required check failed" },
  { id: "EVIDENCE_INSUFFICIENT", label: "The evidence is not enough to decide" },
];

/** What the second reviewer is asked to confirm, in words. Unknown points fall back to their name. */
export const CHALLENGE_TEXT: Record<string, string> = {
  evidence_reviewed: "I looked at the evidence myself, not only at the proposal.",
  reason_matches_evidence: "The reason given matches what the evidence shows.",
  no_conflict_of_interest: "I have no personal or business link to this person or to the first reviewer's work.",
};
export const challengeText = (k: string): string => CHALLENGE_TEXT[k] ?? k.replace(/_/g, " ");

/** True once every point of the checklist is ticked. */
export const checklistDone = (challenges: string[], answers: Record<string, boolean>): boolean => challenges.length > 0 && challenges.every((k) => answers[k] === true);

export type Part = "claim" | "work" | "second" | "applicant" | "watch" | "done";

/** What the signed-in person can do with a case right now, from what the cell says. The cell checks again. */
export function myPart(c: Pick<ReviewCase, "status" | "lease">, me: string | null): Part {
  if (c.status === "APPROVED_FINAL") return "done";
  if (c.status === "AWAITING_APPLICANT") return "applicant";
  if (c.status === "AWAITING_SECOND_REVIEW") return "second";
  if (c.status === "QUEUED" || c.status === "RETURNED" || c.status === "ESCALATED") return "claim";
  return c.lease && me && c.lease.reviewer === me ? "work" : "watch";
}

/** Whether the person holds the case's lease. */
export const iHold = (c: Pick<ReviewCase, "lease">, me: string | null): boolean => !!c.lease && !!me && c.lease.reviewer === me;

/** The findings are complete when every required check has an answer. */
export const findingsComplete = (c: Pick<ReviewCase, "required_checks" | "findings">): boolean => c.required_checks.every((k) => c.findings[k] !== undefined);

/** Whether Verified can be offered: every required check confirmed. */
export const canVerify = (c: Pick<ReviewCase, "required_checks" | "findings">): boolean => c.required_checks.every((k) => c.findings[k] === "pass");

export const minutesLeft = (iso: string, now: number): number => Math.max(0, Math.ceil((new Date(iso).getTime() - now) / 60000));

/** The lease lasts 15 minutes; ask for more while someone is looking, well before it ends. */
export const HEARTBEAT_MS = 4 * 60 * 1000;

/** A revealed item hides itself when its time is up. Seconds left, never below zero. */
export const secondsLeft = (iso: string, now: number): number => Math.max(0, Math.ceil((new Date(iso).getTime() - now) / 1000));
export const clock = (s: number): string => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export const REVEAL_NOTE_MIN = 5;
export const revealReady = (reason: string): boolean => reason.trim().length >= REVEAL_NOTE_MIN;

export type Filters = { status: string; queue: string; mine: boolean };
export const NO_FILTERS: Filters = { status: "", queue: "", mine: false };

export const STATUS_OPTIONS = [{ id: "", label: "Any state" }, ...(Object.keys(REVIEW_STATE) as ReviewCaseState[]).map((id) => ({ id, label: REVIEW_STATE[id].label }))];
export const QUEUE_OPTIONS = [
  { id: "", label: "Both queues" },
  { id: "STANDARD", label: "General" },
  { id: "SPECIALIST", label: "Specialist" },
];

/** The list the person sees: "mine" means the cases whose lease they hold. The cell does the other filtering. */
export const visibleCases = (rows: ReviewCase[], f: Filters, me: string | null): ReviewCase[] => (f.mine ? rows.filter((c) => iHold(c, me)) : rows);

/** The query the list asks the cell, leaving out empty filters. */
export const listQuery = (f: Filters): { status?: string; queue?: string } => ({ ...(f.status ? { status: f.status } : {}), ...(f.queue ? { queue: f.queue } : {}) });
