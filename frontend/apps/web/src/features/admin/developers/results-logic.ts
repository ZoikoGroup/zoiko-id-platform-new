import type { VerifyApiDecision } from "@zoiko/api-client";

/** The plain-language side of the Results card (CR-155). The cell checks everything again. */

export const OUTCOME_LABEL: Record<string, { label: string; tone: "ok" | "danger" | "warn" | "neutral" }> = {
  verified: { label: "Verified", tone: "ok" },
  not_verified: { label: "Not verified", tone: "danger" },
  inconclusive: { label: "No answer", tone: "warn" },
  review_required: { label: "Needs a reviewer", tone: "warn" },
};

export const CHECK_LABEL: Record<string, string> = {
  document_authenticity: "ID document",
  face_match: "Face match",
};

export const STATUS_FILTERS: { id: string; label: string }[] = [
  { id: "", label: "Any state" },
  { id: "ready", label: "Waiting for the customer" },
  { id: "opened", label: "Customer opened the link" },
  { id: "manual_review", label: "With a reviewer" },
  { id: "completed", label: "Finished" },
  { id: "inconclusive", label: "No answer" },
  { id: "expired", label: "Ran out of time" },
  { id: "canceled", label: "Canceled" },
];

export const ENVIRONMENT_FILTERS: { id: string; label: string }[] = [
  { id: "", label: "Sandbox and live" },
  { id: "sandbox", label: "Sandbox tests" },
  { id: "production", label: "Live" },
];

export const checkLabel = (m: string): string => CHECK_LABEL[m] ?? m.replace(/_/g, " ");
export const outcomeLabel = (o: string): { label: string; tone: "ok" | "danger" | "warn" | "neutral" } => OUTCOME_LABEL[o] ?? { label: o, tone: "neutral" };

/** The reference exactly as typed, trimmed. It is matched exactly, never searched, so a partial reference finds nothing. */
export const cleanReference = (v: string): string | undefined => {
  const t = v.trim();
  return t === "" ? undefined : t;
};

/** What each decision in a history says, newest first, so the current answer is on top and the earlier ones follow. */
export function historyLines(decisions: VerifyApiDecision[]): { id: string; text: string; current: boolean }[] {
  const ordered = [...decisions].sort((a, b) => b.decision_version - a.decision_version);
  return ordered.map((d, i) => ({
    id: d.decision_id,
    current: i === 0,
    text: `Version ${d.decision_version}: ${outcomeLabel(d.outcome).label}${d.supersedes_decision_id ? ` (replaced version ${d.decision_version - 1})` : ""}`,
  }));
}
