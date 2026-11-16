/** Pure helpers for the agent governance screens (ZID-AG-WF-002). No React, no network: easy to test, and the only place wording lives. */
import type { GovBudgetRow, GovRequest, GovRequestState } from "@zoiko/api-client";

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral" | "brand";

export const GOV_KEY = ["admin", "agents", "governance"];
export const govKey = (...rest: (string | number | null | undefined)[]) => [...GOV_KEY, ...rest.map((r) => r ?? "")];

/** The decision codes of section 13 in words a company admin can act on. Never says more than the platform knew. */
export const CODE_TEXT: Record<string, string> = {
  AGENT_INACTIVE: "The agent is paused, expired or revoked.",
  TENANT_SCOPE_DENIED: "This request is outside the company it was made for.",
  SCOPE_PROHIBITED: "The platform never allows this action to an agent. No approval can change that.",
  SCOPE_NOT_APPROVED: "Nothing approved this: no current permission covers this tool, resource, purpose or time.",
  REGION_DENIED: "This region is not allowed for the agent or the company.",
  APPROVAL_REQUIRED: "No approval is bound to the policy that is live now.",
  SPONSOR_INVALID: "The agent has no active accountable sponsor.",
  BUDGET_EXCEEDED: "A spending cap above this call has no room left.",
  USAGE_LIMIT: "A usage limit was reached.",
  POLICY_UNAVAILABLE: "A required policy source could not be read, so the platform refused (fail closed).",
  EMERGENCY_FROZEN: "Execution is stopped by an emergency control.",
  ENTITLEMENT_DENIED: "The plan does not include this.",
  SEPARATION_OF_DUTIES: "You asked for or wrote this change. Another qualified reviewer must decide.",
  SIGNER_NOT_QUALIFIED: "You do not hold a signer slot that is still open on this request.",
  SIGNER_NOT_ACTIVE: "Your membership is not active.",
  ALREADY_SIGNED: "You already signed this request.",
  SCOPE_HASH_MISMATCH: "The request changed after this page was loaded. Reload and review the exact version.",
  REQUEST_EXPIRED: "This request expired before it was fully signed.",
  REQUEST_NOT_OPEN: "This request is no longer open.",
  EVIDENCE_UNAVAILABLE: "The evidence for this change is missing, so it cannot be signed.",
  STEP_UP_REQUIRED: "A fresh passkey check is required before signing.",
  REQUEST_FORBIDDEN: "This kind of change can never be approved.",
  RESTORE_NOT_INDEPENDENT: "You froze this, so you cannot approve its restore.",
  RESTORE_HEALTH_CHECK_FAILED: "The fresh health checks did not pass, so a restore cannot be requested.",
  DEPLOYMENT_NOT_ATTESTED: "The agent must be active (its deployment attested) before an approved version can go live.",
  APPROVAL_NOT_EFFECTIVE: "The approval is no longer valid for this version.",
  ALREADY_APPLIED: "This version is already live.",
  NO_PARENT_CEILING: "Set the company ceiling first: a smaller cap must sit under one.",
  BUDGET_ABOVE_PARENT: "A cap cannot be higher than the ceiling above it.",
  CURRENCY_NOT_APPROVED: "That currency is not approved.",
  MONEY_TOO_PRECISE: "That amount has more decimal places than the currency allows.",
  MONEY_NEGATIVE_OR_INVALID: "Enter an amount of zero or more.",
  SIMULATION_REAL_DATA_REFUSED: "A simulation takes a synthetic resource id such as mandate:synthetic-1, never a real one.",
};

export function codeText(code: string | null | undefined): string {
  if (!code) return "";
  return CODE_TEXT[code] ?? "The platform refused this request.";
}

export type Level = GovBudgetRow["level"];
/** A text level with its own icon-free label: colour is never the only signal (GOV03-04). */
export const LEVEL: Record<Level, { label: string; tone: Tone; hint: string }> = {
  OFF: { label: "Off", tone: "neutral", hint: "Nothing may be spent here." },
  OK: { label: "Within limit", tone: "ok", hint: "Under 70% used." },
  WARN_70: { label: "Warning: 70% used", tone: "warn", hint: "A warning only. The hard stop is at 100%." },
  WARN_90: { label: "Warning: 90% used", tone: "warn", hint: "Close to the hard stop at 100%." },
  EXHAUSTED: { label: "Hard stop reached", tone: "danger", hint: "New spending is refused until the window resets." },
};

/** Show a decimal string as it is, with its currency, trimmed to the currency's own number of decimals and never through a float. */
export function money(amount: string, currency: string): string {
  const [whole = "0", frac = ""] = amount.split(".");
  const places = currency === "JPY" ? 0 : 2;
  const trimmed = frac.replace(/0+$/, "").padEnd(places, "0");
  return `${currency} ${whole}${trimmed ? `.${trimmed}` : ""}`;
}

/** 0..100 for a bar's width only. A display aid; the server's level is what says "warning" or "stop". */
export function usagePercent(used: string, cap: string): number {
  const c = Number(cap);
  if (!(c > 0)) return used === "0" || Number(used) === 0 ? 0 : 100;
  return Math.max(0, Math.min(100, Math.round((Number(used) / c) * 100)));
}

export function usedAmount(row: Pick<GovBudgetRow, "committed" | "reserved">): string {
  const [a = "0", af = ""] = row.committed.split(".");
  const [b = "0", bf = ""] = row.reserved.split(".");
  const width = Math.max(af.length, bf.length, 2);
  const x = BigInt(a + af.padEnd(width, "0")) + BigInt(b + bf.padEnd(width, "0"));
  const s = x.toString().padStart(width + 1, "0");
  return `${s.slice(0, s.length - width)}.${s.slice(s.length - width)}`;
}

export const KIND_LABEL: Record<GovRequest["kind"], string> = {
  PUBLISH: "Publish policy",
  SCOPE_EXTENSION: "Scope increase",
  AI_ENABLE: "Turn on AI explanations",
  BUDGET_INCREASE: "Budget increase",
  QUOTA_INCREASE: "Usage limit increase",
  RESTORE: "Restore after emergency stop",
  PRIVILEGE_MUTATION: "Privilege change (never approvable)",
};

export const STATE_LABEL: Record<GovRequestState, { label: string; tone: Tone }> = {
  PENDING: { label: "Waiting for signatures", tone: "warn" },
  CLARIFICATION: { label: "Clarification asked", tone: "info" },
  APPROVED: { label: "Approved, not yet live", tone: "info" },
  REJECTED: { label: "Rejected", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "neutral" },
  INVALIDATED: { label: "Voided by a change", tone: "neutral" },
  APPLIED: { label: "Live", tone: "ok" },
};

/** What the screen may call a request: approved is never shown as active (T45). */
export function requestStateText(r: Pick<GovRequest, "state" | "expired">): { label: string; tone: Tone } {
  if (r.state === "PENDING" && r.expired) return { label: "Expired", tone: "neutral" };
  return STATE_LABEL[r.state];
}

export function reviewAvailability(r: GovRequest): { canReview: boolean; why: string | null } {
  if (r.can_review) return { canReview: true, why: null };
  if (r.review_unavailable === "SEPARATION_OF_DUTIES") {
    return { canReview: false, why: "You requested this change. Another qualified reviewer must approve." };
  }
  if (r.state !== "PENDING") return { canReview: false, why: requestStateText(r).label };
  return { canReview: false, why: "You do not hold a signer slot that is still open on this request." };
}

export function signatureProgress(r: Pick<GovRequest, "required_slots" | "open_slots">): string {
  const done = r.required_slots.length - r.open_slots.length;
  return `${done} of ${r.required_slots.length} signatures`;
}

export function slotLabel(slot: string): string {
  return { SECURITY: "Security", AUTHORITY: "Authority", PRIVACY: "Privacy", FINANCE: "Finance" }[slot] ?? slot;
}

export const TELEMETRY: Record<"CURRENT" | "STALE" | "UNKNOWN", { label: string; tone: Tone }> = {
  CURRENT: { label: "Data is current", tone: "ok" },
  STALE: { label: "Data temporarily unavailable", tone: "warn" },
  UNKNOWN: { label: "No data yet: health is unknown", tone: "warn" },
};

export const SEVERITY_TONE: Record<"P0" | "P1" | "P2", Tone> = { P0: "danger", P1: "warn", P2: "info" };

export const RULE_TEXT: Record<string, string> = {
  INCIDENT_OPEN: "Emergency stop in force",
  SPONSOR_INVALID: "Orphaned sponsor",
  POLICY_DRIFT: "Live policy has no approval for its exact version",
  PROHIBITED_ATTEMPT: "A prohibited action was attempted",
  AUDIT_GAP: "Evidence gap",
  CAP_NEAR_LIMIT: "Cap near its limit",
  GRANT_EXPIRING: "Permission expiring",
  APPROVAL_BACKLOG: "Approvals waiting too long",
  TELEMETRY_UNKNOWN: "No recent evidence",
};

export const DEFAULT_QUOTAS = {
  concurrent_runs_per_agent: "Active runs per agent",
  runs_per_agent_day: "Runs per agent per UTC day",
  runs_per_tenant_day: "Runs per company per UTC day",
  records_per_run: "Records per run",
  records_per_tenant_day: "Records per company per UTC day",
  run_seconds: "Maximum run time (seconds)",
  tool_timeout_seconds: "Tool timeout (seconds)",
  proposals_per_run: "Review proposals per run",
  retries_per_read_step: "Retries per read step",
} as const;

/** A decimal string the server will accept as money: digits, optionally a dot and decimals. No signs, no exponents. */
export function validAmount(value: string, currency: string): string | null {
  const v = value.trim();
  if (!/^\d+(\.\d+)?$/.test(v)) return "Enter an amount such as 5 or 0.10 (digits only).";
  const places = currency === "JPY" ? 0 : 2;
  const frac = v.split(".")[1] ?? "";
  if (frac.length > places) return places === 0 ? `${currency} has no decimal places.` : `${currency} has ${places} decimal places.`;
  return null;
}
