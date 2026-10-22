import type { SubjectType } from "@zoiko/api-client";

export const STREAMS_KEY = ["admin", "signals", "streams"];

export const SUBJECT_TYPES: { id: SubjectType; label: string; description: string }[] = [
  { id: "PRINCIPAL", label: "Principal", description: "A person, organisation, service or agent." },
  { id: "SESSION", label: "Session", description: "One sign-in session." },
  { id: "CREDENTIAL", label: "Credential", description: "A passkey or issued credential." },
];

/** Canonical event types the cell's signal rules accept (signals/domain/rules.py). */
export const CANONICAL_EVENTS = [
  "session.revoked",
  "sessions.revoked",
  "session.established",
  "token.claims_changed",
  "credential.revoked",
  "credential.changed",
  "credential.compromised",
  "assurance.decreased",
  "assurance.increased",
  "device.noncompliant",
  "device.compliant",
  "account.credential_change_required",
  "account.disabled",
  "account.enabled",
  "account.purged",
  "identifier.changed",
  "identifier.recycled",
  "recovery.activated",
  "recovery.information_changed",
  "principal.compromised",
  "risk.elevated",
] as const;

/** States a governed override may set; terminal states (REVOKED, PURGED…) cannot be set by hand. */
export const OVERRIDE_STATES = [
  { id: "TRUSTED", label: "Trusted", description: "Clear the subject: normal access resumes." },
  { id: "ELEVATED_RISK", label: "Elevated risk", description: "Access continues; policies may tighten." },
  { id: "STEP_UP_REQUIRED", label: "Step-up required", description: "The next protected action needs a fresh passkey check." },
  { id: "REAUTH_REQUIRED", label: "Re-authentication required", description: "Sessions must sign in again." },
  { id: "RESTRICTED", label: "Restricted", description: "Only low-risk actions are allowed." },
] as const;

export type OverrideState = (typeof OVERRIDE_STATES)[number]["id"];

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(v: string) {
  return UUID_RE.test(v.trim());
}
