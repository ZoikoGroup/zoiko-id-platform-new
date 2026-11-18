import type { AgeCheckResult } from "@zoiko/api-client";

/** The thresholds a policy defines. Any other number is refused by the platform. */
export const THRESHOLDS = [13, 16, 18, 21, 25, 65] as const;

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/** What each answer means, in plain words. UNDETERMINED is neither a pass nor a refusal. */
export const RESULT: Record<AgeCheckResult["result"], { label: string; tone: Tone; meaning: string }> = {
  AT_LEAST: { label: "At least the age", tone: "ok", meaning: "The evidence shows the person is at or over the age." },
  UNDER: { label: "Under the age", tone: "danger", meaning: "The evidence shows the person is under the age." },
  UNDETERMINED: { label: "Not decided", tone: "warn", meaning: "The evidence does not settle it. This is neither a pass nor a refusal: offer a documented check instead." },
};

export const METHOD: Record<AgeCheckResult["method"], string> = {
  VERIFIED_BIRTH_DATE: "A birth date from a verified source",
  ESTIMATION: "An age estimate",
  SELF_DECLARED: "A birth date the person typed",
};

const REASONS: Record<string, string> = {
  SELF_DECLARED_NOT_VERIFIED: "A birth date the person typed is not verified, so it can never show that they are at least the age.",
  HOLDER_NOT_BOUND: "A chip proves what the document says, not that the person holding it is the holder.",
  CLONE_NOT_DETECTED: "A copied chip is not detected by this check.",
  ESTIMATE_NEAR_THRESHOLD: "The estimate is too close to the age to decide.",
};

export function reasonWords(code: string): string {
  return REASONS[code] ?? code.toLowerCase().replace(/_/g, " ");
}

export type AgeForm = { threshold: number; birthDate: string };
export const EMPTY: AgeForm = { threshold: 18, birthDate: "" };

/** Why the check cannot be sent yet, or null. The birth date is the person's own statement. */
export function formProblem(f: AgeForm, today: Date = new Date()): string | null {
  if (!(THRESHOLDS as readonly number[]).includes(f.threshold)) return "Choose one of the listed ages.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.birthDate)) return "Enter the birth date as year-month-day.";
  const d = new Date(`${f.birthDate}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== f.birthDate) return "That is not a real date.";
  if (d.getTime() > today.getTime()) return "A birth date cannot be in the future.";
  if (today.getUTCFullYear() - d.getUTCFullYear() > 120) return "That birth date is more than 120 years ago.";
  return null;
}

export const ERROR_TEXT: Record<string, string> = {
  AGE_THRESHOLD_NOT_ALLOWED: "That age is not one a policy defines.",
};
