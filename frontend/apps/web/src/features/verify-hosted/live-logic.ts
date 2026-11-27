/** The live check on the hosted page (decision D03, docs/specs/verify-api-live-check-product-definition.md). Pure helpers; the cell checks everything again.
 *
 * What the page may and may not say is fixed here, once:
 * - it says "a photograph of the document and a face match", never "liveness", "biometric verification", "authentic" or "approved";
 * - it never shows the person a score, a threshold, the word "fraud" or whether they "passed": the company decides, and the person is told only that their photographs were checked and the result sent;
 * - a face that did not match is a retake, never an accusation. */

export type LiveView = {
  live: true;
  notice_version: string | null;
  attempts_left: number;
  /** 0: neither photograph is kept. Otherwise both are kept this many days, and the notice says so. */
  photos_kept_days?: number;
};

export const isLive = (v: { live?: boolean }): v is LiveView & { live: true } => v.live === true;

/** The reasons the cell may give a retake, in the person's words. Anything not listed gets the general line, so an unexpected code never shows as raw text. */
export const RETRY_GUIDANCE: Record<string, string> = {
  FACE_NOT_FOUND_OR_UNCLEAR: "We could not see your face clearly. Face the camera in good light, take off glasses or a hat, and try again.",
  FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO: "We could not match the two photographs. Use good, even light with no glare, hold the document flat, and try again.",
  DOCUMENT_UNREADABLE: "We could not read the document. Put it on a plain surface, fill the frame, avoid glare and shadows, and try again.",
  DOCUMENT_EXPIRED: "That document is out of date. Use a document that is still valid.",
  ENGINE_UNAVAILABLE: "We could not check your photographs just now. Nothing was lost. Please try again in a moment.",
};
const GENERAL_RETRY = "That did not work. Please take both photographs again.";

export const retryText = (reason: string | null | undefined): string => (reason && Object.hasOwn(RETRY_GUIDANCE, reason) ? RETRY_GUIDANCE[reason]! : GENERAL_RETRY);

export function attemptsText(left: number): string {
  if (left <= 0) return "No tries left.";
  return left === 1 ? "1 try left." : `${left} tries left.`;
}

/** The notice before anything is photographed: who is asking, what is taken, who processes it, what is kept, and that the person can stop. The company is named as the controller. */
export function noticeLines(requestedBy: string, photosKeptDays = 0): string[] {
  return [
    `${requestedBy} has asked Zoiko iD to check who you are.`,
    "You will photograph an identity document and then take a photograph of your face. Zoiko iD compares the face on the document with your photograph.",
    "This is a photograph and face comparison. It does not test whether a live person is in front of the camera, and it does not check that the document is genuine.",
    photosKeptDays > 0
      ? `Zoiko iD does this for ${requestedBy}. Both photographs are kept, encrypted, for ${photosKeptDays} ${photosKeptDays === 1 ? "day" : "days"} so that people at ${requestedBy} can look at them to understand what happened, and then they are deleted. Each time one is opened, the reason is recorded. ${requestedBy} receives the result.`
      : `Zoiko iD does this for ${requestedBy} and keeps neither photograph. ${requestedBy} receives the result.`,
    "You can stop at any time. If you stop now, nothing is photographed.",
  ];
}

export const DECLINED_TEXT = "You chose not to continue, so nothing was photographed. You can go back to the company and ask how else to verify.";

/** What the person is told at the end: only that the checking is done and the result was sent. Never the result. */
export const finishedLive = (requestedBy: string): string => `Thank you. Your photographs were checked and the result was sent to ${requestedBy}. You can close this page.`;

export const EXHAUSTED_TEXT = (requestedBy: string): string => `We could not complete the check. ${requestedBy} has been told, and can tell you what to do next. You can close this page.`;

export type Capture = { document: File | null; selfie: File | null };

/** Why the two photographs cannot be sent yet, or null. */
export function captureProblem(c: Capture): string | null {
  if (!c.document) return "Take a photograph of the document first.";
  if (!c.selfie) return "Take a photograph of your face.";
  return null;
}

export type CaptureAnswer = { done: boolean; retry?: boolean; reason?: string | null; attempts_left?: number; return_origin?: string | null; closed?: boolean };

/** Where the flow goes after an answer: a retake with the reason and tries left, or the end. */
export function nextAfter(a: CaptureAnswer): { kind: "retry"; text: string; left: number } | { kind: "done"; exhausted: boolean } {
  if (a.done) return { kind: "done", exhausted: a.closed === true };
  return { kind: "retry", text: retryText(a.reason), left: Math.max(0, a.attempts_left ?? 0) };
}
