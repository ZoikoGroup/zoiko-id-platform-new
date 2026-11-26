/** Pure rules for the Trust Center screens (ZID-CP-WF12; CR-174): what each status means, which button is offered, and how a figure is worded. The server decides; this only keeps the screen honest. */
import type { TrustClaim } from "@zoiko/api-client";

export const STATUS: Record<string, { tone: "ok" | "warn" | "danger" | "info" | "neutral"; text: string }> = {
  DRAFT: { tone: "neutral", text: "Draft" },
  APPROVAL_PENDING: { tone: "warn", text: "Waiting for approvals" },
  PUBLISHED: { tone: "ok", text: "Live" },
  EXPIRED: { tone: "danger", text: "Expired" },
  SUPERSEDED: { tone: "neutral", text: "Replaced" },
  EMERGENCY_WITHDRAWN: { tone: "danger", text: "Withdrawn" },
};

export const ROLES = ["product", "legal", "security", "privacy", "sre", "finance", "marketing"] as const;

const BLOCKER_TEXT: Record<string, string> = {
  BUNDLE_TAMPERED: "The content differs from what was approved.",
  EVIDENCE_MISSING: "No evidence is linked.",
  EVIDENCE_UNVERIFIED: "The issuer has not confirmed the evidence.",
  EVIDENCE_EXPIRED: "The evidence is no longer current.",
  SCOPE_MISMATCH: "The evidence covers a different entity, service, region or period.",
  SUPPLIER_EVIDENCE_AS_OWN: "A supplier's certification cannot be shown as Zoiko iD's own.",
  APPROVAL_INCOMPLETE: "The required approvals are not all in.",
  CLAIM_EXPIRED: "The claim's own date has passed.",
  DISCLOSURE_UNSAFE: "The text carries something a public page must not.",
  VAGUE_SECURITY_LANGUAGE: "The wording cannot be tested by a buyer.",
  EVIDENCE_TYPE_TOO_WEAK: "That kind of evidence cannot support a certification statement.",
  SCHEME_TYPE_MISMATCH: "The evidence type does not match the scheme.",
};

export function blockerText(code: string, detail: string): string {
  return `${BLOCKER_TEXT[code] ?? code.replaceAll("_", " ").toLowerCase()}${detail && !BLOCKER_TEXT[code] ? ` ${detail}` : ""}`;
}

export type Offer = { approve: boolean; publish: boolean; rollback: boolean; withdraw: boolean; revise: boolean };

/** Which actions to offer for one revision. Buttons are conveniences: the capability and the database decide. */
export function offers(c: Pick<TrustClaim, "status" | "ready_to_publish" | "history">): Offer {
  const live = c.status === "PUBLISHED";
  const closed = c.status === "EMERGENCY_WITHDRAWN" || c.status === "EXPIRED" || c.status === "SUPERSEDED";
  return {
    approve: !closed && !live,
    publish: !closed && !live && c.ready_to_publish,
    rollback: live && c.history.some((h) => h.was_published && h.status !== "EMERGENCY_WITHDRAWN" && h.status !== "PUBLISHED"),
    withdraw: live,
    revise: true,
  };
}

export function figure(percent: number | null, state: string): string {
  return state === "MEASURED" && percent !== null ? `${percent}%` : "Not yet reported";
}

export function hoursText(h: number | null): string {
  if (h === null) return "No withdrawals yet";
  return h < 1 ? "under an hour" : `${Math.round(h * 10) / 10} hours`;
}

export function shortHash(h: string): string {
  return `${h.slice(0, 10)}…`;
}
