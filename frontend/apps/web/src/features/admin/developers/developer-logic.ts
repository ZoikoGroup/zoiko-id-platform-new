import type { DevChecklist, DevEndpointHealth, DevErrorClass, DevScopeChange, DevSdk, DevSuiteStatus } from "@zoiko/api-client";

/** Plain-language rules for the developer home (ZID-CP-WF10 v2.0, CR-173). Nothing here calls anything ready that the platform has not proved. */

export function checklistHeadline(c: Pick<DevChecklist, "ready" | "next_step" | "next_action">): { tone: "ok" | "info"; text: string } {
  if (c.ready) return { tone: "ok", text: "Your server confirmed a signed webhook it verified. This integration works in the sandbox." };
  return { tone: "info", text: c.next_action ?? "Finish the steps below." };
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "Not finished yet";
  if (seconds < 60) return `${seconds} seconds`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"}`;
  const h = Math.floor(m / 60);
  return `${h} hour${h === 1 ? "" : "s"} ${m % 60} min`;
}

const ACTION_WORDS: Record<DevSdk["action"], string> = { DOWNLOAD: "Download", TRY_BETA: "Try the beta", VIEW_SOURCE: "View the source" };
export const sdkAction = (s: Pick<DevSdk, "action">): string => ACTION_WORDS[s.action];

const PROBLEM_WORDS: Record<string, string> = {
  NOT_PUBLISHED: "Not published to a package registry.",
  GA_NEEDS_SIGNED_RELEASE_AND_SBOM: "A general release needs a signed release and a software bill of materials.",
  GA_NEEDS_AN_EXECUTED_TEST_RUN_WITH_EVIDENCE: "A general release needs an executed test run with its evidence.",
  GA_TEST_RUN_IS_STALE: "The last test run is more than 90 days old.",
  GA_NEEDS_A_SUPPORTED_PLATFORM_WITH_EVIDENCE: "No supported platform is backed by an executed test run.",
  SUPPORTED_PLATFORM_WITHOUT_EXECUTED_EVIDENCE: "A platform is listed as supported without evidence.",
  PUBLISHED_WITHOUT_A_VERSION: "Published without a version.",
};
export const sdkProblem = (code: string): string => PROBLEM_WORDS[code] ?? code;

export function sdkStateWords(s: Pick<DevSdk, "state" | "published">): string {
  if (s.state === "EXPERIMENTAL") return "Experimental: in the repository, not for production use";
  if (s.state === "BETA") return s.published ? "Beta" : "Beta (not yet published)";
  if (s.state === "GA") return "Generally available";
  return "Deprecated";
}

/** What a developer should do on an error, shortest first. A class the platform does not raise yet says so. */
export function errorGuidance(e: Pick<DevErrorClass, "caller_behavior" | "retryable" | "raised_today">): string {
  const base = e.retryable ? `${e.caller_behavior} (safe to retry after waiting)` : e.caller_behavior;
  return e.raised_today ? base : `${base} The platform does not raise this one yet; handle it so you are ready.`;
}

export const SCOPE_WORDS: Record<string, string> = {
  "products:read": "See which products you can verify",
  "workflows:read": "See your approved workflows",
  "verifications:create": "Start verification sessions",
  "verifications:read": "Read a session and its result",
  "verifications:list": "List sessions",
  "verifications:resume": "Resume a session",
  "verifications:recheck": "Start a recheck of an earlier result",
  "decisions:read": "Read decisions",
  "webhooks:read": "Read webhook endpoints and confirm deliveries",
  "webhooks:write": "Change webhook endpoints",
  "usage:read": "Read usage",
  "applications:submit": "Ask for production access",
};
export const scopeWords = (s: string): string => SCOPE_WORDS[s] ?? s;

/** What a scope change is waiting for, in plain words. */
export function scopeChangeState(c: Pick<DevScopeChange, "needs_approval" | "approved_by" | "pending" | "proposed_by">, me: string | null): { state: string; canApprove: boolean } {
  if (c.pending) return { state: "Takes effect later. It is staged.", canApprove: false };
  if (!c.needs_approval) return { state: "In force.", canApprove: false };
  const others = c.approved_by.filter((a) => a !== c.proposed_by);
  if (others.length) return { state: "In force. A second person approved it.", canApprove: false };
  return { state: "Waiting for a second person. It is not in force yet.", canApprove: me !== null && me !== c.proposed_by };
}

const VERDICT_WORDS: Record<DevEndpointHealth["verdict"], string> = { HEALTHY: "Healthy", DEGRADED: "Some deliveries are failing", FAILING: "Failing", UNKNOWN: "Nothing delivered yet" };
export const verdictWords = (v: DevEndpointHealth["verdict"]): string => VERDICT_WORDS[v];

export function suiteHeadline(s: Pick<DevSuiteStatus, "status" | "personas_passed" | "personas_total">): string {
  if (s.status === "NOT_RUN") return "No scored test run yet.";
  if (s.status === "PASSED") return `Passed every scenario (${s.personas_passed} of ${s.personas_total}). This is a test result, not a certification.`;
  return "The latest run does not count: it failed, covers an older catalog, missed a scenario or is more than 90 days old.";
}
