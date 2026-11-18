import type { KybGrade } from "@zoiko/api-client";

export const KYB_KEY = ["company-verification"];

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

/** What each grade says about the company. A grade describes what is proven; it is never a privilege, and no grade is shown as "verified company". */
export const GRADE: Record<KybGrade, { tone: Tone; meaning: string }> = {
  K0: { tone: "neutral", meaning: "Nothing independent is proven about the company yet. What was declared or uploaded is not proof." },
  K1: { tone: "info", meaning: "Two independent reviewers looked at current documents. No official register has confirmed the company." },
  K2: { tone: "info", meaning: "An official register matched the company and reports it active. Ownership, people and authority are separate checks." },
  K3: { tone: "ok", meaning: "The company is confirmed, its ownership is settled, the people are verified and screening is clear." },
  K4: { tone: "ok", meaning: "Everything above, and a verified person holds a mandate in force. Only the acts the mandate names are allowed." },
};

const REASONS: Record<string, { text: string; next: string }> = {
  NO_ASSESSMENT: { text: "The company has not been assessed yet.", next: "Start a company verification, then assess again." },
  NO_REGISTRY_CHECK: { text: "No official register has been checked.", next: "Check the available official records, or continue with documents." },
  REGISTRY_NO_EXACT_MATCH: { text: "The register did not match the registration number exactly.", next: "Check the number. No match does not mean the company does not exist: you can continue with documents." },
  REGISTRY_STATUS_NOT_ESTABLISHED: { text: "The register could not tell us whether the company is active.", next: "We will try again. A register that is down is never read as a problem with your company." },
  REGISTRY_CHECK_STALE: { text: "The last register check is too old to count as current.", next: "Ask for a new check." },
  NO_DOCUMENT_REVIEW: { text: "No documents have been reviewed.", next: "Upload the documents your market asks for." },
  DOCUMENTS_NOT_CURRENT: { text: "The documents are out of date for what they must prove.", next: "Upload a more recent extract." },
  TWO_INDEPENDENT_REVIEWERS_REQUIRED: { text: "Two independent reviewers must agree before documents count.", next: "A second reviewer will look at the documents." },
  OWNERSHIP_INCOMPLETE: { text: "Who owns and controls the company is not fully settled.", next: "Complete the ownership structure; every owner needs a decision." },
  PERSONS_NOT_VERIFIED: { text: "An owner or controller has no current identity check.", next: "Each person completes an identity check, and the decision points at it." },
  SCREENING_POTENTIAL_MATCH: { text: "Screening needs an independent review.", next: "Nothing is needed from you now; a reviewer will decide." },
  SCREENING_UNAVAILABLE: { text: "Screening could not be completed.", next: "We will try again." },
  SCREENING_NOT_RUN: { text: "Screening has not been run.", next: "It starts once the other checks are in." },
  NO_MANDATE_IN_FORCE: { text: "No verified person holds a mandate to act for the company.", next: "Record a mandate for a person whose identity is verified." },
  COMPANY_DISSOLVED_SOURCE_REPORTED: { text: "The register reports the company dissolved.", next: "Authority linked to it has stopped. Contact support if this is wrong." },
};

/** A reason code in words the applicant can act on. An unknown code shows as itself. */
export function explainReason(code: string): { text: string; next: string } {
  return REASONS[code] ?? { text: code, next: "" };
}

/** The distinct next steps, in the order the reasons came. */
export function nextSteps(reasons: string[]): string[] {
  return [...new Set(reasons.map((r) => explainReason(r).next).filter(Boolean))];
}

export const ACTION_LABEL: Record<string, string> = {
  ORG_PROFILE_DRAFT: "Draft the organization profile",
  ORG_PROFILE_MAINTENANCE: "Maintain the organization profile",
  ORG_ROUTINE_FUNCTIONS: "Routine organization functions",
  RISK_APPROVED_COMMERCIAL_WORKFLOWS: "Risk-approved commercial workflows",
  BIND_ENTITY: "Bind the company",
  SIGN_AGREEMENT: "Sign an agreement",
  AUTHORIZE_PAYMENT: "Authorize a payment",
  TRANSFER_ACCOUNT_OWNERSHIP: "Transfer account ownership",
  DELEGATE_AUTHORITY: "Delegate authority",
};

export const BINDING = ["BIND_ENTITY", "SIGN_AGREEMENT", "AUTHORIZE_PAYMENT", "TRANSFER_ACCOUNT_OWNERSHIP", "DELEGATE_AUTHORITY"] as const;
export const ALWAYS_SECOND = new Set(["AUTHORIZE_PAYMENT", "TRANSFER_ACCOUNT_OWNERSHIP", "DELEGATE_AUTHORITY"]);
export const ROLES = ["DIRECTOR", "SECRETARY", "EMPLOYEE", "AGENT", "TRUSTEE", "PARTNER"] as const;
export const SOURCES = [
  ["BOARD_MINUTE", "Board minute"],
  ["POWER_OF_ATTORNEY", "Power of attorney"],
  ["REGISTRY_OFFICER", "Officer entry in the register"],
  ["AUTHORIZED_MANDATE", "Authorized mandate"],
  ["OTHER", "Other"],
] as const;

export type MandateForm = { person: string; role: string; source: string; powers: string[]; evidence: string; from: string; to: string };

/** True when the chosen acts always need a second signatory, whatever the person recording it ticks. */
export function needsSecondSignatory(powers: string[]): boolean {
  return powers.some((p) => ALWAYS_SECOND.has(p));
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The checks the platform makes, shown before it has to refuse. The platform checks again: this only saves a round trip. */
export function mandateProblem(f: MandateForm, me: string | undefined): string | null {
  if (!UUID.test(f.person.trim())) return "Enter the person's account identifier.";
  if (me && f.person.trim().toLowerCase() === me.toLowerCase()) return "You cannot record your own mandate. Someone else records it.";
  if (f.powers.length === 0) return "Choose at least one act the mandate covers.";
  if (!f.evidence.trim()) return "Name the document the mandate rests on.";
  if (!f.from) return "Say when the mandate starts.";
  if (f.to && new Date(f.to).getTime() <= new Date(f.from).getTime()) return "The mandate must end after it starts.";
  return null;
}

export function appealProblem(reason: string): string | null {
  return reason.trim().length >= 20 ? null : "Say in at least 20 characters what is wrong or what is new.";
}

export function exportProblem(purpose: string): string | null {
  const n = purpose.trim().length;
  return n >= 3 && n <= 200 ? null : "Say what the export is for (3 to 200 characters).";
}

export const PROBLEMS: Record<string, string> = {
  MANDATE_SELF_RECORDED: "You cannot record your own mandate. Someone else records it.",
  MANDATE_EVIDENCE_REQUIRED: "Name the document the mandate rests on.",
  MANDATE_POWERS_INVALID: "One of those acts cannot be given by a mandate.",
  MANDATE_WINDOW_INVALID: "The mandate must end after it starts.",
  MANDATE_TERM_TOO_LONG: "That is too long. Record a shorter mandate and renew it.",
  MANDATE_ALREADY_ENDED: "That mandate has already ended.",
  MANDATE_REVOKED: "That mandate has been revoked.",
  COSIGNER_IS_REPRESENTATIVE: "The representative cannot be their own second signatory.",
  COSIGNER_IS_RECORDER: "The person who recorded the mandate cannot be its second signatory.",
  REASON_REQUIRED: "Give a reason of at least a few words.",
  APPEAL_WINDOW_CLOSED: "The time to appeal this decision has passed. Add evidence and apply again.",
  APPEAL_ALREADY_OPEN: "An appeal against this decision is already open.",
  APPEAL_ALREADY_DECIDED: "That appeal has already been decided.",
  DECIDER_MADE_THE_DECISION: "You took part in this decision, so someone else decides the appeal.",
  DECIDER_IS_A_PARTY: "You are a party to this appeal, so someone else decides it.",
  NOTHING_TO_APPEAL: "The company already holds the highest grade.",
  PURPOSE_REQUIRED: "Say what the export is for.",
  KYB_GRADE_INSUFFICIENT: "The company's verification does not cover this act yet.",
};

export function problemText(code: string): string {
  return PROBLEMS[code.toUpperCase()] ?? code;
}

export function exportFileName(summary: { export_id?: unknown }): string {
  const id = typeof summary.export_id === "string" ? summary.export_id.replace(/[^A-Za-z0-9-]/g, "").slice(0, 36) : "export";
  return `company-verification-${id || "export"}.json`;
}
