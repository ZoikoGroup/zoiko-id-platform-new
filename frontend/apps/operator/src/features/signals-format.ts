import type { SignalOutcome, SignalReview } from "@zoiko/api-client";

/** Plain-language wording for the signal queue. A signal is a flag for a person to look at; nothing here says a person is guilty or safe. */

const DIMENSION: Record<string, string> = { DEVICE: "device", CONTACT: "contact detail", NETWORK: "network" };
const ACTION: Record<string, string> = { SIGNUP: "sign-ups", VERIFICATION: "verification attempts", CLAIM: "claims" };

export const OUTCOME_LABEL: Record<SignalOutcome, string> = {
  CLEARED_BY_REVIEWER: "Cleared by reviewer",
  CONFIRMED_RISK: "Risk confirmed",
  ESCALATED: "Escalate",
};

export const OUTCOME_HELP: Record<SignalOutcome, string> = {
  CLEARED_BY_REVIEWER: "You looked and found nothing that needs action.",
  CONFIRMED_RISK: "You looked and the risk is real. This records your finding; it does not block anything by itself.",
  ESCALATED: "You cannot decide. Hand it to someone with more authority.",
};

export function windowText(seconds: number | undefined): string {
  if (seconds === undefined) return "an unknown period";
  if (seconds >= 86_400 && seconds % 86_400 === 0) return `${seconds / 86_400} day${seconds === 86_400 ? "" : "s"}`;
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} hour${seconds === 3600 ? "" : "s"}`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} minute${seconds === 60 ? "" : "s"}`;
  return `${seconds} second${seconds === 1 ? "" : "s"}`;
}

/** "5 sign-ups from one device in 1 hour (limit 3)". */
export function velocitySummary(d: SignalReview["detail"]): string {
  const what = ACTION[d.action ?? ""] ?? "actions";
  const who = DIMENSION[d.dimension ?? ""] ?? "source";
  const count = d.count ?? "Several";
  const limit = d.threshold === undefined ? "" : ` (limit ${d.threshold})`;
  return `${count} ${what} from one ${who} in ${windowText(d.window_seconds)}${limit}`;
}

/** "Adverse media: 2 possible matches, severity high (from provider)". */
export function adverseMediaSummary(d: SignalReview["detail"]): string {
  const hits = d.hit_count ?? 0;
  const matches = hits === 0 ? "no matches" : `${hits} possible ${hits === 1 ? "match" : "matches"}`;
  const sev = d.severity ? `, severity ${d.severity.toLowerCase()}` : "";
  const where = d.provider ? ` (from ${d.provider})` : "";
  return `Adverse media: ${matches}${sev}${where}`;
}

export function signalSummary(s: Pick<SignalReview, "source" | "detail">): string {
  return s.source === "VELOCITY" ? velocitySummary(s.detail) : adverseMediaSummary(s.detail);
}

/** Shortened hash for a table cell; the full value goes in a title. */
export function shortHash(value: string): string {
  return value.length > 16 ? `${value.slice(0, 10)}…${value.slice(-4)}` : value;
}

export function ageText(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "unknown";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86_400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86_400)} d`;
}

export const SIGNAL_ERROR_HELP: Record<string, string> = {
  REVIEW_NOT_FOUND: "This signal is no longer in the queue. Reload the page.",
  SIGNAL_ALREADY_RESOLVED: "Someone already resolved this signal. Reload the page to see the ruling.",
  STATEMENT_INVALID: "The statement must be between 20 and 2000 characters.",
  EVIDENCE_REF_REQUIRED: "Add an evidence reference: where someone can check what you relied on.",
  RATIONALE_REQUIRED: "Write a rationale of at least 20 characters.",
  RULING_OUTCOME_INVALID: "Choose one of the three outcomes.",
};
