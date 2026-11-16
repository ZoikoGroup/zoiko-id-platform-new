/** Wording and gating for the DAARA setup, run and review screens (ZID-AG-WF-001). Pure: no React, no network. */
import type { DaaraRun, DaaraSetup, DaaraStage, ReviewProposal } from "@zoiko/api-client";

import type { Tone } from "./logic";

export const DAARA_KEY = ["admin", "agents", "daara"];
export const daaraKey = (...rest: (string | null | undefined)[]) => [...DAARA_KEY, ...rest.map((r) => r ?? "")];

export const STEPS = [
  { id: "purpose", label: "Purpose" },
  { id: "scope", label: "Scope" },
  { id: "runtime", label: "Runtime" },
  { id: "test", label: "Test" },
  { id: "publish", label: "Publish" },
] as const;
export type StepId = (typeof STEPS)[number]["id"];

export const STAGE_LABEL: Record<DaaraStage, { label: string; tone: Tone; hint: string }> = {
  NOT_ENABLED: { label: "Not set up", tone: "neutral", hint: "Start with the purpose." },
  DRAFT: { label: "Draft", tone: "warn", hint: "Not validated yet. Nothing here is live." },
  READY_TO_RUN_SANDBOX: { label: "Validated: run the sandbox test", tone: "info", hint: "The setup compiles. Test it on synthetic records next." },
  SANDBOX_READY: { label: "Sandbox passed", tone: "info", hint: "You can now request the independent approvals." },
  PENDING_APPROVAL: { label: "Waiting for approvals", tone: "warn", hint: "Security and Authority must sign the exact version." },
  ACTIVE: { label: "Live", tone: "ok", hint: "An approved policy is in force. Runs are manual." },
};

/** Which step the person may open: never past a gate that has not passed. */
export function unlockedStep(setup: DaaraSetup | undefined): StepId {
  if (!setup || !setup.enabled) return "purpose";
  if (!setup.setup?.validated) return "scope";
  if (!setup.setup.sandbox_passed) return "test";
  return "publish";
}

export function stepEnabled(setup: DaaraSetup | undefined, id: StepId): boolean {
  const order = STEPS.map((s) => s.id);
  return order.indexOf(id) <= order.indexOf(unlockedStep(setup)) || (id !== "publish" && Boolean(setup?.enabled));
}

const BLOCKER_TEXT: Record<string, string> = {
  AGENT_NOT_FOUND: "This agent does not exist in this company.",
  AGENT_NOT_GOVERNABLE: "A revoked or retired agent cannot be set up.",
  SPONSOR_INVALID: "The agent has no active accountable sponsor. Name a sponsor first.",
  POLICY_UNAVAILABLE: "The company has not set its agent baseline yet, so nothing can be validated.",
  REGION_DENIED: "This region is not allowed by the company baseline. The service is not available here.",
  AI_PROVIDER_NOT_APPROVED: "AI explanations need a provider the company approved by name.",
  NOT_VALIDATED: "The setup has changed since it was last validated. Validate it again.",
  SANDBOX_NOT_PASSED: "The sandbox test has not passed for this exact setup.",
  DRY_RUN_WRITE_ATTEMPT: "The test tried to write for real. That is a failed test and is recorded as a security incident.",
  SANDBOX_MISMATCH: "The sandbox test did not reproduce the expected answer.",
  DAARA_NOT_ENABLED: "Set the agent up first.",
  SCHEDULE_NOT_PERMITTED: "A schedule needs the company's schedule policy and the platform's permission. Runs stay manual until both exist.",
  QUOTA_ABOVE_PLATFORM_CEILING: "The platform does not allow this limit for any company yet.",
};

export function blockerText(code: string): string {
  const [head, tail] = code.split(":");
  if (head === "AGENT_TOOLS_MISSING") return `The agent does not list these template tools: ${tail}. Register it with them; no other tool may be added.`;
  if (head === "BASELINE_TOOLS_MISSING") return `The company baseline does not yet allow: ${tail}.`;
  if (head === "TOOL_NOT_REGISTERED") return `The template tool ${tail} is not active in the registry.`;
  return BLOCKER_TEXT[head ?? ""] ?? "A requirement is not met.";
}

export const RUN_STATE: Record<DaaraRun["state"], { label: string; tone: Tone }> = {
  QUEUED: { label: "Queued: waiting for a worker", tone: "info" },
  COMPLETED: { label: "Completed: nothing to review", tone: "ok" },
  NEEDS_REVIEW: { label: "Needs review", tone: "warn" },
  FAILED: { label: "Failed: not a result", tone: "danger" },
  DENIED: { label: "Denied before it ran", tone: "danger" },
  CANCELED: { label: "Cancelled", tone: "neutral" },
};

/** An incomplete run is never shown as a clean pass, whatever its state says. */
export function runLabel(r: Pick<DaaraRun, "state" | "incomplete">): { label: string; tone: Tone } {
  if (r.incomplete && r.state === "NEEDS_REVIEW") return { label: "Needs review: incomplete, not a clean result", tone: "warn" };
  return RUN_STATE[r.state];
}

export function runHeadline(r: Pick<DaaraRun, "checked" | "issues" | "proposals" | "privileges_changed">): string {
  return `Checked ${r.checked} record${r.checked === 1 ? "" : "s"} | ${r.issues} issue${r.issues === 1 ? "" : "s"} flagged | ${r.proposals} review proposal${r.proposals === 1 ? "" : "s"} | ${r.privileges_changed} privileges changed`;
}

export const TIMELINE: { step: string; label: string }[] = [
  { step: "run.start", label: "Started and authorized to start" },
  { step: "daara.authorized", label: "Tools authorized" },
  { step: "daara.metadata_read", label: "Metadata read" },
  { step: "daara.rules_evaluated", label: "Rules evaluated" },
  { step: "daara.proposals_written", label: "Proposals created" },
  { step: "daara.completed", label: "Completed" },
];

export function timelineStatus(have: string[]): { step: string; label: string; done: boolean }[] {
  const set = new Set(have);
  return TIMELINE.map((t) => ({ ...t, done: set.has(t.step) }));
}

export const SEVERITY: Record<ReviewProposal["severity"], { label: string; tone: Tone }> = {
  HIGH: { label: "High", tone: "danger" },
  MEDIUM: { label: "Medium", tone: "warn" },
  LOW: { label: "Low", tone: "info" },
};

export const PROPOSAL_STATUS: Record<ReviewProposal["status"], { label: string; tone: Tone }> = {
  OPEN: { label: "Open", tone: "warn" },
  ASSIGNED: { label: "Assigned for investigation", tone: "info" },
  DISMISSED: { label: "Dismissed", tone: "neutral" },
  HANDED_OFF: { label: "Handed to the governed access workflow", tone: "ok" },
};

export const DISMISS_REASONS = [
  { id: "NOT_A_RISK", label: "Not a risk" },
  { id: "ALREADY_HANDLED", label: "Already handled" },
  { id: "DUPLICATE", label: "Duplicate" },
  { id: "ACCEPTED_RISK", label: "Risk accepted" },
];

export const FINDING_TEXT: Record<string, string> = {
  EXPIRED_MANDATE: "A mandate is still active after its end date.",
  EXPIRING_SOON: "A mandate ends soon. This is not a revocation order.",
  SPONSOR_MISSING: "An active agent has no active accountable sponsor.",
  SCOPE_VARIANCE: "A mandate allows more than its source grant does.",
  EVIDENCE_GAP: "The evidence behind a mandate or policy is missing or unverifiable.",
  CONSOLIDATED_FINDINGS: "More findings than the proposal limit were folded into this summary.",
  UNKNOWN_STATE: "State could not be established.",
};

export function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `run-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
