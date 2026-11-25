/** Pure rules for the fraud graph console (CR-168; ZID-CP-WF05). The server decides every change; this keeps the screen honest about what a number or a link means. */
import type { FraudAction, FraudLead } from "@zoiko/api-client";

export const LEAD_STATE: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "info" | "neutral" }> = {
  OPEN: { label: "Open", tone: "warn" },
  IN_ANALYSIS: { label: "Being looked at", tone: "info" },
  ESCALATED: { label: "Sent for review", tone: "danger" },
  CLEARED: { label: "Cleared", tone: "ok" },
  EXPIRED: { label: "Expired", tone: "neutral" },
};

export const FINDING: Record<string, string> = {
  UNREVIEWED: "No finding yet",
  CONFIRMED: "Confirmed after review",
  NO_FINDING: "Reviewed: nothing found",
  INCONCLUSIVE: "Reviewed: inconclusive",
};

export const ACTION_STATE: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  PROPOSED: { label: "Waiting for a second person", tone: "warn" },
  APPROVED: { label: "Approved", tone: "ok" },
  REJECTED: { label: "Rejected", tone: "neutral" },
  REVOKED: { label: "Revoked", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "neutral" },
};

export type LeadActions = { analyze: boolean; escalate: boolean; clear: boolean; finding: boolean };

/** What a lead can do next. A lead is a place to look, not a finding: only an escalated lead can take a finding, and only after its review case. */
export function leadActions(l: Pick<FraudLead, "status" | "finding">): LeadActions {
  const open = l.status === "OPEN" || l.status === "IN_ANALYSIS";
  return { analyze: l.status === "OPEN", escalate: open, clear: open, finding: l.status === "ESCALATED" && l.finding === "UNREVIEWED" };
}

export type ActionButtons = { approve: boolean; reject: boolean; revoke: boolean };

/** The maker never approves their own proposal; an approved one can only be revoked. The cell enforces this too. */
export function actionButtons(a: Pick<FraudAction, "status" | "maker">, me: string | null): ActionButtons {
  const mine = me !== null && a.maker === me;
  return { approve: a.status === "PROPOSED" && !mine, reject: a.status === "PROPOSED", revoke: a.status === "APPROVED" };
}

/** Coverage in words. Sessions with no observation are unread or erased, never "safe". */
export function coverageText(c: { sessions: number; sessions_with_observation: number; sessions_without: number }): string {
  if (c.sessions === 0) return "No sessions yet.";
  if (c.sessions_with_observation === 0) return `None of ${c.sessions} sessions has been read in yet. Run a refresh: no result here means no data, not no risk.`;
  return `${c.sessions_with_observation} of ${c.sessions} sessions read in. ${c.sessions_without} have no signal and are not shown as safe.`;
}

export function riskTone(risk: string): "danger" | "warn" | "neutral" {
  return risk === "HIGH" ? "danger" : risk === "MEDIUM" ? "warn" : "neutral";
}
