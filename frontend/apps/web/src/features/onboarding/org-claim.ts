import type {
  OrgClaimCheckState,
  OrgClaimHolding,
  OrgClaimAppealKind,
  OrgClaimOutcome,
  OrgClaimParty,
  OrgClaimProofKind,
  OrgClaimScreening,
  OrgClaimStatus,
} from "@zoiko/api-client";

/** The four independent checks of WF-13B-03, in the order they are shown. A found company is not a verified person,
 * a verified person is not an authorized one, and none of it is a cleared risk review. */
export const CHECKS: { key: keyof OrgClaimStatus["checks"]; label: string; help: string }[] = [
  { key: "company", label: "Company and legal status", help: "The company exists and is active in an official source." },
  { key: "person_assurance", label: "Your identity", help: "Your identity is verified in its own step; signing up or a matching email is not proof." },
  { key: "authority", label: "Your authority to act for it", help: "You are a current officer of the company, or hold a valid delegation." },
  { key: "risk", label: "Risk and ownership review", help: "Directors, owners and sanctions checks the country requires." },
];

export const CHECK_TONE: Record<OrgClaimCheckState, "ok" | "info" | "warn" | "danger"> = {
  COMPLETED: "ok",
  PENDING: "info",
  NEEDS_ACTION: "warn",
  HELD: "warn",
  DECLINED: "danger",
};

export const CHECK_WORDS: Record<OrgClaimCheckState, string> = {
  COMPLETED: "Completed",
  PENDING: "Pending",
  NEEDS_ACTION: "Needs action",
  HELD: "In review",
  DECLINED: "Declined",
};

/** The one outcome, in plain words and with what to do next. Never promises a time, and never calls a register match "verified". */
export function outcomeCopy(outcome: OrgClaimOutcome): { title: string; body: string; tone: "ok" | "info" | "warn" | "danger" } {
  switch (outcome) {
    case "VERIFIED":
      return { title: "Verified", body: "Every check is complete. Organization roles and credentials that need a verified company are available.", tone: "ok" };
    case "IN_REVIEW":
      return { title: "In review", body: "A person at Zoiko iD is reviewing part of this claim. Nothing is needed from you unless we contact you.", tone: "warn" };
    case "NEEDS_EVIDENCE":
      return { title: "More information needed", body: "Add the details marked Needs action, then check again.", tone: "warn" };
    case "DECLINED":
      return { title: "Declined", body: "This claim could not be verified. You can ask for a review or start a new claim.", tone: "danger" };
    case "STALE":
      return { title: "Out of date", body: "The evidence has expired and must be refreshed.", tone: "warn" };
    case "RESTRICTED":
      return { title: "Restricted", body: "Verified privileges are paused while a review is open.", tone: "warn" };
    default:
      return {
        title: "Provisional",
        body: "The company is recorded as a claim only. It is not a verified organization, and no owner or approver rights follow from it.",
        tone: "info",
      };
  }
}

/** A person's name as the applicant typed it, with the whitespace tidied; nothing else is changed. */
export const tidy = (value: string): string => value.trim().replace(/\s+/g, " ");

/** "25", "12.5" or "100"; anything else is rejected before it is sent, and the server checks again. */
export function pctValid(value: string): boolean {
  const v = value.trim();
  if (!/^\d{1,3}(\.\d{1,4})?$/.test(v)) return false;
  const n = Number(v);
  return n >= 0 && n <= 100;
}

export type OwnerRow = { name: string; birthYear: string; sharesPct: string; votesPct: string; boardControl: boolean };

export function emptyOwner(): OwnerRow {
  return { name: "", birthYear: "", sharesPct: "", votesPct: "", boardControl: false };
}

export function ownerRowErrors(r: OwnerRow): string[] {
  const out: string[] = [];
  if (!tidy(r.name)) out.push("Enter the person's full legal name.");
  if (r.birthYear && !/^(19|20)\d\d$/.test(r.birthYear.trim())) out.push("Enter a four-digit birth year.");
  if (r.sharesPct && !pctValid(r.sharesPct)) out.push("Shares must be between 0 and 100.");
  if (r.votesPct && !pctValid(r.votesPct)) out.push("Votes must be between 0 and 100.");
  if (!r.sharesPct && !r.votesPct && !r.boardControl) out.push("State the shares, the votes or the board control.");
  return out;
}

/** The screening request. Each person gets an id of this form's own; holdings are what the applicant declares, and the server
 * treats them as unverified until a source confirms them, so a declaration never clears anyone. */
export function buildScreening(representativeName: string, owners: OwnerRow[]): OrgClaimScreening {
  const party = (ref: string, name: string, birthYear?: string): OrgClaimParty => ({
    ref,
    kind: "PERSON",
    name: tidy(name),
    birth: birthYear?.trim() || null,
  });
  const people = owners.map((o, i) => party(`owner${i + 1}`, o.name, o.birthYear));
  const holdings: OrgClaimHolding[] = owners.map((o, i) => ({
    owner: `owner${i + 1}`,
    shares_pct: o.sharesPct.trim() || null,
    votes_pct: o.votesPct.trim() || null,
    controls: o.boardControl ? ["APPOINT_BOARD_MAJORITY"] : [],
  }));
  return { representative: party("rep", representativeName), owners: people, holdings };
}

/** One id per opening of the search page: a double click is one claim, and coming back later is a new attempt. */
export function attemptId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36);
}

/** A fresh idempotency key per intention, so a double click is one claim and a second company is a second claim. */
export function claimKey(country: string, registrationNumber: string, attempt: string): string {
  return `claim-${country}-${registrationNumber}-${attempt}`.slice(0, 120);
}

/** What each kind of document is called, in the order a person is likely to need them. */
export const PROOF_KINDS: { id: OrgClaimProofKind; label: string }[] = [
  { id: "CERTIFICATE_OF_INCORPORATION", label: "Certificate of incorporation" },
  { id: "REGISTER_EXTRACT", label: "Register extract" },
  { id: "LETTER_OF_AUTHORITY", label: "Letter of authority" },
  { id: "OWNERSHIP_CHART", label: "Ownership chart" },
  { id: "BOARD_RESOLUTION", label: "Board resolution" },
  { id: "OTHER", label: "Another document" },
];
export const PROOF_ACCEPT = "application/pdf,image/png,image/jpeg";
export const MAX_PROOF_BYTES = 10 * 1024 * 1024;

/** A problem the browser can see before sending, in plain words; the server checks again whatever this says. */
export function proofProblem(file: { name: string; size: number; type: string }): string | null {
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_PROOF_BYTES) return "That file is larger than 10 MB.";
  if (file.type && !PROOF_ACCEPT.split(",").includes(file.type)) return "Upload a PDF, PNG or JPEG file.";
  return null;
}

/** The server's refusals in the product's words. An unknown code gets a neutral message, never the raw code. */
const PROOF_MESSAGES: Record<string, string> = {
  PROOF_TOO_LARGE: "That file is larger than 10 MB.",
  PROOF_EMPTY: "That file is empty.",
  PROOF_TYPE_NOT_ALLOWED: "Upload a PDF, PNG or JPEG file.",
  PROOF_TYPE_MISMATCH: "That file is not the type it says it is.",
  PROOF_ACTIVE_CONTENT: "That PDF contains scripts or embedded content and cannot be accepted.",
  PROOF_MALFORMED: "That PDF is incomplete or damaged.",
  PROOF_INFECTED: "That file was refused by the security scan.",
  PROOF_SCAN_UNAVAILABLE: "Uploads are paused for a moment. Nothing was stored; try again shortly.",
  PROOF_RESIDENCY_DENIED: "This organization's data region does not allow the document to be stored here.",
  PROOF_LIMIT_REACHED: "This claim already holds the most documents it can.",
};

export function proofMessage(code: string | undefined): string {
  return (code && PROOF_MESSAGES[code]) || "That document could not be added.";
}

/** One idempotency key per file choice, so a double click is one upload and a different file is another. */
export function proofKey(claimId: string, file: { name: string; size: number }, attempt: string): string {
  return `proof-${claimId.slice(0, 8)}-${file.size}-${attempt}`.slice(0, 120);
}

export function sizeLabel(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const MIN_STATEMENT = 20;
export const MAX_STATEMENT = 2000;

/** Which request a person can make about a screening, from its state: an appeal after a rejection, a correction while a case is
 * waiting, in review or rejected. Nothing is offered once the checks are complete or while they are unavailable. */
export function appealKinds(screeningStatus: string | undefined): OrgClaimAppealKind[] {
  switch (screeningStatus) {
    case "REJECTED":
      return ["APPEAL", "CORRECTION"];
    case "GATHERING":
    case "REVIEW_REQUIRED":
      return ["CORRECTION"];
    default:
      return [];
  }
}

export const APPEAL_LABELS: Record<OrgClaimAppealKind, { label: string; help: string }> = {
  APPEAL: { label: "Appeal the decision", help: "Ask a different person to look again, and say why the decision should change." },
  CORRECTION: { label: "Ask for a correction", help: "Tell us what is wrong in what we hold or in what the register says." },
};

/** A problem the browser can see before sending; the server checks again. */
export function statementProblem(statement: string): string | null {
  const n = statement.trim().length;
  if (n < MIN_STATEMENT) return `Write at least a sentence (${MIN_STATEMENT} characters).`;
  if (n > MAX_STATEMENT) return `Keep it under ${MAX_STATEMENT} characters.`;
  return null;
}

const APPEAL_MESSAGES: Record<string, string> = {
  STATEMENT_INVALID: `Write at least a sentence (${MIN_STATEMENT} characters), and no more than ${MAX_STATEMENT}.`,
  APPEAL_NOT_AVAILABLE: "That request is not available for this check right now.",
  APPEAL_ALREADY_OPEN: "A request is already open. A person is looking at it.",
  AMEND_NOT_AVAILABLE: "These details can only be changed while we are waiting for them.",
  SCREENING_RESIDENCY_DENIED: "This organization's data region does not allow these details to be stored here. Nothing was stored.",
};

export function appealMessage(code: string | undefined): string {
  return (code && APPEAL_MESSAGES[code]) || "That request could not be sent.";
}

/** What the next step is, from the checks alone. */
export function nextStep(status: OrgClaimStatus): "authority" | "wait" | "done" {
  if (status.outcome === "VERIFIED") return "done";
  if (status.screening === null && status.checks.company === "COMPLETED") return "authority";
  return "wait";
}
