import type { ProofingCheckResult, ProofingCheckType, ProofingDecision, ProofingFacts } from "@zoiko/api-client";

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

/** The headline in the applicant's words (WF01 screen A5). Only `verified_for_scope` ever reads as verified, and it always says for what. */
export function headline(facts: Pick<ProofingFacts, "decision" | "scope" | "indeterminate">): { title: string; tone: Exclude<Tone, "neutral">; body: string } {
  const scope = facts.scope.replace(/_/g, " ");
  switch (facts.decision as ProofingDecision) {
    case "verified_for_scope":
      return { title: `Verified for ${scope}`, tone: "ok", body: "This covers this purpose only. It is not a general proof of who you are." };
    case "review_required":
      return { title: "Under review", tone: "info", body: "A person is looking at your check. You do not need to do anything yet." };
    case "more_evidence":
      return { title: "More evidence required", tone: "warn", body: "We could not use what was sent. You can send it again." };
    case "indeterminate":
      return { title: "We could not complete this check", tone: "warn", body: "Something needed for the check was not available. This is not a decision about you. You can try another way." };
    case "rejected":
      return { title: "Not verified", tone: "danger", body: "The check did not succeed. You can ask for a correction or a review." };
    default:
      return { title: "In progress", tone: "info", body: "Your check has not finished." };
  }
}

export const CHECK_LABEL: Record<ProofingCheckType, string> = {
  document_validity: "Document",
  liveness_pad: "Live presence",
  face_match: "Face match",
  chip_crypto: "Chip in the document",
};

export const RESULT_LABEL: Record<ProofingCheckResult, { label: string; tone: Tone }> = {
  pass: { label: "Passed", tone: "ok" },
  fail: { label: "Did not pass", tone: "danger" },
  indeterminate: { label: "Could not be judged", tone: "warn" },
  not_requested: { label: "Not part of this check", tone: "neutral" },
};

/** What one check's result means, in words that also say what it does NOT mean. An indeterminate check is never described as a failure of the person. */
export function checkMeaning(type: ProofingCheckType, result: ProofingCheckResult): string {
  if (result === "not_requested") return "Your organization did not ask for this check.";
  if (type === "chip_crypto") {
    if (result === "pass") return "The chip's signature was checked against a country list this platform trusts. That shows the document is genuine, not that you are its holder.";
    if (result === "indeterminate") return "The issuing country's signing list is not trusted here yet, so the chip could not be checked. This is not a failure.";
    return "The chip's signature did not check out.";
  }
  if (type === "face_match") {
    if (result === "pass") return "Your live face matched the photo read from your document's chip.";
    if (result === "indeterminate") return "Nothing yet ties you to the document: a face match is not available here, so this check could not be made.";
    return "Your face did not match the photo.";
  }
  if (type === "liveness_pad") {
    if (result === "pass") return "The live-presence check passed.";
    if (result === "indeterminate") return "A live-presence check was needed but could not be made.";
    return "The live-presence check did not pass, so a person will look at it.";
  }
  if (result === "pass") return "The document details were validated. That does not show you own it.";
  if (result === "indeterminate") return "The document has been received but has not been fully checked.";
  return "The document could not be validated.";
}

/** The assertions that hold, in plain words. Nothing here is a claim the platform did not derive. */
export const ASSERTION_LABEL: Record<string, string> = {
  DOCUMENT_COLLECTED: "A document was received",
  DOCUMENT_REVIEWED: "A person reviewed it",
  DOCUMENT_VALIDATED: "The document details were validated",
  DOCUMENT_CHIP_CRYPTO_VALID: "The chip's signature was checked",
  PRESENCE_CONFIRMED: "A live person was present",
  FACE_MATCH_CONFIRMED: "Your face matched the document photo",
  IDENTITY_VERIFIED_FOR_SCOPE: "Identity verified for this purpose",
};

export function assertionText(code: string): string {
  return ASSERTION_LABEL[code] ?? code;
}
