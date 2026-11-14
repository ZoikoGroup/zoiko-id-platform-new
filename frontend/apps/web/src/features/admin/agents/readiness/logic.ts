import { ApiError } from "@zoiko/api-client";
import type { ReadinessDecision, ReadinessEvidence, ReadinessRun, ReadinessRunState, ReadinessSetup } from "@zoiko/api-client";

/* Pure rules for the Access Readiness agent screens (ZID-AG-WF-001 WF-AG-01 to WF-AG-04). The server decides every outcome; these only
 * say how an answer is shown, so a refusal is never drawn as an empty success. */

export const READINESS_KEY = ["admin", "agents", "readiness"];
export const TEMPLATE_NAME = "Delegated Authority & Access Readiness";
export const PURPOSE_LABEL = "Access posture review";

/** How an error from these routes is told to the person. */
export type Availability = "off" | "region" | "forbidden" | "missing" | "other";

/** The feature switch answers a generic 404 (NOT_FOUND); a missing object answers its own *_NOT_FOUND code. */
export function classify(error: unknown): Availability {
  if (!(error instanceof ApiError)) return "other";
  if (error.code === "REGION_UNSUPPORTED" || error.code === "REGION_MISMATCH") return "region";
  if (error.status === 404 && (error.code === "NOT_FOUND" || error.code === "FEATURE_DISABLED")) return "off";
  if (error.status === 404) return "missing";
  if (error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) return "forbidden";
  return "other";
}

export const STAGES = [
  { id: "purpose", short: "Purpose", title: "Purpose" },
  { id: "scope", short: "Scope", title: "Scope" },
  { id: "runtime", short: "Runtime", title: "Runtime" },
  { id: "test", short: "Test", title: "Test" },
  { id: "publish", short: "Publish", title: "Publish" },
] as const;
export type StageId = (typeof STAGES)[number]["id"];

export function stageIndex(id: StageId): number {
  return STAGES.findIndex((s) => s.id === id);
}

/** Stages after Purpose need a saved draft. Publish also needs a passing dry run on exactly the current scope. */
export function furthestStage(setup: ReadinessSetup | null | undefined): StageId {
  if (!setup) return "purpose";
  return setup.validated ? "publish" : "test";
}

export type Gate = { id: string; label: string; met: boolean; why: string };

/** The release gates of WF-AG-02: the publishing request stays disabled until every one holds. */
export function publishGates(setup: ReadinessSetup | null | undefined): Gate[] {
  if (!setup) return [];
  return [
    {
      id: "sponsor",
      label: "Accountable sponsor is active",
      met: setup.agent?.sponsor_active === true,
      why: "An agent without an active sponsor is never published.",
    },
    {
      id: "validated",
      label: "Dry run passed on this exact scope",
      met: setup.validated,
      why: "Changing the scope invalidates an earlier dry run. Run it again.",
    },
    {
      id: "draft",
      label: "Setup is a validated draft",
      met: setup.status === "SANDBOX_READY",
      why: setup.status === "PENDING_APPROVAL" ? "A publishing request is already waiting for approval." : "Only a validated draft can be sent for approval.",
    },
  ];
}

export function canRequestPublish(setup: ReadinessSetup | null | undefined): boolean {
  return publishGates(setup).length > 0 && publishGates(setup).every((g) => g.met);
}

/** Field errors by form field, from the server's own answer. Nothing is validated on the client except what is required to ask. */
export function fieldIssues(error: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!(error instanceof ApiError)) return out;
  const byCode: Record<string, string> = {
    NAME_INVALID: "display_name",
    AGENT_SPONSOR_REQUIRED: "sponsor",
    AGENT_NOT_FOUND: "agent",
    BLUEPRINT_NOT_ALLOWED: "agent",
    TOOL_NOT_ALLOWED: "agent",
    SOURCE_NOT_APPROVED: "resource_kinds",
    SCOPE_INVALID: "max_records",
    PURPOSE_NOT_ALLOWED: "purpose",
  };
  const mapped = byCode[error.code];
  if (mapped) out[mapped] = readinessMessage(error.code, error.message);
  for (const d of error.details) {
    const field = d.field?.split(".").pop();
    if (field && d.issue) out[field] = d.issue;
  }
  return out;
}

/** The first field, in form order, that the server refused. Focus goes here. */
export const FIELD_ORDER = ["agent", "sponsor", "display_name", "purpose", "resource_kinds", "max_records", "warning_window_days"];
export function firstInvalid(issues: Record<string, string>): string | undefined {
  return FIELD_ORDER.find((f) => issues[f]) ?? Object.keys(issues)[0];
}

const MESSAGES: Record<string, string> = {
  AGENT_SPONSOR_REQUIRED: "This agent has no active sponsor. Assign an active, accountable sponsor to the agent first.",
  AGENT_NOT_FOUND: "This agent is not registered in your workspace.",
  BLUEPRINT_NOT_ALLOWED: "This agent was not built from the approved Access Readiness blueprint.",
  TOOL_NOT_ALLOWED: "This agent holds a tool the Access Readiness agent may not use.",
  SOURCE_NOT_APPROVED: "That data source is not approved. Choose from the fixed list.",
  PURPOSE_NOT_ALLOWED: "Only the access posture review purpose is available.",
  NAME_INVALID: "Use 1 to 80 printable characters.",
  SCOPE_INVALID: "A number is outside the allowed range.",
  SETUP_NOT_VALIDATED: "Run the dry run on the current scope first.",
  SETUP_NOT_FOUND: "No access readiness setup exists for this agent yet.",
  SETUP_STALE: "Someone changed this draft. Reload it and make your change again.",
  APPROVAL_NOT_INDEPENDENT: "The person who requested publishing cannot approve it. Another authorized person has to.",
  SIGNING_UNAVAILABLE: "No signing key is configured, so no handoff was recorded.",
  PROPOSAL_INVALID_TRANSITION: "This proposal was already reviewed.",
  SETUP_PAUSED: "Runs are paused for this agent. Resume it before changing the setup.",
  POLICY_VERSION_UNKNOWN: "The policy changed since this page loaded. Reload and run the dry run again.",
  RUN_NOT_RETRYABLE: "A finished run cannot be retried.",
  SETUP_INVALID_TRANSITION: "That change is not possible from the setup's current state.",
};

export function readinessMessage(code: string, fallback: string): string {
  return MESSAGES[code] ?? fallback;
}

/** Reason codes as people read them. A code the screen does not know is shown as the code, never hidden. */
const REASONS: Record<string, string> = {
  SPONSOR_NOT_ACTIVE: "The agent's sponsor is not active.",
  AGENT_NOT_ACTIVE: "The agent is not active.",
  AGENT_NOT_ATTESTED: "The agent has no verified attestation.",
  AGENT_EXPIRED: "The agent identity has expired.",
  SETUP_NOT_APPROVED: "The setup is not approved.",
  SETUP_PAUSED: "Runs are paused for this agent.",
  REGION_UNSUPPORTED: "Not available in this region.",
  REGION_MISMATCH: "The run did not start in the region the setup is pinned to.",
  DEPENDENCY_UNAVAILABLE: "A required service was unavailable, so the run failed closed.",
  TOOL_NOT_ALLOWED: "A tool outside the allowed list was refused.",
  FEATURE_DISABLED: "The feature is turned off.",
  PREFLIGHT_PASSED: "All preflight checks passed.",
  READ_PERMITTED: "The read was permitted.",
  TOOL_PERMITTED: "The tool call was permitted.",
  PROPOSAL_CREATED: "A review proposal was created.",
  PROPOSAL_DUPLICATE: "The proposal already existed, so none was added.",
  RULES_APPLIED: "The rules were applied.",
};
export function reasonText(code: string): string {
  return REASONS[code] ?? code;
}

export function isRefusedRun(run: Pick<ReadinessRun, "state">): boolean {
  return run.state === "DENIED";
}

export const RUN_STATE_TEXT: Record<ReadinessRunState, string> = {
  QUEUED: "Queued",
  PREFLIGHT: "Checking",
  RUNNING: "Running",
  DENIED: "Denied by policy",
  COMPLETED: "Completed",
  NEEDS_REVIEW: "Needs review",
  FAILED: "Failed",
  CANCELED: "Canceled",
};

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";
export function severityTone(severity: string): Tone {
  return ({ HIGH: "danger", MEDIUM: "warn", LOW: "info" } as Record<string, Tone>)[severity.toUpperCase()] ?? "neutral";
}

export const RULE_TEXT: Record<string, string> = {
  "R-01": "Expired mandate",
  "R-02": "Expiring mandate",
  "R-03": "Missing sponsor",
  "R-04": "Scope mismatch",
  "R-05": "Evidence gap",
  "R-06": "Unknown state",
};

export type StepState = "done" | "pending" | "stopped" | "unverified";
export type TimelineStep = { id: string; label: string; state: StepState };

/** WF-AG-03 timeline. A step is only "done" when the record shows it; evidence that does not verify marks every step unverified. */
export function timelineSteps(run: ReadinessRun, decisions: ReadinessDecision[] | undefined, evidence: ReadinessEvidence | undefined): TimelineStep[] {
  const summary = run.summary ?? {};
  const denied = run.state === "DENIED";
  const terminal = ["COMPLETED", "NEEDS_REVIEW", "FAILED", "CANCELED", "DENIED"].includes(run.state);
  const authorized = decisions ? decisions.some((d) => d.outcome === "ALLOW") : !denied && run.state !== "QUEUED";
  const read = !denied && (summary.records_read !== undefined || ["COMPLETED", "NEEDS_REVIEW"].includes(run.state));
  const evaluated = run.state === "COMPLETED" || run.state === "NEEDS_REVIEW";
  const proposals = (summary.proposals_created ?? 0) + (summary.proposals_existing ?? 0);
  const broken = evidence !== undefined && !evidence.verified;
  const mark = (done: boolean, stoppedHere: boolean): StepState => (broken && done ? "unverified" : done ? "done" : stoppedHere ? "stopped" : "pending");
  const stopAt = denied ? "authorized" : run.state === "FAILED" || run.state === "CANCELED" ? (read ? "evaluated" : "read") : "";
  return [
    { id: "registered", label: "Registered", state: mark(true, false) },
    { id: "attested", label: "Attested", state: mark(run.worker_attestation_id !== null, denied && run.worker_attestation_id === null) },
    { id: "authorized", label: "Authorized", state: mark(authorized, stopAt === "authorized") },
    { id: "read", label: "Metadata read", state: mark(read, stopAt === "read") },
    { id: "evaluated", label: "Rules evaluated", state: mark(evaluated, stopAt === "evaluated") },
    { id: "proposal", label: "Proposal created", state: mark(proposals > 0, false) },
    { id: "completed", label: "Completed", state: mark(terminal && !denied && run.state !== "FAILED" && run.state !== "CANCELED", false) },
  ];
}
