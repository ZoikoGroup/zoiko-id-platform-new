import type { IdvCase } from "@zoiko/api-client";

import { toBase64 } from "../selfie";

export const MY_IDV_KEY = ["my-idv-cases"] as const;

/** The applicant's journey (P01 to P07), in order. Reading the chip and the selfie need the phone app; the browser takes the notice, consent and a document photograph, and shows where the case stands. */
export type Step = "START" | "NOTICE" | "DOCUMENT" | "CHIP" | "SELFIE" | "STATUS";

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

/** What the applicant is told is next, from the case alone. It never promises an outcome. */
export function nextStep(c: Pick<IdvCase, "state" | "route" | "signals" | "decision" | "attempts">): Step {
  if (c.state === "CLOSED") return "STATUS";
  if (c.signals.optical === "NOT_STARTED") return "DOCUMENT";
  if (c.route === "CHIP" && c.signals.nfc_read !== "READ_OK") return "CHIP";
  if (c.signals.liveness === "NOT_RUN") return "SELFIE";
  return "STATUS";
}

const FOR_APPLICANT: Record<string, string> = {
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
