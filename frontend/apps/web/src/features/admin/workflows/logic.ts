import { ApiError, type WfProblem, type WfStatus } from "@zoiko/api-client";

export const WF_KEY = ["workflows"];

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

export const STATUS: Record<WfStatus, { label: string; tone: Tone; meaning: string }> = {
  DRAFT: { label: "Draft", tone: "neutral", meaning: "Not approved. It cannot run. A different person approves it once it passes the safety rules." },
  APPROVED: { label: "Approved", tone: "ok", meaning: "Approved and frozen. A change is a new version. New runs may start on it." },
  RETIRED: { label: "Retired", tone: "warn", meaning: "No new runs start on this version. Runs already under way finish on it." },
};

/** The outcomes a check can come back with, in the words the platform uses. A result that is not VERIFIED never approves anyone. */
export const OUTCOMES = ["VERIFIED", "VERIFIED_LIMITED", "NOT_VERIFIED", "INDETERMINATE", "PENDING", "REVOKED", "EXPIRED", "UNAVAILABLE"];

const PROBLEMS: Record<string, string> = {
  START_MISSING: "The workflow has no valid first step.",
  TARGET_MISSING: "A step points to a step that does not exist.",
  STEP_UNREACHABLE: "A step can never be reached.",
  CYCLE: "The steps loop and would never end.",
  NO_END: "No step ends the workflow.",
  TOO_COMPLEX: "There are too many paths to review. Simplify it.",
  APPROVAL_NOT_GUARDED: "A path reaches Approved without every required check having come back Verified.",
  APPROVAL_SKIPS_A_REQUIRED_CHECK: "A path reaches Approved without running a required check.",
  REGULATED_CHECK_AUTO_APPROVES: "A regulated check can lead to Approved with no person looking after it.",
  ADDRESS_CHECK_AUTO_APPROVES: "An address check can lead to Approved with no person looking after it. An address result supports an address; it does not prove someone lives there.",
  REQUIRED_CHECK_NOT_RUN: "A required check never runs in this workflow.",
  PRODUCT_LINE_NOT_AVAILABLE: "A check needs a regulated service that is not switched on.",
  APPROVER_IS_AUTHOR: "A different person must approve a workflow.",
  APPROVER_MISSING: "An approver is needed.",
};

const PARSE: Record<string, string> = {
  WORKFLOW_INVALID: "The workflow's id, version, start or steps are not valid.",
  STEP_INVALID: "A step is not valid. Only the fixed step types and keys are accepted.",
  CONDITION_INVALID: "A condition is not valid. Only check and outcome, all of and any of are allowed.",
  DECISION_WITHOUT_ELSE: "Every decision needs an else, so an unexpected result goes to a person on purpose.",
  END_OUTCOME_NOT_ALLOWED: "A workflow can end Approved, Refer to person or Completed with no decision. It never declines anyone.",
};

export function problemText(p: WfProblem): string {
  const base = PROBLEMS[p.code] ?? p.code;
  return p.where ? `${base} (${p.where})` : base;
}

export function parseErrorText(code: string | null): string {
  return code ? (PARSE[code] ?? code) : "";
}

/** The checks a definition says it requires, for the simulator's inputs. Reads only the fixed `required_checks` list, never anything else. */
export function requiredChecks(definition: Record<string, unknown> | undefined): string[] {
  const raw = definition?.["required_checks"];
  return Array.isArray(raw) ? raw.filter((c): c is string => typeof c === "string") : [];
}

export function canApprove(status: WfStatus, author: string, me: string | undefined, problems: WfProblem[] | null): boolean {
  return status === "DRAFT" && (problems ?? []).length === 0 && Boolean(me) && me !== author;
}

export type Availability = "forbidden" | "missing" | "other";

export function classify(error: unknown): Availability {
  if (!(error instanceof ApiError)) return "other";
  if (error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) return "forbidden";
  if (error.status === 404) return "missing";
  return "other";
}

export const STARTER = `{
  "id": "my_workflow",
  "version": "0.1.0",
  "start": "identity",
  "required_checks": ["IDENTITY_DOCUMENT", "LIVENESS"],
  "allow_limited": false,
  "steps": {
    "identity": { "type": "CHECK", "check": "IDENTITY_DOCUMENT", "next": "liveness" },
    "liveness": { "type": "CHECK", "check": "LIVENESS", "next": "decide" },
    "decide": {
      "type": "DECISION",
      "branches": [
        {
          "when": { "all_of": [
            { "check": "IDENTITY_DOCUMENT", "outcome_in": ["VERIFIED"] },
            { "check": "LIVENESS", "outcome_in": ["VERIFIED"] }
          ] },
          "next": "approved"
        }
      ],
      "else": "look"
    },
    "look": { "type": "REVIEW", "role": "verification_reviewer", "next": "refer" },
    "refer": { "type": "END", "outcome": "REFER_TO_PERSON" },
    "approved": { "type": "END", "outcome": "APPROVED" }
  }
}
`;

/** Parse what the person typed. A definition is data: anything that is not a JSON object is refused before it is sent. */
export function parseDefinition(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  try {
    const value: unknown = JSON.parse(text);
    if (value === null || typeof value !== "object" || Array.isArray(value)) return { ok: false, error: "A workflow is one JSON object." };
    return { ok: true, value: value as Record<string, unknown> };
  } catch (e) {
    return { ok: false, error: `This is not valid JSON: ${e instanceof Error ? e.message : "unreadable"}` };
  }
}
