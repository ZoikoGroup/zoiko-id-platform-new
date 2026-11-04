import type { VerificationClaimView, VerificationTierName } from "@zoiko/api-client";

/** Wording for the verification tier card (ZID-EDR-006). The server decides the tier and what the next one needs; this only says it plainly. */

export const CHECK_COPY: Record<string, { label: string; help: string }> = {
  EMAIL: { label: "Confirm your email address", help: "Proves you control the inbox. Nothing else is asked at sign-up." },
  GOVERNMENT_ID: { label: "A photo ID", help: "A government-issued document, checked by our verification provider." },
  SELFIE_LIVENESS: { label: "A live selfie", help: "Shows the document is yours. You can use an approved alternative if you cannot." },
  COMPANY_CHECK: { label: "A company check", help: "A separate check of the organization, not of you." },
  MANDATE_EVIDENCE: { label: "Evidence of your authority", help: "That you may act for the organization." },
  STRONG_AUTHENTICATION: { label: "A passkey", help: "A phishing-resistant sign-in." },
  FRESH_STRONG_SIGN_IN: { label: "A fresh passkey sign-in", help: "Confirmed just now, for this action." },
  INDEPENDENT_APPROVAL: { label: "An independent approval", help: "A second person who is not you approves the action." },
};

export const checkCopy = (check: string) => CHECK_COPY[check] ?? { label: check.replace(/_/g, " ").toLowerCase(), help: "" };

/** What a person can say they cannot do. Choosing one shows the approved alternatives; nobody else is steered away from the remote check. */
export const CANNOT_OPTIONS = [
  { id: "NO_CAMERA", label: "I can't use a camera" },
  { id: "NO_PHONE", label: "I don't have a smartphone" },
  { id: "NO_STANDARD_ID", label: "I don't have a standard photo ID" },
] as const;

export const ALTERNATIVE_COPY: Record<string, { label: string; help: string }> = {
  ASSISTED_PROOFING: { label: "Assisted proofing", help: "A trained person checks your document with you in a scheduled session." },
  EQUIVALENT_CREDENTIAL: { label: "A government eID or verifiable credential", help: "Accepted when its issuer, validity, revocation and binding have been checked." },
};

export const TIER_TONE: Record<VerificationTierName, "neutral" | "info" | "ok"> = { NONE: "neutral", R0: "neutral", R1: "ok", R2: "ok", R3: "ok" };

/** Why a claim is, or is no longer, in force, in words. Distinct states are never merged. */
export function revocationText(c: VerificationClaimView): string {
  switch (c.revocation.state) {
    case "REVOKED":
      return c.revocation.revoked_at ? `Withdrawn on ${c.revocation.revoked_at.slice(0, 10)}` : "Withdrawn";
    case "EXPIRED":
      return "Expired";
    case "VALID":
      return "In force; can be withdrawn at any time";
    default:
      return c.revocation.state.toLowerCase();
  }
}

export const claimLabel = (claim: string) => claim.replace(/_/g, " ");

/** Toggle one item in the list of things the person cannot do. */
export function toggle(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}
