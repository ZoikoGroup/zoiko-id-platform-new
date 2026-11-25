import type { VerificationAxes } from "@zoiko/api-client";

/** The verification result as separate statements (ZID-CP-WF08 section 08.1, CR-170). Nothing here adds the axes up: there is no overall pass. */

export type AxisLine = { label: string; value: string; tone: "ok" | "warn" | "danger" | "neutral" };

const INTEGRITY: Record<VerificationAxes["signature_integrity"], AxisLine["tone"]> = { VALID: "ok", INVALID: "danger", INDETERMINATE: "warn" };
const TRUST: Record<VerificationAxes["issuer_trust"], AxisLine["tone"]> = { TRUSTED: "ok", UNTRUSTED: "danger", UNKNOWN: "warn" };
const COMPLETE: Record<VerificationAxes["proof_completeness"], AxisLine["tone"]> = { COMPLETE: "ok", PARTIAL: "warn", MISSING: "warn" };
const TIME: Record<VerificationAxes["time_proof"], AxisLine["tone"]> = { VERIFIED_TIMESTAMP: "ok", UNVERIFIED_TIMESTAMP: "warn", NONE: "neutral", INVALID: "danger" };

const words = (v: string): string => v.toLowerCase().replaceAll("_", " ");

export function axisLines(a: VerificationAxes): AxisLine[] {
  return [
    { label: "Integrity", value: words(a.signature_integrity), tone: INTEGRITY[a.signature_integrity] },
    { label: "Signer", value: a.issuer_trust === "TRUSTED" ? "a key you trust" : a.issuer_trust === "UNTRUSTED" ? "not a key you trust" : "not known", tone: TRUST[a.issuer_trust] },
    { label: "Proof", value: words(a.proof_completeness), tone: COMPLETE[a.proof_completeness] },
    { label: "Time", value: a.time_proof === "NONE" ? "no outside timestamp" : words(a.time_proof), tone: TIME[a.time_proof] },
    { label: "Status now", value: a.status_now === "NOT_CHECKED" ? "not checked" : words(a.status_now), tone: a.status_now === "NOT_CHECKED" || a.status_now === "UNKNOWN" ? "neutral" : "ok" },
    { label: "Was the decision right?", value: "not assessed here", tone: "neutral" },
  ];
}

/** Green only when the receipt is intact AND signed by a key the verifier pinned. Anything less is a caution, and a failure is a failure. */
export function bannerTone(a: VerificationAxes | undefined, ok: boolean): "ok" | "warn" | "info" | "danger" | undefined {
  if (!a) return undefined;
  if (a.signature_integrity === "INVALID" || a.issuer_trust === "UNTRUSTED") return "danger";
  if (a.signature_integrity === "VALID" && a.issuer_trust === "TRUSTED") return ok ? "ok" : "warn";
  return "info";
}

const REASON: Record<string, string> = {
  NOT_YET_SEALED: "It is in the ledger but a signed checkpoint has not sealed it yet.",
  NOT_YET_LEDGERED: "It is committed but not yet in the ledger.",
  KEYS_FROM_THE_BUNDLE: "The signing keys came with the file itself, so it vouches for itself.",
  SIGNING_KEY_NOT_PINNED_OR_SIGNATURE_BAD: "The signing key is not one you trust, or the signature is bad.",
  NO_EXTERNAL_TIMESTAMP: "No outside timestamp backs the time. The ledger's own clock is all there is.",
  TIMESTAMP_NOT_CHECKED_AGAINST_A_TRUST_LIST: "A timestamp is attached, but nobody checked it against a trusted authority.",
  TIMESTAMP_REJECTED: "The attached timestamp was refused.",
  RECEIPT_HASH_DIFFERS: "This is not the receipt that was committed.",
  STATUS_NOT_FRESH_ENOUGH: "The status was last checked longer ago than you accept.",
  STATUS_AGE_POLICY_NOT_GIVEN: "No maximum age was given, so the status is not treated as current.",
};
export const reasonLine = (code: string): string => REASON[code] ?? (code.startsWith("CHECK_FAILED:") ? `A check failed: ${code.slice(13).replaceAll("_", " ")}.` : code);
