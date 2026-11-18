import type { ScreeningAction, ScreeningCaseState, ScreeningDisposition } from "@zoiko/api-client";

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

export const CASE_STATE: Record<ScreeningCaseState, { tone: Tone; text: string }> = {
  NO_CANDIDATE: { tone: "ok", text: "No candidate in the checked sources" },
  REVIEW_REQUIRED: { tone: "warn", text: "Needs a reviewer" },
  SOURCE_UNAVAILABLE: { tone: "danger", text: "A required source was not usable" },
  CLOSED_CLEAR_FOR_SCOPE: { tone: "ok", text: "Closed: clear for this purpose" },
  CLOSED_RESTRICTED: { tone: "danger", text: "Closed: restricted" },
  CLOSED_UNRESOLVED: { tone: "neutral", text: "Closed: unresolved" },
};

export const DISPOSITION: Record<ScreeningDisposition, string> = {
  CLEARED_FOR_PURPOSE: "Cleared for this purpose",
  EDD_REQUIRED: "Enhanced due diligence required",
  HOLD_PENDING_REVIEW: "On hold pending review",
  RESTRICTED: "Restricted",
  PROHIBITED: "Prohibited (Legal)",
  UNRESOLVED: "Unresolved",
};

/** The acts that fit each disposition, as the platform allows them. The platform checks again. */
export const ACTIONS_FOR: Record<ScreeningDisposition, ScreeningAction[]> = {
  CLEARED_FOR_PURPOSE: ["RELEASE"],
  EDD_REQUIRED: ["EDD", "HOLD"],
  HOLD_PENDING_REVIEW: ["HOLD"],
  RESTRICTED: ["HOLD", "REJECT"],
  PROHIBITED: ["REJECT", "FREEZE", "BLOCK", "REPORT"],
  UNRESOLVED: ["HOLD"],
};

export const LEGAL_ONLY_ACTIONS: ScreeningAction[] = ["FREEZE", "BLOCK", "REPORT"];
const NEEDS_CHECKER: ScreeningDisposition[] = ["CLEARED_FOR_PURPOSE", "RESTRICTED", "PROHIBITED"];
const NEEDS_EVIDENCE: ScreeningDisposition[] = ["CLEARED_FOR_PURPOSE", "RESTRICTED", "PROHIBITED"];
const NEEDS_REVIEW_DATE: ScreeningDisposition[] = ["RESTRICTED", "PROHIBITED", "UNRESOLVED"];

export function needsChecker(d: ScreeningDisposition, a: ScreeningAction): boolean {
  return NEEDS_CHECKER.includes(d) || LEGAL_ONLY_ACTIONS.includes(a);
}

export type ProposeForm = { disposition: ScreeningDisposition; action: ScreeningAction; rationale: string; reasons: string; evidence: string; citation: string; reviewBy: string };

export const EMPTY_FORM: ProposeForm = { disposition: "HOLD_PENDING_REVIEW", action: "HOLD", rationale: "", reasons: "", evidence: "", citation: "", reviewBy: "" };

/** Split free text into upper-case codes, as the platform wants them. */
export function codes(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((c) => c.trim().toUpperCase())
    .filter(Boolean);
}

/** What the platform will refuse, said before it has to. The platform checks again. */
export function proposeProblem(f: ProposeForm): string | null {
  if (!ACTIONS_FOR[f.disposition].includes(f.action)) return "That act does not fit that decision.";
  if (f.rationale.trim().length < 20) return "Write the reason in at least 20 characters.";
  const reasons = codes(f.reasons);
  if (reasons.length === 0) return "Give at least one reason code, for example CONFLICTING_DOB.";
  if (reasons.some((c) => !/^[A-Z][A-Z0-9_]{0,63}$/.test(c))) return "Reason codes use capital letters, digits and underscores.";
  if (NEEDS_EVIDENCE.includes(f.disposition) && f.evidence.split(/[\n,]+/).filter((e) => e.trim()).length === 0) return "Cite the evidence that supports this decision.";
  if ((f.disposition === "PROHIBITED" || LEGAL_ONLY_ACTIONS.includes(f.action)) && !f.citation.trim()) return "A prohibition or a legal act needs its legal citation.";
  if (NEEDS_REVIEW_DATE.includes(f.disposition) && !f.reviewBy) return "Say when this decision is looked at again.";
  return null;
}

const CODE_TEXT: Record<string, string> = {
  POSSIBLE_MATCH_AWAITS_REVIEWER: "A candidate was found and a reviewer must decide.",
  UNSCREENED_REQUIRED_CLASSES: "A class the policy requires was not screened.",
  REQUIRED_SOURCE_STALE: "A required source is older than the policy allows.",
  REQUIRED_SOURCE_UNAVAILABLE: "A required source could not be used.",
  INSUFFICIENT_IDENTIFIERS: "There are too few identifiers to tell whether the candidate is the subject.",
  CONFIRMED_MATCH: "A reviewer confirmed the match.",
  CONFIRMED_MATCH_LEGALLY_SIGNED: "Legal signed the determination.",
  NO_CANDIDATE_IN_CHECKED_SOURCES: "No candidate in the sources that were checked.",
  PEP_OR_RCA_CANDIDATE: "A licensed source named a possible politically exposed person or associate.",
  ADVERSE_MEDIA_RELEVANCE_REVIEW: "A licensed source returned articles to judge for relevance.",
  PEP_CURRENT: "The provider says the office is held now.",
  PEP_FORMER: "The provider gives an end date for the office: a former holder. Being former does not by itself lower the risk.",
  PEP_UNKNOWN: "The provider's dates or relationship do not say whether this is a current or former holder.",
  PEP_NOTE_FORMER_RISK_NOT_REDUCED: "No jurisdiction lookback is set, so being former changes nothing.",
  PEP_NOTE_FORMER_OUTSIDE_LOOKBACK_REVIEW: "The office ended before the jurisdiction's lookback: a person still decides.",
  PEP_NOTE_FORMER_WITHIN_LOOKBACK: "The office ended inside the jurisdiction's lookback.",
  PEP_NOTE_RELATIONSHIP_MISSING: "A relative or associate with no named politically exposed person or relationship: it cannot be judged yet.",
  PEP_NOTE_DATES_MISSING: "No end date and not stated as current: treated as unknown, never as former.",
  ARTICLE_LINKED: "At least two independent details (birth, country, identifier) agree with the subject, and none contradicts.",
  ARTICLE_NAME_ONLY: "The article shares only a name with the subject: not linked, and never a strong match.",
  ARTICLE_CONTRADICTED: "A detail in the article contradicts the subject (for example birth or country): not linked.",
  ARTICLE_RETRACTED: "The provider retracted an article this case relied on.",
  ARTICLE_CORRECTED: "The provider corrected an article this case relied on.",
};

export const EDD_ITEMS: Record<string, string> = {
  SOURCE_OF_FUNDS: "Source of funds",
  SOURCE_OF_WEALTH: "Source of wealth",
  OWNERSHIP_EXPLANATION: "Explanation of ownership or control",
  IDENTITY_CLARIFICATION: "Clarification of identity details",
  PURPOSE_OF_RELATIONSHIP: "Purpose of the relationship",
  SUPPORTING_DOCUMENT: "A supporting document",
};

/** Why a request for information cannot be sent yet, or null. The customer is told what is wanted, never why. */
export function askProblem(items: string[], days: number): string | null {
  if (items.length === 0) return "Choose at least one item to ask for.";
  if (items.some((i) => !(i in EDD_ITEMS))) return "Choose items from the list.";
  if (!Number.isInteger(days) || days < 3 || days > 60) return "Give the customer between 3 and 60 days.";
  return null;
}

export const EDD_STATE: Record<string, string> = {
  REQUESTED: "Waiting for the customer",
  RECEIVED: "Answered: needs review by someone other than the person who asked",
  ACCEPTED: "Accepted",
  INSUFFICIENT: "Not enough: it can be asked again",
  EXPIRED: "Expired: the case is unchanged and a person decides what the silence means",
};

export const INSUFFICIENT_CODES = ["INCOMPLETE", "UNREADABLE", "INCONSISTENT", "NOT_SUPPORTED_BY_DOCUMENTS"] as const;

export function reasonText(code: string): string {
  const not = /^NOT_SCREENED:(.+)$/.exec(code);
  if (not) return `Not screened: ${not[1]!.toLowerCase().replaceAll("_", " ")}.`;
  return CODE_TEXT[code] ?? code;
}

export function errorText(code: string): string {
  const known: Record<string, string> = {
    REVISION_CONFLICT: "The case changed since you opened it. Open it again.",
    CHECKER_IS_THE_MAKER: "You made this proposal, so someone else finalises it.",
    LEGAL_ROLE_REQUIRED: "A prohibition and the freeze, block and report acts need Legal.",
    CHECKER_NEEDS_SENIOR_ROLE: "A second person with a senior role finalises this.",
    CHECKER_DECIDED_BEFORE: "You decided this case before, so you cannot check this decision.",
    PROPOSAL_NOT_PENDING: "That proposal has already been finalised or withdrawn.",
    ONLY_THE_MAKER_WITHDRAWS: "Only the person who proposed it can withdraw it.",
    REQUEST_ALREADY_OPEN: "This case already has an open request for information.",
    REVIEWER_ASKED_FOR_IT: "You asked for this information, so someone else reviews the answer.",
    NOTHING_TO_REVIEW: "The customer has not answered yet.",
    REVIEW_CODE_REQUIRED: "Say why it is not enough.",
    REQUEST_CLOSED: "That request is already closed.",
    REQUEST_EXPIRED: "That request has expired.",
  };
  return known[code] ?? code;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
