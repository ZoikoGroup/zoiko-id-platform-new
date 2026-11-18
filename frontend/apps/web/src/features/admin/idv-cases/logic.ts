import type { IdvDecision } from "@zoiko/api-client";

export type Tone = "neutral" | "info" | "ok" | "warn" | "danger";

export const IDV_KEY = ["idv-cases"] as const;

/** What each decision means, in plain words. None of them is a statement about an assurance level. */
export const DECISION: Record<IdvDecision, { tone: Tone; meaning: string }> = {
  APPROVED: { tone: "ok", meaning: "Every check the market requires supported approval. Only the claims listed below may be repeated, and no others." },
  REVIEW_REQUIRED: { tone: "warn", meaning: "A person has to look. Something needs judgement; the case has not been refused." },
  REJECTED: { tone: "danger", meaning: "A defined failure was found, such as a broken chip signature, a manipulated document or an expired one the market does not accept." },
  INDETERMINATE: { tone: "info", meaning: "The evidence needed is missing, unsupported or could not be established. That is not a finding against the applicant." },
  ABANDONED: { tone: "neutral", meaning: "The applicant left before a decision." },
};

const REASONS: Record<string, string> = {
  CHIP_TRUST_ROOT_MISSING: "No trusted issuing authority is loaded for this passport's country, so the chip is read but cannot be called verified.",
  CHIP_SIGNER_NOT_TRUSTED: "The chip was signed by someone the platform does not trust for this country.",
  CHIP_REVOCATION_UNKNOWN: "Whether the chip's signing certificate was withdrawn could not be checked.",
  CHIP_INTEGRITY_FAILED: "The chip's signed data does not match what was read from it.",
  CHIP_NOT_AUTHENTICATED: "The chip's signature has not been checked.",
  CHIP_UNSUPPORTED: "The phone cannot read the chip.",
  CHIP_USER_DECLINED: "The applicant chose not to read the chip.",
  CHIP_READ_FAILED: "Reading the chip failed.",
  CHIP_NOT_STARTED: "The chip has not been read yet.",
  WEB_CAPTURE_CANNOT_BE_CHIP_VERIFIED: "A browser cannot read a chip, so this capture cannot support a chip claim.",
  CLONE_SUSPECTED: "The check that the chip is not a copy failed.",
  CLONE_CHECK_NOT_PASSED: "The market needs the not-a-copy check and it did not pass or is not supported.",
  DOCUMENT_MANIPULATION_INDICATED: "A printed-document check found signs of alteration.",
  OPTICAL_REVIEW: "A printed-document check needs a person's eye.",
  TYPED_TRANSCRIPTION_IS_NOT_AUTHENTICATION: "Details typed by a person do not prove the document is genuine.",
  MRZ_CHECK_DIGITS_FAILED: "The machine-readable lines do not add up; the applicant can recapture.",
  PRINTED_DATA_DIFFERS_FROM_CHIP: "What is printed differs from what the chip holds.",
  PRINTED_DATA_NOT_COMPARED_WITH_CHIP: "The printed details were not compared with the chip's.",
  CAPTURE_INJECTION_RISK: "The capture may not have come from a live camera in front of the applicant.",
  CAPTURE_INTEGRITY_NOT_ESTABLISHED: "Where the capture came from could not be established.",
  CAPTURE_MEDIA_REPLAYED: "This exact capture was already used.",
  CAPTURE_NONCE_UNKNOWN: "The capture did not carry the one-time value this case issued.",
  CAPTURE_NONCE_ALREADY_USED: "The one-time value was already used.",
  CAPTURE_NOT_FRESH: "The capture is too old.",
  VIRTUAL_CAMERA_DETECTED: "A virtual camera was detected.",
  DEVICE_ATTESTATION_FAILED: "The device or app failed its own integrity check.",
  DOCUMENT_EXPIRED: "The document has expired and this market does not accept that.",
  DOCUMENT_EXPIRED_POLICY_REVIEW: "The document has expired; this market sends that to a person.",
  DOCUMENT_DATE_NOT_ESTABLISHED: "The document's expiry date could not be established.",
  PRESENTATION_ATTACK_DETECTED: "The liveness check found signs of a presentation attack. A good face match does not cancel it.",
  LIVENESS_INCONCLUSIVE: "The liveness check could not decide.",
  LIVENESS_NOT_RUN: "No liveness check has run.",
  FACE_DOES_NOT_MATCH_REFERENCE: "The live face did not match the reference portrait; a person decides.",
  FACE_MATCH_INCONCLUSIVE: "The face match could not decide.",
  FACE_MATCH_NOT_RUN: "No face match has run.",
  FACE_REFERENCE_NOT_AUTHENTIC: "The portrait compared against is not itself proven genuine.",
  ROUTE_DISABLED: "This route is switched off or its provider entitlement is gone.",
  METHOD_NOT_RELEASED_FOR_MARKET: "The method has not been approved for this market.",
  ROUTE_NOT_ALLOWED_BY_POLICY: "The market's signed policy does not name this route.",
  ALTERNATE_ROUTE_REVIEW_ONLY: "This alternative route can only be reviewed, not approved.",
  ALTERNATE_ROUTE_NEEDS_INDEPENDENT_REVIEWERS: "This alternative route needs two different reviewers to sign.",
  APPLICANT_ABANDONED: "The applicant left.",
};

export function explainReason(code: string): string {
  if (code.startsWith("BIOMETRICS_OFF:")) return `Biometric steps are off for this market: ${code.slice(15).toLowerCase().replace(/_/g, " ")}.`;
  if (code.startsWith("CHIP_")) return REASONS[code] ?? code;
  return REASONS[code] ?? code;
}

const CLAIMS: Record<string, string> = {
  DOCUMENT_OPTICALLY_AUTHENTIC: "The printed document passed its checks",
  CHIP_DATA_READ: "The chip's data was read",
  CHIP_DATA_ISSUER_SIGNED: "The chip's data is signed by a trusted issuing authority",
  CHIP_NOT_CLONED: "The chip is not a copy",
  LIVE_PRESENCE: "A live person was present",
  HOLDER_MATCHES_TRUSTED_CHIP_PORTRAIT: "The person matches the trusted chip portrait",
  HOLDER_MATCHES_DOCUMENT_PORTRAIT: "The person matches the printed portrait",
};

export function claimText(code: string): string {
  return CLAIMS[code] ?? code;
}

export const SIGNAL_LABEL: Record<string, string> = {
  optical: "Printed document",
  nfc_read: "Chip read",
  passive_auth: "Chip signature",
  chip_auth: "Not a copy",
  liveness: "Live presence",
  face_match: "Face match",
};

export function signalWords(value: string): string {
  return value.toLowerCase().replace(/_/g, " ");
}

const PROBLEMS: Record<string, string> = {
  REVIEWER_IS_THE_APPLICANT: "A person cannot review their own case.",
  ALREADY_REVIEWED: "You already signed this case.",
  CASE_CLOSED: "This case reached a final decision.",
  ROUTE_HAS_NO_CHIP: "This case is on an alternative route; it has no chip.",
  POLICY_CHANGED_SINCE_OPENED: "The market's rules changed after this case opened.",
  MARKET_POLICY_NOT_PUBLISHED: "No proofing policy is published for that market.",
};

export function problemText(code: string): string {
  return PROBLEMS[code] ?? code;
}

/** Only an open case on an alternative route that still needs signatures can be reviewed, and never by its applicant (the platform checks both again). */
export function canReview(c: { state: string; route: string; reviewers: number; decision: IdvDecision }): boolean {
  return c.state === "OPEN" && c.route !== "CHIP" && c.decision === "REVIEW_REQUIRED";
}
