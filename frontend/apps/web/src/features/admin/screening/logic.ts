import type { ScreeningClass, ScreeningSubjectType } from "@zoiko/api-client";

export const SCREENING_KEY = ["screening"];

export const SUBJECT_TYPES: [ScreeningSubjectType, string][] = [
  ["BUSINESS", "Your company"],
  ["UBO", "An owner or controller"],
  ["REPRESENTATIVE", "A representative"],
  ["INDIVIDUAL", "A person"],
];

export const CLASS_LABEL: Record<ScreeningClass, string> = {
  SANCTIONS: "Official sanctions lists",
  PEP_RCA: "Politically exposed persons and associates",
  ADVERSE_MEDIA: "Adverse media",
  OWNERSHIP_CONTROL: "Ownership and control",
};

export type CaseForm = { market: string; purpose: string; subjectType: ScreeningSubjectType; subjectRef: string; name: string; birth: string; countries: string; classes: ScreeningClass[] };

export const EMPTY_CASE: CaseForm = { market: "", purpose: "KYB", subjectType: "BUSINESS", subjectRef: "", name: "", birth: "", countries: "", classes: ["SANCTIONS"] };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** What the platform would refuse for the shape of the request, said before it has to. The platform decides eligibility itself. */
export function caseProblem(f: CaseForm, tenantId: string | undefined): string | null {
  if (!/^[A-Za-z]{2}$/.test(f.market.trim())) return "Enter the market as a two-letter country code, for example GB.";
  if (!f.purpose.trim()) return "Say what the screening is for.";
  if (f.name.trim().length < 2) return "Enter the name exactly as it appears on the identity record.";
  if (f.birth.trim() && !/^\d{4}(-\d{2}-\d{2})?$/.test(f.birth.trim())) return "Enter a birth year (2001) or a full date (2001-04-30).";
  if (f.classes.length === 0) return "Choose at least one check.";
  if (f.subjectType === "BUSINESS") {
    if (tenantId && f.subjectRef.trim() && f.subjectRef.trim().toLowerCase() !== tenantId.toLowerCase()) return "A company is screened only as your own organization.";
  } else if (!UUID.test(f.subjectRef.trim())) return "Enter the person's account identifier.";
  return null;
}

export function splitList(text: string): string[] {
  return text
    .split(/[,;\n]+/)
    .map((x) => x.trim())
    .filter(Boolean);
}

/** A fresh key for each time the person presses the button, kept until the request is answered, so a retry is the same request. */
export function newKey(): string {
  return `screening-${crypto.randomUUID()}`;
}

export const FINDING_TONE: Record<string, "neutral" | "ok" | "warn" | "danger" | "info"> = {
  "No candidate was found in the checked sources": "info",
  "Review pending": "warn",
  "Further information is needed": "warn",
  "Restricted for this use": "danger",
};

/** The words a result must never use. A guard for tests: the platform already avoids them. */
export const FORBIDDEN = ["aml approved", "aml passed", "clean", "no risk", "risk free"];

export function saysTooMuch(text: string): boolean {
  const t = text.toLowerCase();
  return FORBIDDEN.some((w) => t.includes(w));
}

export const ERROR_TEXT: Record<string, string> = {
  SCREENING_NOT_ELIGIBLE: "Screening cannot be requested for this case yet.",
  CASE_NOT_FOUND: "There is no such screening case for your organization.",
  TENANT_ROLE_REQUIRED: "Only an owner or an administrator can request screening.",
  REQUEST_NOT_FOUND: "There is nothing more we are waiting for on this case.",
  REQUEST_EXPIRED: "The date for this has passed. Please contact us.",
  REQUEST_CLOSED: "This request is already closed.",
};

/** What a customer is asked for, in plain words. The wording never says why. */
export const ITEM_LABEL: Record<string, string> = {
  SOURCE_OF_FUNDS: "Where the money for this relationship comes from",
  SOURCE_OF_WEALTH: "How your overall wealth was built",
  OWNERSHIP_EXPLANATION: "An explanation of who owns or controls the company",
  IDENTITY_CLARIFICATION: "A clarification of your identity details",
  PURPOSE_OF_RELATIONSHIP: "What you intend to use the account for",
  SUPPORTING_DOCUMENT: "A supporting document",
};
