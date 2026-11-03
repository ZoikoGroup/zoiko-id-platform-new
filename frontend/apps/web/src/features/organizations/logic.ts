import type {
  ApprovalScope,
  OnboardingItem,
  OrgApprovalCase,
  OrgApprovalQuery,
  OrgApprovalSummary,
  OrgDecision,
  OrgReadiness,
} from "@zoiko/api-client";

/**
 * What the WF-013C screens decide for themselves: nothing about authority. These helpers build requests, choose words and read the
 * server's refusals. Who may vote, whether an organization can be activated and what a request means are all answered by the server.
 */

/** The nav and old links say "current"; the server only ever answers for the caller's own organization anyway. */
export const CURRENT = "current";
export const orgBase = (orgId: string) => `/organizations/${encodeURIComponent(orgId)}`;
export const setupHref = (orgId: string) => `${orgBase(orgId)}/setup`;
export const inboxHref = (orgId: string) => `${orgBase(orgId)}/approvals`;
export const requestHref = (orgId: string, id: string) => `${inboxHref(orgId)}/${encodeURIComponent(id)}`;
export const GOVERNANCE_HREF = "/admin/approvals/governance";

/** `current` becomes the signed-in person's own organization once it is known; an explicit id is used as it is. */
export function resolveOrgId(param: string, tenantId: string | null | undefined): string | null {
  return param === CURRENT ? (tenantId ?? null) : param;
}

// ------------------------------------------------------------------------------------------------ the inbox (13C-02)

export const TABS: { id: ApprovalScope; label: string }[] = [
  { id: "review", label: "To review" },
  { id: "mine", label: "Submitted by me" },
  { id: "history", label: "History" },
];

export type InboxFilters = { risk: string; state: string; from: string; q: string };
export const noFilters: InboxFilters = { risk: "", state: "", from: "", q: "" };

/** The query for one tab: only what the person set, plus the cursor for "Load more". The server authorizes first and filters second. */
export function inboxQuery(scope: ApprovalScope, f: InboxFilters, cursor?: string | null): OrgApprovalQuery {
  const q: OrgApprovalQuery = { scope, limit: 25 };
  if (f.risk === "A1" || f.risk === "A2") q.risk = f.risk;
  if (f.state) q.state = f.state as OrgApprovalQuery["state"];
  if (f.from) q.created_from = new Date(`${f.from}T00:00:00`).toISOString();
  if (f.q.trim()) q.q = f.q.trim().toLowerCase();
  if (cursor) q.cursor = cursor;
  return q;
}

export const hasFilters = (f: InboxFilters) => Boolean(f.risk || f.state || f.from || f.q.trim());

/** The sentence a screen reader hears when the list or a count changes. */
export function countsAnnouncement(counts: Record<ApprovalScope, number> | undefined, scope: ApprovalScope, shown: number): string {
  if (!counts) return "";
  const tab = TABS.find((t) => t.id === scope)?.label ?? scope;
  return `${tab}: showing ${shown} of ${counts[scope]}. To review ${counts.review}, submitted by me ${counts.mine}, history ${counts.history}.`;
}

export const tabLabel = (t: { id: ApprovalScope; label: string }, counts?: Record<ApprovalScope, number>) =>
  counts ? `${t.label} (${counts[t.id]})` : t.label;

export const STATE_FILTER_OPTIONS = [
  { id: "", label: "Every state" },
  { id: "PENDING_APPROVAL", label: "Pending approval" },
  { id: "APPROVED_READY", label: "Approved, waiting to be applied" },
  { id: "EXECUTED", label: "Executed" },
  { id: "NEEDS_CHANGES", label: "Needs changes" },
  { id: "REJECTED", label: "Denied" },
  { id: "EXPIRED", label: "Expired" },
  { id: "CANCELLED", label: "Cancelled" },
  { id: "INVALIDATED", label: "Replaced" },
];

export const RISK_OPTIONS = [
  { id: "", label: "Any level" },
  { id: "A1", label: "Sensitive (A1)" },
  { id: "A2", label: "Critical (A2)" },
];

export const levelWords = (level: "A1" | "A2") => (level === "A2" ? "Critical (A2): two independent approvals" : "Sensitive (A1): one independent approval");

/** Colour is never the only signal: the word is always shown, the tone only reinforces it. */
export function statusTone(state: OrgApprovalSummary["state"]): "ok" | "warn" | "danger" | "info" | "neutral" {
  if (state === "EXECUTED") return "ok";
  if (state === "REJECTED" || state === "FAILED") return "danger";
  if (state === "NEEDS_CHANGES") return "warn";
  if (state === "APPROVED_READY") return "info";
  if (state === "EXPIRED" || state === "INVALIDATED" || state === "CANCELLED") return "neutral";
  return "warn";
}

// ------------------------------------------------------------------------------------------- the decision (13C-03)

export const VOTE_ACTION = "execute-sensitive-approval-rejection";

/** What the passkey is asked to approve: this request, this exact version hash, this vote. The server rebuilds and checks it. */
export function stepUpDraft(c: Pick<OrgApprovalCase, "case_id" | "request_hash" | "tier">, vote: "APPROVE" | "REJECT") {
  return {
    action: VOTE_ACTION,
    resource: `approval-case:${c.case_id}`,
    change: { case_id: c.case_id, request_hash: c.request_hash, vote },
    critical: c.tier === "A2",
  };
}

export const needsStepUp = (d: OrgDecision) => d === "APPROVE" || d === "REJECT";
export const needsReason = (d: OrgDecision) => d === "REJECT" || d === "REQUEST_CHANGES";

/** One key per intention to decide, so a double click or a retry is one decision, and a new decision is a new key. */
export function decisionKey(caseId: string, decision: OrgDecision, attempt: string): string {
  return `dec-${caseId}-${decision}-${attempt}`.slice(0, 120);
}

export type Refusal = { kind: "conflict" | "needs_version" | "refused" | "other"; code: string };

/** 409 VERSION_CONFLICT means the request changed since it was read: refresh, no side effects, the old form is disabled. */
export function refusalKind(code: string | undefined, status?: number): Refusal["kind"] {
  if (code === "VERSION_CONFLICT" || (status === 409 && code === "APPROVAL_HASH_MISMATCH")) return "conflict";
  if (status === 428 || code === "PRECONDITION_REQUIRED") return "needs_version";
  if (code && (code.startsWith("VOTER_") || code.startsWith("APPROVAL_"))) return "refused";
  return "other";
}

export const CONFLICT_MESSAGE = "This request changed since you opened it. Nothing was recorded. Refresh to review the current version before deciding.";

export const HOLD_WORDS: Record<string, string> = {
  POLICY_CHANGED: "The policy this request was made under has changed, so it cannot run. The approvals are kept; start a new request.",
  REGION_CHANGED: "The data region changed since this request was made, so it cannot run. Start a new request.",
  TENANT_NOT_SERVING: "The organization is suspended or closed, so nothing can be applied right now.",
  INCOMING_OWNER_STRONG_AUTHENTICATOR: "The incoming Owner no longer has a passkey, so the change cannot run.",
  AGENT_PENDING_APPROVAL: "The agent is no longer waiting for approval, so the change cannot run.",
};
export const holdWords = (code: string | null | undefined) => (code ? (HOLD_WORDS[code] ?? `The change is held: ${code.replace(/_/g, " ").toLowerCase()}.`) : null);

export type DeltaRow = { label: string; value: string };

/** The immutable facts an approver signs: id, version, requester, level, policy, region, expiry, reason, then the change itself. */
export function deltaRows(c: OrgApprovalCase): { facts: DeltaRow[]; change: DeltaRow[] } {
  const facts: DeltaRow[] = [
    { label: "Request id", value: c.case_id },
    { label: "Version", value: String(c.version) },
    { label: "Requested by", value: c.requester_principal_id },
    { label: "Level", value: c.level },
    { label: "Policy version", value: c.policy_version },
    { label: "Data region", value: c.region },
    { label: "Expires", value: c.expires_at },
    { label: "Justification", value: c.reason },
  ];
  const change = Object.entries(c.payload)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => ({ label: k.replace(/_/g, " "), value: typeof v === "string" ? v : JSON.stringify(v) }));
  return { facts, change };
}

/** "0 of 2" progress, with who the request is not for: the requester sees "not eligible" and the server's reason. */
export function progressWords(c: Pick<OrgApprovalCase, "approvals_received" | "approvals_required">): string {
  return `${c.approvals_received} of ${c.approvals_required} independent approvals`;
}

export const isRequesterView = (c: Pick<OrgApprovalCase, "you">) => c.you?.is_requester === true;
export const canDecide = (c: Pick<OrgApprovalCase, "you" | "state">) =>
  c.you?.may_vote === true && (c.state === "PENDING_APPROVAL" || c.state === "PARTIALLY_APPROVED");
export const canRequestChanges = (c: Pick<OrgApprovalCase, "you" | "state">) =>
  c.you?.is_requester !== true && c.you?.may_vote === true && ["PENDING_APPROVAL", "PARTIALLY_APPROVED", "APPROVED_READY"].includes(c.state);
export const canApply = (c: Pick<OrgApprovalCase, "state" | "execution_hold">) => (c.state === "APPROVED_READY" || c.state === "FAILED") && !c.execution_hold;
export const canResubmit = (c: Pick<OrgApprovalCase, "you" | "state">) => c.you?.is_requester === true && ["PENDING_APPROVAL", "PARTIALLY_APPROVED", "NEEDS_CHANGES"].includes(c.state);

// -------------------------------------------------------------------------------------------------- setup (13C-01)

export const READINESS_WORDS: Record<OrgReadiness, { title: string; body: string; tone: "ok" | "warn" | "danger" | "info" }> = {
  DRAFT: { title: "Setup not started", body: "Nothing is verified yet. Ordinary use is limited until the checks below are done.", tone: "info" },
  LIMITED_SETUP: { title: "Limited setup", body: "Some checks or required steps are still open. Privileged actions stay restricted until they are done.", tone: "warn" },
  READY_FOR_ACTIVATION: { title: "Ready to activate", body: "Every verification and required step is complete. Activation is the last step.", tone: "info" },
  ACTIVE: { title: "Active", body: "Your organization is active. It stays active only while the facts it rests on still hold.", tone: "ok" },
  ACTIVE_LIMITED: { title: "Active, with limits", body: "Your organization was activated, but a check or a required step no longer holds, so some functions are limited.", tone: "warn" },
  RESTRICTED: { title: "Restricted", body: "A verification check was declined. Privileged functions are restricted until it is resolved.", tone: "danger" },
};

export function itemTone(i: Pick<OnboardingItem, "status">): "ok" | "warn" | "danger" | "info" | "neutral" {
  if (i.status === "Verified") return "ok";
  if (i.status === "Restricted") return "danger";
  if (i.status === "Needs action") return "warn";
  if (i.status === "In review") return "info";
  return "neutral";
}

/** Where each unfinished item sends the person next. Only places that exist; never a payment or plan page. */
export function remediationHref(key: string): string | null {
  switch (key) {
    case "company":
    case "authority":
    case "risk":
      return "/admin/organization";
    case "person_assurance":
      return "/account/verification";
    case "privileged_account":
      return "/admin/team";
    case "approvers":
      return GOVERNANCE_HREF;
    default:
      return null;
  }
}

export const SOLO_MESSAGE = "You can manage your organization on your own. Certain high-risk actions need independent approval.";

/** The explicit reasons an activation is refused, from the server's 409 details. */
export function blockerLines(details: { field?: string; issue: string }[] | undefined, fallback: string[]): string[] {
  const lines = (details ?? []).filter((d) => d.field === "blocker").map((d) => d.issue);
  return lines.length ? lines : fallback;
}

/** A fresh key for each acknowledgement attempt so a double click is one acceptance. */
export const ackKey = (kind: string, version: string) => `ack-${kind}-${version}`.slice(0, 120);

export const ACK_ORDER = ["ORGANIZATIONAL_CONTROLS", "REGION_POLICY"] as const;

/** Match a blocker line ("Authority to represent the company: ...") to the item it came from, to offer the right next step. */
export function remediationKeyFor(line: string): string | null {
  const pairs: [string, string][] = [
    ["Legal entity", "company"],
    ["Your identity", "person_assurance"],
    ["Authority", "authority"],
    ["Screening", "risk"],
    ["Secure every privileged account", "privileged_account"],
  ];
  return pairs.find(([title]) => line.startsWith(title))?.[1] ?? null;
}
