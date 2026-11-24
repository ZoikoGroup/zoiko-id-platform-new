import type { VerifyApiGoLiveIn } from "@zoiko/api-client";

/** The plain-language side of the Go live screen (CR-153). The cell checks everything again. */

export const METHOD_LABEL: Record<string, string> = {
  document_authenticity: "Check the ID document",
  face_match: "Match the face to the document",
};

export const PURPOSE_LABEL: Record<string, string> = {
  customer_onboarding: "Opening a customer account",
  account_recovery: "Recovering an account",
  fraud_prevention: "Preventing fraud",
  age_verification: "Confirming age",
  employment: "An employment check",
};

export const methodLabel = (m: string): string => METHOD_LABEL[m] ?? m.replace(/_/g, " ");
export const purposeLabel = (p: string): string => PURPOSE_LABEL[p] ?? p.replace(/_/g, " ");

/** The request in words a company reads back: used to confirm what it asked for. */
export const REQUEST_STATE: Record<string, { label: string; tone: "info" | "ok" | "warn" | "danger" | "neutral"; text: string }> = {
  SUBMITTED: { label: "Sent, waiting for review", tone: "info", text: "Our team has your request and will take it into review." },
  IN_REVIEW: { label: "Being reviewed", tone: "info", text: "Our team is checking your company, terms and the markets you asked for." },
  APPROVED: { label: "Approved", tone: "ok", text: "Production is open for you." },
  DECLINED: { label: "Not approved", tone: "danger", text: "The request was not approved. Read the reason, fix what is needed and ask again." },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral", text: "You took this request back." },
};

const COUNTRY = /^[A-Za-z]{2}$/;

/** Two-letter country codes, separated by spaces, commas or new lines. Repeats are removed and the codes are upper-cased. */
export function parseMarkets(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[\s,;]+/)) {
    const c = part.trim().toUpperCase();
    if (c) seen.add(c);
  }
  return [...seen];
}

export function marketsProblem(markets: string[]): string | null {
  if (markets.length === 0) return "Enter at least one country, for example GB or US.";
  if (markets.length > 20) return "Choose at most 20 countries.";
  const bad = markets.find((c) => !COUNTRY.test(c));
  return bad ? `“${bad}” is not a two-letter country code, for example GB or US.` : null;
}

export type GoLiveDraft = {
  markets: string;
  methods: string[];
  purpose: string;
  band: string;
  useCase: string;
  terms: boolean;
  dpa: boolean;
};

export const EMPTY_DRAFT: GoLiveDraft = { markets: "", methods: [], purpose: "", band: "", useCase: "", terms: false, dpa: false };

/** Why the request cannot be sent yet, or null. The first thing in the order a person fills the form. */
export function draftProblem(d: GoLiveDraft): string | null {
  const m = marketsProblem(parseMarkets(d.markets));
  if (m) return m;
  if (d.methods.length === 0) return "Choose at least one check.";
  if (!d.purpose) return "Choose what you will use it for.";
  if (!d.band) return "Choose how many verifications you expect.";
  const n = d.useCase.trim().length;
  if (n < 10 || n > 500) return "Describe in 10 to 500 characters what you will use it for.";
  if (!d.terms || !d.dpa) return "Accept the terms and the data-processing terms to continue.";
  return null;
}

export function toRequest(d: GoLiveDraft, versions: { terms: string; dpa: string }): VerifyApiGoLiveIn {
  return {
    markets: parseMarkets(d.markets),
    methods: d.methods,
    purpose_code: d.purpose,
    volume_band: d.band,
    use_case: d.useCase.trim(),
    terms_version: versions.terms,
    dpa_version: versions.dpa,
    accept_terms: d.terms,
    accept_dpa: d.dpa,
  };
}
