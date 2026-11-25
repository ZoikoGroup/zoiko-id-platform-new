/** Every word the hosted verification shows (ZID-CP-WF01 §06). One catalog per reviewed language: a language is offered only when the server lists it as
 * reviewed (ZID_HOSTED_VERIFY_LANGUAGES) AND a catalog exists here. Legal notices and this copy are never machine-translated: a new language is a reviewed
 * catalog added below, with the same keys. */

export type Messages = {
  opening: string;
  linkUnusable: string;
  linkUnusableBody: (detail: string) => string;
  aBusiness: string;
  securedBy: (by: string) => string;
  stepOf: (at: number, of: number) => string;
  testMode: string;
  whatToDo: string;
  stepFailed: string;
  leave: string;
  welcomeTitle: string;
  requestedBy: string;
  purpose: string;
  whatWeAsk: string;
  askDocument: string;
  askDocumentAndFace: string;
  howUsed: string;
  readNotice: (version: string) => string;
  language: string;
  agreePurpose: string;
  agreeFace: string;
  continue: string;
  chooseTitle: string;
  cannotHere: string;
  getPhoneLink: string;
  useThis: string;
  phoneLinkTitle: (minutes: number) => string;
  phoneLinkBody: string;
  phoneLinkQr: string;
  whyUnavailable: string;
  notOffered: string;
  withheld: Record<string, string>;
  kind: Record<string, string>;
  captureTitle: string;
  captureHint: string;
  chooseFile: string;
  documentReady: string;
  detailsTitle: string;
  detailsHint: string;
  surname: string;
  givenNames: string;
  documentNumber: string;
  birthDate: string;
  liveTitle: string;
  liveHint: string;
  liveReady: string;
  sending: string;
  sendPhotos: string;
  submitTitle: string;
  submitBody: string;
  submit: string;
  processingTitle: string;
  processingBody: string;
  reference: (ref: string) => string;
  outcome: Record<"FINAL_APPROVED" | "FINAL_DECLINED" | "INCONCLUSIVE", { title: string; body: string }>;
  returnTo: (name: string | null) => string;
  expiredTitle: string;
  endedTitle: string;
  endedBody: (name: string | null) => string;
  /** the §10 codes the server may name; the server's own sentence is shown when a code is missing here */
  action: Record<string, string>;
  somethingWrong: string;
};

const en: Messages = {
  opening: "Opening your verification…",
  linkUnusable: "This link cannot be used",
  linkUnusableBody: (detail) => `${detail} Ask the business that sent it for a new link.`,
  aBusiness: "A business",
  securedBy: (by) => `Secured by ${by}`,
  stepOf: (at, of) => `Step ${at} of ${of}`,
  testMode: "Test mode: no real check is made",
  whatToDo: "What to do",
  stepFailed: "This step did not go through",
  leave: "Leave without verifying",
  welcomeTitle: "Why we need to verify your identity",
  requestedBy: "Requested by",
  purpose: "Purpose",
  whatWeAsk: "What we may ask for",
  askDocument: "A photograph of an identity document.",
  askDocumentAndFace: "A photograph of an identity document, and a live photograph of you if you agree.",
  howUsed: "How your information is used and kept",
  readNotice: (v) => `Read the notice (${v})`,
  language: "Language",
  agreePurpose: "I agree to this verification for the purpose above.",
  agreeFace: "I agree to a comparison of a live photograph of me with my document's photograph. I can verify another way if I do not.",
  continue: "Continue",
  chooseTitle: "Choose how to confirm your identity",
  cannotHere: "This device cannot do this. Continue on a phone that can.",
  getPhoneLink: "Get a link for my phone",
  useThis: "Use this",
  phoneLinkTitle: (m) => `Open this on your phone within ${m} minute${m === 1 ? "" : "s"}`,
  phoneLinkBody: "Scan the code with your phone's camera, or open the link on it. It works once. Never share it with anyone else.",
  phoneLinkQr: "Code to continue on your phone",
  whyUnavailable: "Why are some methods unavailable?",
  notOffered: "It is not offered here.",
  withheld: { BIOMETRICS_NOT_AUTHORIZED_IN_MARKET: "A face comparison is not offered where you are." },
  kind: {
    CHIP_AND_DOCUMENT: "Passport with a chip (read with a phone)",
    DOCUMENT_REVIEWED: "Photograph of your ID, checked by people",
    DOCUMENT_AUTOMATIC: "Photograph of your ID",
    DOCUMENT_AND_FACE: "Photograph of your ID and a live photograph of you",
  },
  captureTitle: "Photograph your identity document",
  captureHint: "Use the real document, in good light. The camera opens only when you choose it.",
  chooseFile: "Or choose a photograph from this device",
  documentReady: "Document photograph ready.",
  detailsTitle: "Your details, as printed on the document",
  detailsHint: "We compare them once with the document and do not keep them.",
  surname: "Surname",
  givenNames: "Given names",
  documentNumber: "Document number",
  birthDate: "Date of birth",
  liveTitle: "A live photograph of you",
  liveHint: "Why: to check that the person matches the document. Keep your face visible.",
  liveReady: "Live photograph ready.",
  sending: "Sending securely…",
  sendPhotos: "Send photographs",
  submitTitle: "Send your verification",
  submitBody: "Your photographs were received. When you send, they are checked; you cannot add more afterwards unless we ask.",
  submit: "Send for checking",
  processingTitle: "We're checking your information",
  processingBody: "A person may need to look at it, which can take longer. You can safely leave this page; the business that asked will be told the outcome.",
  reference: (ref) => `Your reference: ${ref}. Keep it if you contact support.`,
  outcome: {
    FINAL_APPROVED: { title: "Your verification request is finished", body: "The business that asked receives the result." },
    FINAL_DECLINED: { title: "We could not complete this verification", body: "The business that asked receives the result. This is not an accusation of anything." },
    INCONCLUSIVE: { title: "We could not verify your identity with this method", body: "The business that asked receives the result and may offer you another way." },
  },
  returnTo: (name) => `Return to ${name ?? "the business"}`,
  expiredTitle: "This verification link has expired",
  endedTitle: "This verification has ended",
  endedBody: (name) => `Ask ${name ?? "the business that asked"} for a new link if you still need to verify.`,
  action: {
    CAPTURE_QUALITY: "We could not read the image clearly. Please retake it.",
    UNSUPPORTED_DOCUMENT: "This document is not supported for this check.",
    NFC_UNAVAILABLE: "Your device cannot complete this chip check.",
    NFC_TRUST_INDETERMINATE: "We could not confirm this chip automatically.",
    DEVICE_RISK: "We need another verification method.",
    PROVIDER_TEMPORARILY_UNAVAILABLE: "This verification step is unavailable right now.",
    SESSION_EXPIRED: "This verification link has expired.",
    LIMIT_REACHED: "We could not complete this step. Contact support.",
    REVIEW_REQUIRED: "Your verification needs additional review.",
    INCONCLUSIVE: "We could not verify your identity with this method.",
    SUSPICIOUS_SIGNAL: "We need an additional verification step.",
  },
  somethingWrong: "Something went wrong. Try again.",
};

/** Reviewed catalogs only. Add a language here (same keys, reviewed copy) and list it in ZID_HOSTED_VERIFY_LANGUAGES. */
export const CATALOGS: Record<string, Messages> = { en };

/** The languages to offer: those the server lists as reviewed that also have a catalog; English when none match. */
export function offered(serverLanguages: string[]): string[] {
  const got = serverLanguages.filter((l) => l in CATALOGS);
  return got.length ? got : ["en"];
}

export function messages(lang: string): Messages {
  return CATALOGS[lang] ?? CATALOGS[primary(lang)] ?? en;
}

/** The language's own name for itself, for the picker. */
export function languageName(lang: string): string {
  try {
    return new Intl.DisplayNames([lang], { type: "language" }).of(lang) ?? lang;
  } catch {
    return lang;
  }
}

const RTL = new Set(["ar", "he", "fa", "ur", "ps", "sd", "ug", "yi", "dv", "ckb"]);

/** Layout direction for a BCP 47 tag (§06: RTL-aware layouts). */
export function dirOf(lang: string): "rtl" | "ltr" {
  return RTL.has(primary(lang)) ? "rtl" : "ltr";
}

function primary(lang: string): string {
  return (lang.split("-")[0] ?? lang).toLowerCase();
}
