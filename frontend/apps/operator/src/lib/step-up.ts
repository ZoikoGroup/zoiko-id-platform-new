/** Action-bound step-up, as the person sees it (ZID-ENG-DEC-009). Wording and parsing only: the server classifies every command, picks the
 * tier and the method, and decides; nothing here grants anything or chooses a lower tier. */
import { ApiError } from "@zoiko/api-client";

export const PROOF_HEADER = "x-step-up-proof";

export class StepUpCancelled extends Error {
  constructor() {
    super("The confirmation was cancelled.");
    this.name = "StepUpCancelled";
  }
}

export type StepUpNeed = { tier: "S1" | "S2"; action: string; reason: string };

/** The refusal an action-bound command returns carries the tier and the action; a plain session step-up does not. */
export function stepUpNeed(e: unknown): StepUpNeed | null {
  if (!(e instanceof ApiError) || e.code !== "STEP_UP_REQUIRED") return null;
  const field = (name: string) =>
    e.details.find((d) => d.field === name)?.issue;
  const tier = field("tier");
  const action = field("action");
  if ((tier !== "S1" && tier !== "S2") || !action) return null;
  return { tier, action, reason: field("reason") ?? "STEP_UP_REQUIRED" };
}

/** True when a fresh sign-in of the session cannot help: the command needs its own proof. */
export function isActionBound(e: unknown): boolean {
  return stepUpNeed(e) !== null;
}

export function secondsLeft(expiresAt: string, nowMs: number): number {
  const ms = Date.parse(expiresAt) - nowMs;
  return Number.isFinite(ms) ? Math.max(0, Math.ceil(ms / 1000)) : 0;
}

export function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export const TIER_COPY: Record<"S1" | "S2", { title: string; body: string }> = {
  S1: {
    title: "Confirm it's you",
    body: "This change needs a fresh sign-in with your passkey. It stays valid for ten minutes for similar changes.",
  },
  S2: {
    title: "Approve this exact change",
    body: "This is a critical change. Your passkey approves the details shown here, once. If anything changes, you will be asked again.",
  },
};

const REFUSAL: Record<string, string> = {
  STEP_UP_REQUIRED: "A fresh confirmation with your passkey is needed.",
  STEP_UP_PROOF_EXPIRED: "The confirmation expired. Confirm again.",
  STEP_UP_PROOF_CONSUMED: "That confirmation was already used. Confirm again.",
  STEP_UP_PAYLOAD_MISMATCH:
    "The details changed after you confirmed. Confirm the new details.",
  STEP_UP_ACTION_MISMATCH: "That confirmation was for a different action.",
  STEP_UP_SESSION_MISMATCH:
    "That confirmation belongs to another sign-in. Confirm again here.",
  STEP_UP_ACTOR_MISMATCH: "That confirmation belongs to someone else.",
  STEP_UP_TIER_INSUFFICIENT:
    "A stronger confirmation is needed for this change.",
  STEP_UP_HUMAN_REQUIRED:
    "Only a signed-in person can confirm this. A service or an agent cannot.",
  NO_STRONG_AUTHENTICATOR:
    "No passkey is registered. Use account recovery; email and text messages cannot confirm this.",
  STEP_UP_CHALLENGE_EXPIRED: "The confirmation request timed out. Start again.",
  STEP_UP_BINDING_MISMATCH:
    "The passkey did not sign this change. Start again.",
  STEP_UP_ASSURANCE_INSUFFICIENT:
    "That passkey check was not strong enough. Use a passkey with a PIN or biometric.",
  STEP_UP_ACTION_UNKNOWN: "This action cannot be confirmed here.",
};

export function refusalText(code: string): string {
  return REFUSAL[code] ?? "The confirmation could not be completed.";
}

/** The sentence that says why, in plain words, from the tier the server chose. */
export function whyRequired(
  tier: "S1" | "S2",
  control: string | undefined,
): string {
  const base =
    tier === "S2"
      ? "Critical changes need your passkey and are approved once, for exactly what you see."
      : "Sensitive changes need a recent passkey check.";
  return control ? `${base} ${control}` : base;
}
