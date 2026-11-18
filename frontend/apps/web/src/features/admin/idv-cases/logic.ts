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
  const recapture = /^RECAPTURE_REQUESTED:(.+)$/.exec(code);
  if (recapture) return `A reviewer asked for a new photograph: ${(RECAPTURE_TEXT[recapture[1]!] ?? recapture[1]!).toLowerCase()}.`;
  const rejected = /^REVIEWER_REJECTED:(.+)$/.exec(code);
  if (rejected) return `A reviewer rejected the case: ${(REJECT_TEXT[rejected[1]!] ?? rejected[1]!).toLowerCase()}.`;
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
  REVIEWER_IS_THE_APPLICANT: "A person cannot review their own case or open their own photograph.",
  DATE_ALREADY_ESTABLISHED: "The document's date is already established for this case.",
  SCAN_UNAVAILABLE: "The file could not be checked for malware right now. Try again soon.",
  IMAGE_REFUSED: "That file could not be accepted.",
  MEDIA_RETENTION_NOT_SET: "This market has no retention period for photographs, so none is kept.",
  MEDIA_NOT_FOUND: "There is no photograph on this case now (it may have been replaced).",
  MEDIA_EXPIRED: "The retention period for this photograph has ended.",
  GRANT_INVALID: "That permission has expired, been used up or is not yours. Ask again with a reason.",
  REASON_REQUIRED: "Say why you need to see the photograph (10 to 500 characters).",
  ONLY_ON_REVIEWED_ROUTE: "Only a case that people decide can be rejected or sent back this way.",
  CODE_INVALID: "Choose one of the listed reasons.",
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

/** Why a reviewer may reject, each a defined failure and never a bare accusation. */
export const REJECT_TEXT: Record<string, string> = {
  DOCUMENT_MANIPULATION_SUSPECTED: "The document looks altered",
  DOCUMENT_EXPIRED: "The document has expired",
  NOT_A_SUPPORTED_DOCUMENT: "This is not a document the market accepts",
  DETAILS_INCONSISTENT: "The details on the document do not agree with each other",
};

/** Why a reviewer may ask for a new photograph. Asking is not a finding against the applicant and costs them no attempt. */
export const RECAPTURE_TEXT: Record<string, string> = {
  PHOTO_UNREADABLE: "The photograph cannot be read",
  GLARE_OR_SHADOW: "Glare or a shadow hides part of it",
  PAGE_CUT_OFF: "Part of the page is cut off",
  WRONG_PAGE: "It is not the page with the photo and the two lines at the bottom",
};

export const CHECK_TEXT: Record<string, string> = {
  RESOLUTION: "Resolution",
  SHARPNESS: "Sharpness",
  GLARE: "Glare",
  EXPOSURE: "Exposure",
  ELA_ANOMALY: "Re-compression screen",
  MRZ_READABLE: "The two lines at the bottom can be read",
  MRZ_CHECK_DIGITS: "The check digits on those lines add up",
};

/** A measured check in words. A good score is about the capture, not about whether the document is genuine. */
export function checkWords(name: string, result: string): { label: string; tone: Tone; text: string } {
  const label = CHECK_TEXT[name] ?? name.toLowerCase().replace(/_/g, " ");
  if (result === "PASS") return { label, tone: "ok", text: "fine" };
  if (result === "FAIL") return { label, tone: "danger", text: name === "ELA_ANOMALY" ? "residue worth a look" : "not good enough" };
  return { label, tone: "info", text: "could not be judged" };
}

/** Why the reviewer cannot ask to open the photograph yet, or null. Every look is logged with this reason. */
export function reasonProblem(reason: string): string | null {
  const n = reason.trim().length;
  if (n < 10) return "Say why you need to see it (at least 10 characters).";
  if (n > 500) return "Keep the reason under 500 characters.";
  return null;
}

/** The sentence about how long the photograph is kept, from what the platform says. */
export function keptLine(photo: { has_photo: boolean; expired?: boolean; expires_at?: string }): string {
  if (!photo.has_photo) return "There is no photograph on this case.";
  if (photo.expired) return "The retention period for this photograph has ended.";
  return photo.expires_at ? `Kept encrypted until ${new Date(photo.expires_at).toLocaleDateString()}, then deleted.` : "Kept encrypted.";
}

export const OPEN_NOTE = "Opening it is logged with your reason. It stays open for five minutes and can be shown three times. It is never in a list or a log.";

/** What an approved case counts as downstream. The grade travels with the claim so a consumer can decide what to accept. */
export const GRADE_TEXT: Record<string, string> = {
  CHIP_VERIFIED: "Verified with the passport chip",
  REVIEWED_NO_CHIP: "Reviewed by people, no chip read",
};

export function claimLine(c: { grade: string; valid_until: string; status: string } | null): string {
  if (c === null) return "This case left no identity claim, so nothing else on the platform will rely on it.";
  const when = new Date(c.valid_until).toLocaleDateString();
  if (c.status === "REVOKED") return "The identity claim this case left has been withdrawn.";
  return `Counts as an identity claim until ${when}: ${(GRADE_TEXT[c.grade] ?? c.grade).toLowerCase()}.`;
}

/** How long a case has waited, and whether the market's deadline has passed. */
export function waitingLine(i: { waiting_hours: number; due_at: string | null; overdue: boolean }): string {
  const waited = i.waiting_hours < 1 ? "just arrived" : i.waiting_hours < 48 ? `waiting ${i.waiting_hours} hours` : `waiting ${Math.floor(i.waiting_hours / 24)} days`;
  if (!i.due_at) return waited;
  return i.overdue ? `${waited}, overdue (was due ${new Date(i.due_at).toLocaleString()})` : `${waited}, due ${new Date(i.due_at).toLocaleString()}`;
}

export const ATTEST_NOTE = "The platform could not read this document's date. If you attest to it, that is logged as your act and recorded as an attestation, not as the platform's reading. Once a date is established, nobody changes it.";
