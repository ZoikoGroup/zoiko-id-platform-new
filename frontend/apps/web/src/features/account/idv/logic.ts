import type { IdvCase } from "@zoiko/api-client";

import { toBase64 } from "../selfie";

export const MY_IDV_KEY = ["my-idv-cases"] as const;

/** The applicant's journey (P01 to P07), in order. Reading the chip and the selfie need the phone app; the browser takes the notice, consent and a document photograph, and shows where the case stands. */
export type Step = "START" | "NOTICE" | "DOCUMENT" | "CHIP" | "SELFIE" | "STATUS";

/** The route a person takes with no phone app: a browser photograph that two independent reviewers look at. It names the market's signed alternate; the platform refuses it for a market whose policy does not. It never carries a chip claim. */
export const WEB_ROUTE = "WEB_REVIEW";

/** The route a market may sign where the platform decides alone: the applicant types four details, they are compared once with the platform's own reading of the document, and a match is verified at a lower grade. No person looks and no face is checked. */
export const AUTO_ROUTE = "WEB_AUTO";

export function isAuto(route: string): boolean {
  return route === AUTO_ROUTE;
}

/** The route a market may sign where the platform decides from a live face against the photograph on the uploaded document (CR-129): no reviewer, no liveness test, no check that the
 * document is genuine, the lowest grade. It works only where the owner acknowledged exactly that. */
export const FACE_ROUTE = "WEB_FACE";

export function isFace(route: string): boolean {
  return route === FACE_ROUTE;
}

export type Declared = { surname: string; given_names: string; document_number: string; birth_date: string };
export const NO_DETAILS: Declared = { surname: "", given_names: "", document_number: "", birth_date: "" };

/** Why the typed details cannot be sent yet, or null. Only their shape is checked here: whether they match the document is the platform's judgement. */
export function detailsProblem(d: Declared, today: Date = new Date()): string | null {
  if (!d.surname.trim() || !d.given_names.trim()) return "Type your surname and given names as they appear on the document.";
  if (d.document_number.trim().length < 3) return "Type the document number.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.birth_date)) return "Type your date of birth.";
  const born = new Date(`${d.birth_date}T00:00:00Z`);
  if (Number.isNaN(born.getTime()) || born > today || born.getUTCFullYear() < 1900) return "That date of birth is not possible.";
  return null;
}

export function trimDetails(d: Declared): Declared {
  return { surname: d.surname.trim(), given_names: d.given_names.trim(), document_number: d.document_number.trim(), birth_date: d.birth_date };
}

/** The steps shown for a route: the browser route has no chip and no selfie step. */
export function stepsFor(route: string): Step[] {
  return route === "CHIP" ? ["START", "NOTICE", "DOCUMENT", "CHIP", "SELFIE", "STATUS"] : ["START", "NOTICE", "DOCUMENT", "STATUS"];
}

export const STEP_LABEL: Record<Step, string> = {
  START: "Start",
  NOTICE: "Notice and consent",
  DOCUMENT: "Document photo",
  CHIP: "Passport chip",
  SELFIE: "Selfie",
  STATUS: "Where it stands",
};

const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/jpeg", "image/png"]);

/** Why a chosen file is refused before anything is sent, or null if it can go. Only a JPEG or PNG within the size the platform accepts. */
export function imageProblem(file: { type: string; size: number } | null): string | null {
  if (!file) return "Choose a photograph of the document first.";
  if (!IMAGE_TYPES.has(file.type)) return "Use a JPEG or PNG photograph.";
  if (file.size > MAX_IMAGE_BYTES) return "That photograph is too large. Use one under 6 MB.";
  if (file.size < 20 * 1024) return "That photograph is too small to read.";
  return null;
}

export async function fileToBase64(file: File): Promise<string> {
  return toBase64(new Uint8Array(await file.arrayBuffer()));
}

// ---- the simple identity page: one document, one photograph, one answer -----------------------------------------------------------------------------------

/** The two documents the simple page offers. A passport has two machine-readable lines the platform reads and checks itself; an Aadhaar card has none, so
 * people (two independent reviewers) look at the photograph. */
export type DocKind = "PASSPORT" | "AADHAAR";
export const DOC_LABEL: Record<DocKind, string> = { PASSPORT: "Passport", AADHAAR: "Aadhaar card" };
/** The documents the identity page offers, in this order, in every region. To retire one or add another later, change this list (and `DocKind`, `routeFor` and `docKindOf`). */
export const DOC_OPTIONS: readonly DocKind[] = ["PASSPORT", "AADHAAR"];
/** An Aadhaar card is an Indian document, so its market is fixed. A passport's market is the country that issued it, typed by the person. */
export const AADHAAR_MARKET = "IN";

/** Both documents take the face-match route: upload it, take a live photograph, and the platform compares them. (`AUTO_ROUTE` reads a passport's two lines; `WEB_ROUTE` sends a photograph to two
 * reviewers. Both stay available in a market's policy; this page no longer offers them.) */
export function routeFor(): string {
  return FACE_ROUTE;
}

export function docKindOf(c: Pick<IdvCase, "route">): DocKind {
  return isAuto(c.route) ? "PASSPORT" : "AADHAAR";
}

/** Where the newest check stands, in the words the page uses. REJECTED only when the platform or the reviewers rejected it: a photograph the platform could not
 * read is RETAKE, never a finding against the person. */
/** Attempts a person gets on the face-match route before an administrator reviews the photographs. The market policy's capture ceiling is set to the same number and is what the platform enforces. */
export const FACE_ATTEMPTS = 3;

export type SimpleState = "VERIFIED" | "REJECTED" | "WAITING" | "RETAKE" | "SEND" | "RESTART";
export function simpleState(c: Pick<IdvCase, "route" | "decision" | "signals">): SimpleState {
  if (c.decision === "APPROVED") return "VERIFIED";
  if (c.decision === "REJECTED") return "REJECTED";
  if (c.signals.optical === "NOT_STARTED") return "SEND";
  // the face-match route has no reviewers: a check the platform marked for review (a flagged capture) cannot recover, and sending photographs into it again changes nothing, so a new one is needed
  if (isFace(c.route) && c.decision === "REVIEW_REQUIRED") return "RESTART";
  return isAuto(c.route) || isFace(c.route) ? "RETAKE" : "WAITING"; // an automatic check nobody reviews asks for a better photograph; the other waits for two reviewers
}

/** The newest check that is still in play (not ended by the person), or null. */
export function latestCheck<T extends Pick<IdvCase, "decision" | "created_at">>(cases: readonly T[]): T | null {
  const live = cases.filter((c) => c.decision !== "ABANDONED");
  return [...live].sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;
}

/** What the applicant is told is next, from the case alone. It never promises an outcome. */
export function nextStep(c: Pick<IdvCase, "state" | "route" | "signals" | "decision" | "attempts">): Step {
  if (c.state === "CLOSED") return "STATUS";
  if (c.signals.optical === "NOT_STARTED") return "DOCUMENT";
  if (isAuto(c.route) || isFace(c.route)) return c.decision === "INDETERMINATE" ? "DOCUMENT" : "STATUS"; // nobody reviews it: an undecided case asks for a better photograph and the details again
  if (c.route !== "CHIP") return "STATUS"; // the browser route ends with the photograph: reviewers decide
  if (c.signals.nfc_read !== "READ_OK") return "CHIP";
  if (c.signals.liveness === "NOT_RUN") return "SELFIE";
  return "STATUS";
}

/** The address the phone app registers for opening a check (a custom scheme, or the universal link once the app's domain exists). It carries NO secret: only which case. The app signs in as the same person, and the platform refuses
 * anyone else's case (NOT_YOUR_CASE), so a copied or forwarded link opens nothing for another person. */
export const CHIP_APP_LINK = "zoikoid://identity-check";

export function chipLink(c: Pick<IdvCase, "case_id" | "market">, base: string = CHIP_APP_LINK): string {
  return `${base}?case=${encodeURIComponent(c.case_id)}&market=${encodeURIComponent(c.market)}`;
}

/** Whether this browser is on a phone or tablet, from the user agent. Only a hint to choose the wording: "open the app" here, or "send this to your phone". Never a security decision. */
export function onPhone(userAgent: string): boolean {
  return /Android|iPhone|iPad|iPod|Mobile/i.test(userAgent);
}

/** What the hand-off card says, from the case alone. It polls while the person is away in the app, and stops when the chip has been read or the case is closed. */
export function handoff(c: Pick<IdvCase, "state" | "route" | "signals">): "NOT_NEEDED" | "WAITING_FOR_APP" | "CHIP_DONE" {
  if (c.state === "CLOSED" || c.route !== "CHIP" || c.signals.optical === "NOT_STARTED") return "NOT_NEEDED";
  return c.signals.nfc_read === "READ_OK" ? "CHIP_DONE" : "WAITING_FOR_APP";
}

const FOR_APPLICANT: Record<string, string> = {
  FACE_ATTEMPTS_USED_UP: "You have used all the attempts allowed in 24 hours. Try again tomorrow with a clear, face-on photograph in good light, or contact support.",
  DETAILS_DO_NOT_MATCH_DOCUMENT: "One of the details you typed (name, document number or date of birth) is not on the document photograph. Correct it or clear it, then send again. Nothing was used up.",
  FACE_CONSENT_REQUIRED: "Tick the box to agree to the comparison of your two photographs, then send again.",
  LIVE_PHOTO_REQUIRED: "Add a live photograph of yourself as well as the document photograph, then send again.",
  FACE_ENGINE_UNAVAILABLE: "The face comparison is not available right now. Nothing was used up. Try again in a minute.",
  FACE_ROUTE_NOT_ENABLED: "Verifying with a face match is not switched on in this deployment.",
  SCAN_UNAVAILABLE: "The photograph could not be checked for safety right now. Nothing was used up. Try again in a minute.",
  ALTERNATE_ROUTE_NEEDS_INDEPENDENT_REVIEWERS: "Two people who work independently of each other will look at your photograph. You do not need to do anything.",
  ALTERNATE_ROUTE_REVIEW_ONLY: "A person will look at your photograph. This way of verifying is checked by people only.",
  ROUTE_NOT_ALLOWED_BY_POLICY: "Checking with a browser only is not available for that country.",
  DECLARED_DATA_DIFFERS_FROM_DOCUMENT: "The details you typed do not match the document. Check them against the page with your photo and start again if you made a mistake.",
  DECLARED_DATA_NOT_COMPARED: "Type your surname, given names, document number and date of birth with the photograph so they can be compared.",
  FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO: "Your live photograph did not match the photograph on the document. Take both again: the document flat and sharp, your face in good light, looking at the camera.",
  FACE_NOT_FOUND_OR_UNCLEAR: "We could not find one clear face in a photograph. Take it again in good light, with only you in the picture.",
  FACE_MATCH_NOT_RUN: "The face comparison has not run yet. Send the document photograph and the live photograph together.",
  FACE_CONSENT_GIVEN: "You agreed to the comparison.",
  DOCUMENT_READING_NOT_RELIABLE: "We could not read the two lines at the bottom reliably. Take the photograph again, flat, sharp and fully in view.",
  OPTICAL_REVIEW: "The photograph was not clear enough. Take it again in good light.",
  DOCUMENT_MANIPULATION_INDICATED: "This photograph could not be accepted. You can contact support or use another document.",
  TYPED_TRANSCRIPTION_IS_NOT_AUTHENTICATION: "Typing the details is not enough on its own. Send a photograph of the document.",
  MEDIA_RETENTION_NOT_SET: "Checking with a browser only is not available for your country yet.",
  DOCUMENT_EXPIRED: "The document has expired. Use a document that is still valid.",
  DOCUMENT_DATE_NOT_ESTABLISHED: "We could not read the expiry date reliably. Take the photograph again with the two lines at the bottom sharp, flat and fully in view.",
  CHIP_UNSUPPORTED: "This phone cannot read the passport chip. You may be offered another way to verify.",
  CHIP_USER_DECLINED: "You chose not to read the chip. You may be offered another way to verify.",
  CHIP_READ_FAILED: "The chip could not be read. Hold the phone steady on the passport and try again.",
  CHIP_NOT_STARTED: "Open the Zoiko app on your phone to read the chip in your passport.",
  CHIP_TRUST_ROOT_MISSING: "We could not complete the check of your passport chip for your country yet. Nothing is wrong with your passport.",
  WEB_CAPTURE_CANNOT_BE_CHIP_VERIFIED: "A browser cannot read a chip. Use the phone app to finish.",
  MRZ_CHECK_DIGITS_FAILED: "The two lines at the bottom of the photo could not be read correctly. Take the photo again in good light, flat and sharp.",
  LIVENESS_NOT_RUN: "The selfie step is still to do, in the phone app.",
  LIVENESS_INCONCLUSIVE: "The selfie check could not decide. You can try again.",
  FACE_MATCH_INCONCLUSIVE: "The face check could not decide. You can try again.",
  CAPTURE_NOT_FRESH: "That photograph was too old. Take a new one now.",
  CAPTURE_MEDIA_REPLAYED: "That exact photograph was already used. Take a new one.",
  CAPTURE_NONCE_ALREADY_USED: "Take a new photograph and send it again.",
  RETRY_CEILING_REACHED: "You have used all the attempts for this step. Start again later or contact support.",
  RETRY_CEILING_NOT_CONFIGURED: "This step is not available yet for your country.",
  MARKET_POLICY_NOT_PUBLISHED: "Identity proofing is not available for that country yet.",
  BIOMETRICS_OFF: "Face checks are not switched on for your country, so this step cannot run.",
  CASE_CLOSED: "This case is finished.",
  NOT_YOUR_CASE: "Only the person who started this case can add to it.",
  IMAGING_NOT_CONFIGURED: "Photograph checks are not available right now.",
  IMAGING_UNAVAILABLE: "Photograph checks are not available right now. Try again soon.",
  IMAGE_REFUSED: "That file could not be read as a photograph.",
};

/** Plain words for the applicant. Reasons that would only help someone gaming the check are not explained; they get the general line. */
export function applicantText(code: string): string {
  if (code.startsWith("RECAPTURE_REQUESTED:")) return "A reviewer asked for a clearer photograph. Take a new one and send it again. This does not count as a failed attempt.";
  if (code.startsWith("REVIEWER_REJECTED:")) return "A reviewer could not accept this document. You can contact support or start again with another document.";
  if (code.startsWith("BIOMETRICS_OFF:")) return "Face checks are not switched on for your country, so this step cannot run.";
  return FOR_APPLICANT[code] ?? "We need a person to look at this case, or more evidence, before we can say more.";
}

type Outcome = { tone: "ok" | "warn" | "danger" | "info" | "neutral"; text: string };

const OUTCOMES: Record<string, Outcome> = {
  APPROVED: { tone: "ok", text: "Your identity check is complete." },
  REVIEW_REQUIRED: { tone: "warn", text: "A person is looking at your case. You do not need to do anything." },
  REJECTED: { tone: "danger", text: "We could not accept this document. You can contact support or ask for a review." },
  INDETERMINATE: { tone: "info", text: "We cannot decide yet because some evidence is missing. This is not a finding against you." },
  ABANDONED: { tone: "neutral", text: "You left this check before it finished." },
};

export function outcomeOf(decision: string): Outcome {
  return OUTCOMES[decision] ?? { tone: "info", text: "We need a person to look at this case before we can say more." };
}
