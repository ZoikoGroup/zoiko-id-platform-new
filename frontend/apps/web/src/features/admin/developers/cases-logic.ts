import type { VerifyApiCase } from "@zoiko/api-client";

/** The plain-language side of disputes, appeals and erasure requests (CR-159). The cell checks everything again. */

export type CaseKind = "DISPUTE" | "APPEAL" | "ERASURE";

export const KINDS: { id: CaseKind; label: string; help: string; target: "session" | "reference" }[] = [
  { id: "DISPUTE", label: "Dispute a result", help: "You disagree with a recorded result. A person at Zoiko iD reviews it. The result itself is never changed: ask for a recheck if you want a new look.", target: "session" },
  { id: "APPEAL", label: "Appeal a result", help: "For a result that was not verified. A person looks at it again.", target: "session" },
  { id: "ERASURE", label: "Erase a customer reference", help: "Your customer asked to be forgotten. We remove their reference from the sessions it was sent with. The results stay, without any personal data.", target: "reference" },
];

export const kindLabel = (k: string): string => KINDS.find((x) => x.id === k)?.label ?? k;

export const REASON_MIN = 10;
export const REASON_MAX = 1000;

export type CaseDraft = { kind: CaseKind; target: string; reason: string };
export const EMPTY_DRAFT: CaseDraft = { kind: "DISPUTE", target: "", reason: "" };

/** Why the case cannot be sent yet, or null. */
export function draftProblem(d: CaseDraft): string | null {
  const kind = KINDS.find((k) => k.id === d.kind)!;
  const target = d.target.trim();
  if (!target) return kind.target === "session" ? "Enter the session ID, which starts with vfs_." : "Enter the customer reference exactly as you sent it.";
  if (kind.target === "session" && !/^vfs_[0-9a-f]{32}$/.test(target)) return "A session ID looks like vfs_ followed by 32 letters and numbers.";
  if (kind.target === "reference" && target.length > 128) return "A customer reference is at most 128 characters.";
  const reason = d.reason.trim().replace(/\s+/g, " ");
  if (reason.length < REASON_MIN) return `Say why in at least ${REASON_MIN} characters, so the reviewer has something to act on.`;
  if (reason.length > REASON_MAX) return `The reason is at most ${REASON_MAX} characters.`;
  return null;
}

export function toRequest(d: CaseDraft): { kind: CaseKind; reason: string; session_id?: string; customer_reference?: string } {
  const kind = KINDS.find((k) => k.id === d.kind)!;
  const reason = d.reason.trim().replace(/\s+/g, " ");
  return kind.target === "session" ? { kind: d.kind, reason, session_id: d.target.trim() } : { kind: d.kind, reason, customer_reference: d.target.trim() };
}

export const CASE_STATE: Record<string, { label: string; tone: "info" | "warn" | "ok" | "danger" | "neutral"; text: string }> = {
  OPEN: { label: "Waiting", tone: "info", text: "A person at Zoiko iD has not picked this up yet." },
  IN_REVIEW: { label: "In review", tone: "warn", text: "A person at Zoiko iD is looking at it." },
  RESOLVED: { label: "Resolved", tone: "ok", text: "It is done. The note below says what was decided." },
  REJECTED: { label: "Not upheld", tone: "danger", text: "It was not upheld. The note below says why." },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral", text: "You withdrew it." },
};

export const canWithdraw = (c: Pick<VerifyApiCase, "status">): boolean => c.status === "OPEN" || c.status === "IN_REVIEW";

/** What the company reads about an erasure once it is done. */
export function outcomeText(c: VerifyApiCase): string | null {
  if (c.kind === "ERASURE" && c.status === "RESOLVED") {
    const n = c.sessions_erased ?? 0;
    return `${n} ${n === 1 ? "session was" : "sessions were"} cleaned of that customer reference. The results were kept.`;
  }
  return null;
}
