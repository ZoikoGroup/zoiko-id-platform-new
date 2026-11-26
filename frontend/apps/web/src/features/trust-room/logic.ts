/** Pure rules for the restricted evidence room screen (ZID-CP-WF12 WF-12.03). The cell decides every request and every download; this only words the state. */
import type { TrustRoomMine } from "@zoiko/api-client";

export const REQUEST_STATE: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  REQUESTED: { label: "Waiting for our security team", tone: "warn" },
  APPROVED: { label: "Approved", tone: "ok" },
  DENIED: { label: "Declined", tone: "danger" },
  REVOKED: { label: "Access withdrawn", tone: "danger" },
};

type Access = Pick<TrustRoomMine, "status" | "revoked" | "grant_expires_at" | "downloads_left">;

/** Can the person fetch the document with the code they were given? Only while approved, unexpired, unrevoked and with downloads left. */
export function canDownload(r: Access, now: Date): boolean {
  if (r.status !== "APPROVED" || r.revoked) return false;
  if (r.grant_expires_at !== null && new Date(r.grant_expires_at) <= now) return false;
  return (r.downloads_left ?? 0) > 0;
}

export function accessText(r: Access, now: Date): string {
  if (r.status === "DENIED") return "Your request was declined.";
  if (r.revoked || r.status === "REVOKED") return "Access was withdrawn.";
  if (r.status === "REQUESTED") return "We will reply to your company contact once it is reviewed.";
  if (r.grant_expires_at !== null && new Date(r.grant_expires_at) <= now) return "The access has expired. Ask again if you still need the document.";
  if ((r.downloads_left ?? 0) <= 0) return "All downloads were used. Ask again if you need another copy.";
  return `Ready. ${r.downloads_left} download(s) left, until ${new Date(r.grant_expires_at ?? "").toLocaleString("en-GB")}.`;
}

export function requestReady(f: { evidence_id: string; organization: string; purpose: string; accept_terms: boolean }): boolean {
  return f.evidence_id !== "" && f.organization.trim().length >= 2 && f.purpose.trim().length >= 10 && f.accept_terms;
}
