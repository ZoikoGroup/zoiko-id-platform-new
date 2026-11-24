import { imageProblem } from "./logic";

/** The screens of identity verification. The person is guided one task at a time, as in the reference product: what you need, your details, how it works, which document, a
 * photograph of it, a selfie, and a review before anything is sent. The result is the check card that appears once a case exists, so it counts as the last screen. */
export const WIZARD_STEPS = ["INTRO", "DETAILS", "HOW", "DOCUMENT", "DOCUMENT_PHOTO", "SELFIE", "REVIEW"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];
export const TOTAL_SCREENS = WIZARD_STEPS.length + 1;

export const STEP_TITLE: Record<WizardStep, string> = {
  INTRO: "Before you start",
  DETAILS: "Confirm your details",
  HOW: "How verification works",
  DOCUMENT: "Choose your document",
  DOCUMENT_PHOTO: "Photograph your identity document",
  SELFIE: "Take a selfie",
  REVIEW: "Review and continue",
};

export type WizardDetails = { given: string; middle: string; family: string; birth: string; country: string };
export const NO_WIZARD_DETAILS: WizardDetails = { given: "", middle: "", family: "", birth: "", country: "" };

/** Why the details cannot go on, or null. Only their shape is checked: whether they match the document is the platform's judgement, made once and never stored. */
export function detailsProblem(d: WizardDetails, today: Date = new Date()): string | null {
  if (!d.given.trim()) return "Type your given name as it appears on the document.";
  if (!d.family.trim()) return "Type your family name as it appears on the document.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.birth)) return "Type your date of birth.";
  const born = new Date(`${d.birth}T00:00:00Z`);
  if (Number.isNaN(born.getTime()) || born > today || born.getUTCFullYear() < 1900) return "That date of birth is not possible.";
  return null;
}

export function fullName(d: Pick<WizardDetails, "given" | "middle" | "family">): string {
  return [d.given, d.middle, d.family]
    .map((p) => p.trim())
    .filter(Boolean)
    .join(" ");
}

/** The market a case is opened for: the country the person chose, so a stricter policy an owner wrote for that country still applies; otherwise the global policy's key.
 * The country never decides whether the check can run. */
export function marketFor(country: string): string {
  const c = country.trim().toUpperCase();
  return /^[A-Z]{2}$/.test(c) ? c : "GLOBAL";
}

type Photo = { type: string; size: number } | null;

/** Why the documents cannot go on, or null. An identity document and a live selfie are both needed; the person agrees to the comparison. */
export function documentsProblem(input: { docType: string; document: Photo; selfie: Photo; agreed: boolean }): string | null {
  if (!input.docType) return "Choose the identity document you will photograph.";
  if (!input.document) return "Add a photograph of the document.";
  const doc = imageProblem(input.document);
  if (doc) return doc;
  if (!input.selfie) return "Add a live photograph of your face.";
  const face = imageProblem(input.selfie);
  if (face) return face;
  if (!input.agreed) return "Agree to the comparison of your live photograph with the photograph on your document.";
  return null;
}

/** The screen a person is sent back to when something is missing: the first one that is not ready, or null when everything is. The agreement is given on the review screen
 * itself, so it is not a reason to go back. */
export function firstIncomplete(input: { details: WizardDetails; docType: string; document: Photo; selfie: Photo; agreed?: boolean }): WizardStep | null {
  if (detailsProblem(input.details)) return "DETAILS";
  if (!input.docType) return "DOCUMENT";
  if (!input.document || imageProblem(input.document)) return "DOCUMENT_PHOTO";
  if (!input.selfie || imageProblem(input.selfie)) return "SELFIE";
  return null;
}
