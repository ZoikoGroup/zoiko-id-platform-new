import type { CapabilityDependencies, OfferCard, OfferState, SolutionIn, SolutionPurpose, SolutionVolumes } from "@zoiko/api-client";

/** The solution builder's logic (ZID-COM-WF-2026-11 WF-C02, B06). The server decides what is available and what a configuration needs; this only
 * helps a person fill the form in and shows the answer truthfully. Nothing here prices anything, and a forecast is never usage. */

export type StepId = "purpose" | "services" | "coverage" | "volumes" | "review";

export const STEPS: { id: StepId; label: string }[] = [
  { id: "purpose", label: "Purpose" },
  { id: "services", label: "Services" },
  { id: "coverage", label: "Coverage" },
  { id: "volumes", label: "Volumes" },
  { id: "review", label: "Review" },
];

export const PURPOSES: { id: SolutionPurpose; label: string; description: string }[] = [
  { id: "ONBOARDING", label: "Customer onboarding", description: "Verify new customers or members as they join." },
  { id: "COMPLIANCE", label: "Compliance and screening", description: "Know-your-business and risk checks you must keep up to date." },
  { id: "PROFESSIONAL_HIRING", label: "Professional hiring", description: "Qualifications, licences and employment checks for candidates." },
  { id: "REGULATED_TRANSACTION", label: "Regulated transaction", description: "A transaction that needs a verified person and a signature." },
  { id: "MARKETPLACE", label: "Marketplace", description: "Trust between the buyers and sellers on your platform." },
];

export const VOLUME_FIELDS: { id: "expected_units" | "rechecks_per_year" | "manual_reviews"; label: string; help: string }[] = [
  { id: "expected_units", label: "Expected checks per year", help: "Your forecast. It is never billed as usage." },
  { id: "rechecks_per_year", label: "Rechecks per year", help: "How often an existing subject would be checked again." },
  { id: "manual_reviews", label: "Manual reviews per year", help: "Only billed if you instruct and price a manual review." },
];

export type Draft = {
  purpose: SolutionPurpose | null;
  families: string[];
  subjectCountry: string;
  payerCountry: string;
  region: string;
  /** What the person typed, per family and field. Parsed only when the configuration is built. */
  volumes: Record<string, Record<string, string>>;
};

export const EMPTY_DRAFT: Draft = { purpose: null, families: [], subjectCountry: "", payerCountry: "", region: "", volumes: {} };

const COUNTRY = /^[A-Z]{2}$/;

export const cleanCountry = (v: string): string => v.trim().toUpperCase();

export function toggleFamily(families: string[], code: string): string[] {
  return families.includes(code) ? families.filter((f) => f !== code) : [...families, code].sort();
}

/** A selected service whose prerequisite is not selected, for example a signature without a verified identity. These block going further. */
export function missingDependencies(families: string[], deps: CapabilityDependencies | undefined): { family: string; needs: string }[] {
  if (!deps) return [];
  const out: { family: string; needs: string }[] = [];
  for (const f of deps.families) {
    if (!families.includes(f.family)) continue;
    for (const need of f.depends_on) if (!families.includes(need)) out.push({ family: f.family, needs: need });
  }
  return out;
}

/** What depends on a service you are about to remove, so the person is told rather than left with an impossible selection. */
export function dependents(families: string[], code: string, deps: CapabilityDependencies | undefined): string[] {
  if (!deps) return [];
  return deps.families.filter((f) => families.includes(f.family) && f.depends_on.includes(code)).map((f) => f.family);
}

export type Parsed = { ok: true; value: number | undefined } | { ok: false };

/** A volume is a whole, non-negative number. Blank means "not stated" and is left out rather than sent as zero. */
export function parseVolume(text: string): Parsed {
  const t = text.trim().replaceAll(",", "");
  if (t === "") return { ok: true, value: undefined };
  if (!/^\d{1,10}$/.test(t)) return { ok: false };
  const n = Number(t);
  return n <= 1_000_000_000 ? { ok: true, value: n } : { ok: false };
}

export function volumeErrors(draft: Draft): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [family, fields] of Object.entries(draft.volumes)) {
    if (!draft.families.includes(family)) continue;
    for (const [field, text] of Object.entries(fields)) {
      if (!parseVolume(text).ok) out[`${family}.${field}`] = "Enter a whole number, or leave blank.";
    }
  }
  return out;
}

export function marketErrors(draft: Draft): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of [["subjectCountry", draft.subjectCountry], ["payerCountry", draft.payerCountry]] as const) {
    if (value.trim() !== "" && !COUNTRY.test(cleanCountry(value))) out[key] = "Use a two-letter country code, for example DE.";
  }
  if (draft.region.trim() !== "" && (draft.region.trim().length < 2 || draft.region.trim().length > 40)) out.region = "Use 2 to 40 characters.";
  return out;
}

/** The request body for the server, or null while the draft cannot be built (no purpose, no services, or a malformed figure). */
export function buildBody(draft: Draft): SolutionIn | null {
  if (!draft.purpose || draft.families.length === 0) return null;
  if (Object.keys(volumeErrors(draft)).length || Object.keys(marketErrors(draft)).length) return null;
  const volumes: SolutionVolumes = {};
  for (const family of draft.families) {
    const fields = draft.volumes[family] ?? {};
    const clean: SolutionVolumes[string] = {};
    for (const [field, text] of Object.entries(fields)) {
      const parsed = parseVolume(text);
      if (parsed.ok && parsed.value !== undefined) clean[field as keyof SolutionVolumes[string]] = parsed.value;
    }
    if (Object.keys(clean).length) volumes[family] = clean;
  }
  return {
    purpose: draft.purpose,
    families: [...draft.families].sort(),
    subject_country: draft.subjectCountry.trim() ? cleanCountry(draft.subjectCountry) : null,
    payer_country: draft.payerCountry.trim() ? cleanCountry(draft.payerCountry) : null,
    operating_region: draft.region.trim() || null,
    volumes,
  };
}

/** Whether the person may move forward from a step. Coverage and review need the market; nothing needs a price, because there is none. */
export function canAdvance(step: StepId, draft: Draft, deps: CapabilityDependencies | undefined): { ok: boolean; why?: string } {
  if (step === "purpose") return draft.purpose ? { ok: true } : { ok: false, why: "Choose what you are setting this up for." };
  if (step === "services") {
    if (draft.families.length === 0) return { ok: false, why: "Choose at least one service." };
    const miss = missingDependencies(draft.families, deps);
    return miss.length ? { ok: false, why: "Add the services your selection depends on." } : { ok: true };
  }
  if (step === "coverage") {
    if (Object.keys(marketErrors(draft)).length) return { ok: false, why: "Fix the market details." };
    return draft.subjectCountry.trim() || draft.payerCountry.trim() ? { ok: true } : { ok: false, why: "Name at least one country so coverage can be judged." };
  }
  if (step === "volumes") return Object.keys(volumeErrors(draft)).length ? { ok: false, why: "Fix the volumes." } : { ok: true };
  return { ok: true };
}

export const STATE_LABEL: Record<OfferState, string> = {
  AVAILABLE: "Available",
  CONTACT_SALES: "Contact sales",
  NOT_ELIGIBLE: "Not eligible",
  RESEARCH_ONLY: "Research only",
  MARKET_REQUIRED: "Choose a market",
};

export function stateTone(state: OfferState): "ok" | "info" | "warn" | "danger" | "neutral" {
  if (state === "AVAILABLE") return "ok";
  if (state === "NOT_ELIGIBLE") return "danger";
  if (state === "RESEARCH_ONLY") return "warn";
  if (state === "CONTACT_SALES") return "info";
  return "neutral";
}

/** Coverage told in four distinct groups, as the document asks: confirmed, assisted, pending verification and unavailable. */
export function coverageGroups(cards: OfferCard[]): { id: "confirmed" | "assisted" | "pending" | "unavailable"; title: string; help: string; cards: OfferCard[] }[] {
  const pick = (states: OfferState[]) => cards.filter((c) => states.includes(c.state));
  return [
    { id: "confirmed" as const, title: "Confirmed", help: "Recorded and released for this market.", cards: pick(["AVAILABLE"]) },
    { id: "assisted" as const, title: "Assisted", help: "Zoiko sets this up with you; nothing is recorded for self-service yet.", cards: pick(["CONTACT_SALES", "MARKET_REQUIRED"]) },
    { id: "pending" as const, title: "Pending verification", help: "Designed but not yet released for this market.", cards: pick(["RESEARCH_ONLY"]) },
    { id: "unavailable" as const, title: "Unavailable", help: "A right or the legal basis is missing for this market.", cards: pick(["NOT_ELIGIBLE"]) },
  ].filter((g) => g.cards.length > 0);
}

/** The one line a person sees about what happens next. With no signed price the only way forward is a quote, never a checkout. */
export function nextStep(cards: OfferCard[]): string {
  if (cards.length === 0) return "Choose services to continue.";
  if (cards.every((c) => c.state === "AVAILABLE")) return "Every service is available. A binding price still needs a signed quote or order form.";
  return "Send this to the deal desk. They confirm what can be offered for your markets and send a quote you approve.";
}

/** A draft saved long ago reads as expired and cannot be sent. */
export function expiryNote(expiresAt: string, now: Date = new Date()): string {
  const left = Math.ceil((new Date(expiresAt).getTime() - now.getTime()) / 86_400_000);
  if (left <= 0) return "This draft has expired. Save the configuration again to send it.";
  return left === 1 ? "This draft expires tomorrow." : `This draft expires in ${left} days.`;
}
