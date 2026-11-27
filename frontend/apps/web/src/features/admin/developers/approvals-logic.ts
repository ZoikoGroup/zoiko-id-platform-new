import type { VerifyApiApproval } from "@zoiko/api-client";

import { parsePublicKey } from "./credential-options-logic";

/** The plain-language side of production-credential approvals (CR-162). The cell checks everything again, and it alone decides who may approve: the person who asked can never do it. */

export const APPROVAL_STATE: Record<string, { label: string; tone: "info" | "ok" | "neutral" | "danger"; text: string }> = {
  PENDING: { label: "Waiting", tone: "info", text: "Waiting for approval. Press Approve below. If your deployment needs a different administrator, the server will say so." },
  APPROVED: { label: "Approved", tone: "ok", text: "Approved. The person who asked can use it once, within 24 hours." },
  CONSUMED: { label: "Used", tone: "neutral", text: "It was used to make the credential." },
  CANCELED: { label: "Taken back", tone: "neutral", text: "The person who asked took it back." },
  REJECTED: { label: "Rejected", tone: "danger", text: "It was rejected. The note says why." },
  EXPIRED: { label: "Expired", tone: "neutral", text: "It was not used in time. Ask again." },
};

export const canDecide = (a: Pick<VerifyApiApproval, "status">): boolean => a.status === "PENDING";
export const canUse = (a: Pick<VerifyApiApproval, "status">): boolean => a.status === "APPROVED";
export const canCancel = (a: Pick<VerifyApiApproval, "status">): boolean => a.status === "PENDING";

/** What an approval is for, in words. */
export function describe(a: Pick<VerifyApiApproval, "action" | "with_key">): string {
  if (a.action === "ROTATE") return a.with_key ? "Rotate a credential to a new key" : "Rotate a credential to a new secret";
  return a.with_key ? "Create a key credential" : "Create a credential with a secret";
}

/** Whole hours left to use an approved approval, never negative. */
export function hoursLeft(a: Pick<VerifyApiApproval, "expires_at">, now: Date): number {
  return Math.max(0, Math.floor((new Date(a.expires_at).getTime() - now.getTime()) / 3_600_000));
}

export type AskDraft = { action: "ISSUE" | "ROTATE"; credentialId: string; kind: "SECRET" | "KEY"; keyText: string };
export const EMPTY_ASK: AskDraft = { action: "ISSUE", credentialId: "", kind: "SECRET", keyText: "" };

/** Why the request cannot be sent yet, or null. */
export function askProblem(d: AskDraft): string | null {
  if (d.action === "ROTATE" && !d.credentialId) return "Choose the credential to rotate.";
  if (d.kind === "KEY") {
    if (!d.keyText.trim()) return "Paste the public key this approval is for. It is approved for that key only.";
    return parsePublicKey(d.keyText).problem;
  }
  return null;
}

export function askBody(d: AskDraft): { action: "ISSUE" | "ROTATE"; credential_id?: string; public_jwk?: Record<string, string> } {
  const key = d.kind === "KEY" ? parsePublicKey(d.keyText).jwk : null;
  return { action: d.action, ...(d.action === "ROTATE" ? { credential_id: d.credentialId } : {}), ...(key ? { public_jwk: key } : {}) };
}
