import { ApiError, type SofDecision, type SofQuestion, type SofState } from "@zoiko/api-client";

/** The query key every source of funds screen shares, so a decision refreshes the queue and the case together. */
export const SOF_KEY = ["source-of-funds"];

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

export type StateText = { label: string; tone: Tone; meaning: string };

const STATES: Record<SofState, StateText> = {
  DRAFT: { label: "Draft", tone: "neutral", meaning: "The person has not sent their answers yet." },
  INCOMPLETE: { label: "Incomplete", tone: "warn", meaning: "Something is missing or was asked for. Nothing can be decided until it is sent." },
  NEEDS_REVIEW: { label: "Waiting for a reviewer", tone: "info", meaning: "The answers are complete. A person has not decided yet." },
  CLEARED: { label: "Cleared", tone: "ok", meaning: "A reviewer cleared this case. A clearance lapses." },
  NOT_CLEARED_PENDING_APPEAL: { label: "Not cleared, can be appealed", tone: "danger", meaning: "A reviewer did not clear this case. It is not final: the person can appeal." },
  APPEALED: { label: "Appeal waiting for a reviewer", tone: "info", meaning: "The person appealed. A different reviewer decides it." },
  NOT_CLEARED_FINAL: { label: "Not cleared, final", tone: "danger", meaning: "The appeal was decided and the case was not cleared." },
  CANCELED: { label: "Closed", tone: "neutral", meaning: "The case was closed, or its answers were erased at the person's request." },
};

/** A cleared case counts as cleared only until `valid_until`. After that it is shown as expired, never as still cleared. */
export function stateText(state: SofState, validUntil: string | null, now: Date = new Date()): StateText {
  if (state === "CLEARED" && validUntil && new Date(validUntil).getTime() <= now.getTime()) {
    return { label: "Clearance expired", tone: "warn", meaning: "The clearance has lapsed. Funds and wealth change, so a new case is needed." };
  }
  return STATES[state];
}

export const REASONS: Record<string, string> = {
  FUNDS_EXPLAINED_AND_EVIDENCED: "The funds are explained and evidenced",
  EVIDENCE_CONSISTENT_WITH_ANSWERS: "The evidence is consistent with the answers",
  EVIDENCE_INSUFFICIENT: "The evidence is not enough",
  EVIDENCE_INCONSISTENT: "The evidence does not match the answers",
  SOURCE_NOT_EXPLAINED: "The source of the funds is not explained",
  AMOUNT_NOT_SUPPORTED: "The amount is not supported by the evidence",
  DOCUMENT_UNREADABLE: "A document could not be read",
  OUT_OF_SCOPE: "Out of scope for this check",
  APPEAL_NEW_EVIDENCE: "The appeal brought new evidence",
  APPEAL_ERROR_FOUND: "The first decision contained an error",
  APPEAL_NO_NEW_INFORMATION: "The appeal brought no new information",
};

export const DECISION_LABEL: Record<SofDecision | "APPEAL_FILED", string> = {
  CLEAR: "Clear",
  NOT_CLEAR: "Do not clear",
  ASK_FOR_MORE: "Ask for more",
  APPEAL_FILED: "Appeal filed",
};

const APPEAL_REASONS = ["APPEAL_NEW_EVIDENCE", "APPEAL_ERROR_FOUND", "APPEAL_NO_NEW_INFORMATION"];

/** What a reviewer may decide now. Only a case waiting for a decision, or an appeal, can be decided; an appeal cannot ask for more. */
export function decisionOptions(state: SofState): SofDecision[] {
  if (state === "NEEDS_REVIEW") return ["CLEAR", "NOT_CLEAR", "ASK_FOR_MORE"];
  if (state === "APPEALED") return ["CLEAR", "NOT_CLEAR"];
  return [];
}

/** The reason codes that fit this case: appeal reasons for an appeal, the others for a first decision. */
export function reasonOptions(state: SofState): string[] {
  const all = Object.keys(REASONS);
  return state === "APPEALED" ? APPEAL_REASONS : all.filter((c) => !APPEAL_REASONS.includes(c));
}

/** Only a first adverse decision can be appealed, and only once. */
export function canAppeal(state: SofState): boolean {
  return state === "NOT_CLEARED_PENDING_APPEAL";
}

/** An answer as a person reads it. Amounts stay decimal strings: they are never turned into a number and back. */
export function answerText(question: SofQuestion | undefined, value: unknown): string {
  if (value === undefined || value === null) return "No answer";
  if (question?.answer === "YES_NO") return value === true ? "Yes" : value === false ? "No" : String(value);
  if (question?.answer === "AMOUNT" && typeof value === "object") {
    const v = value as { value?: unknown; currency?: unknown };
    if (typeof v.value === "string" && typeof v.currency === "string") return `${v.value} ${v.currency}`;
  }
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** A problem code the system reported, in words. A code it does not know is shown as the code, never hidden. */
export function problemText(code: string): string {
  const [kind, a, b] = code.split(":");
  switch (kind) {
    case "ANSWER_MISSING":
      return `No answer to ${a}.`;
    case "ANSWER_INVALID":
      return `The answer to ${a} is not in the form the question asks for.`;
    case "ANSWER_UNKNOWN_QUESTION":
      return `${a} is not a question in this questionnaire.`;
    case "EVIDENCE_MISSING":
      return `${a} needs evidence of type ${(b ?? "").toLowerCase().replaceAll("_", " ")}.`;
    case "EVIDENCE_REFERENCE_INVALID":
      return `An evidence reference for ${a} is not valid.`;
    case "EVIDENCE_UPLOAD_NOT_USABLE":
      return `The upload for ${a} was not found, is still being checked, or was removed.`;
    default:
      return code;
  }
}

export type Availability = "off" | "forbidden" | "missing" | "other";

/** The product line is switched off until the owners record a legal basis, and the cell then answers "service unavailable" for every call that
 * starts or changes a case. That reads as "not available", never as an empty queue. */
export function classify(error: unknown): Availability {
  if (!(error instanceof ApiError)) return "other";
  const code = String(error.code ?? "").toLowerCase();
  if (code === "service_unavailable" || code === "service_unavailable_in_market") return "off";
  if (error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) return "forbidden";
  if (error.status === 404) return "missing";
  return "other";
}

export const FILTERS: { id: string; label: string }[] = [
  { id: "", label: "All cases" },
  { id: "NEEDS_REVIEW", label: "Waiting for a reviewer" },
  { id: "APPEALED", label: "Appeals" },
  { id: "INCOMPLETE", label: "Incomplete" },
  { id: "CLEARED", label: "Cleared" },
  { id: "NOT_CLEARED_PENDING_APPEAL", label: "Not cleared, can be appealed" },
  { id: "NOT_CLEARED_FINAL", label: "Not cleared, final" },
];
