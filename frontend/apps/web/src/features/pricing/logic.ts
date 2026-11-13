import type { PackagingState, PublicAvailability, PublicLeadIn, PublicPricingPage, PublicState } from "@zoiko/api-client";

/** The pricing page's logic (ZID-UX-PRICE-004). The server decides what is available, where and why; this only carries the buyer's choices, resolves what a table cell
 * says and checks a form before it is sent. Nothing here prices anything, and nothing infers a market from the browser. */

export type Context = { subject: string; payer: string; issuer: string; region: string };
export const EMPTY_CONTEXT: Context = { subject: "", payer: "", issuer: "", region: "" };

/** ISO 3166-1 alpha-2 codes, shown by name (never by flag alone). A code the browser cannot name is shown as the code. */
export const COUNTRY_CODES =
  "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split(
    " ",
  );

export function countryName(code: string, locale = "en"): string {
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function countryOptions(locale = "en"): { id: string; label: string }[] {
  return COUNTRY_CODES.map((c) => ({ id: c, label: `${countryName(c, locale)} (${c})` })).sort((a, b) => a.label.localeCompare(b.label, locale));
}

export const cleanCode = (v: string): string => v.trim().toUpperCase();
export const isCountry = (v: string): boolean => /^[A-Z]{2}$/.test(v);

/** The query for the availability route. A blank field is left out, so the server reports it as not evaluated rather than guessing it. */
export function availabilityQuery(c: Context): string {
  const q = new URLSearchParams();
  if (isCountry(c.subject)) q.set("subject_country", c.subject);
  if (isCountry(c.payer)) q.set("payer_country", c.payer);
  if (isCountry(c.issuer)) q.set("issuer_country", c.issuer);
  if (c.region.trim()) q.set("operating_region", c.region.trim());
  return q.toString();
}

export const marketNamed = (c: Context): boolean => isCountry(c.subject) || isCountry(c.payer) || c.region.trim() !== "";

/** Compare-matrix groups from WF-P05, in order. */
export const GROUPS: { id: string; label: string; families: string[] }[] = [
  { id: "core", label: "Core checks", families: ["IDV", "DOC", "BIO"] },
  { id: "credential", label: "Credential verification", families: ["QUAL", "PRO", "WORK"] },
  { id: "risk", label: "Risk and screening", families: ["KYB", "SCR"] },
  { id: "authority", label: "Credentials and authority", families: ["AUTHN", "DELEG", "VC"] },
  { id: "esign", label: "Electronic signatures", families: ["SIGN"] },
  { id: "developer", label: "Developer", families: ["API"] },
  { id: "evidence", label: "Evidence", families: ["EVID"] },
  { id: "operations", label: "Operations", families: ["SOV", "REV"] },
];

export type CellState = "INCLUDED" | "OPTIONAL" | "QUOTE_ONLY" | "NOT_IN_MARKET";

export const CELL_LABEL: Record<CellState, string> = {
  INCLUDED: "Included",
  OPTIONAL: "Optional",
  QUOTE_ONLY: "Quote only",
  NOT_IN_MARKET: "Not in market",
};

export const CELL_HELP: Record<CellState, string> = {
  INCLUDED: "Part of this plan. A metered unit may still apply.",
  OPTIONAL: "A module you can add to this plan, subject to your market.",
  QUOTE_ONLY: "Scoped with Sales before it can be offered.",
  NOT_IN_MARKET: "Not offered for the market you chose.",
};

const FROM_PACKAGING: Record<PackagingState, CellState> = { included: "INCLUDED", optional: "OPTIONAL", quote: "QUOTE_ONLY" };

/** What one cell of the matrix says. The plan's own packaging is the starting point; the market can only make it more cautious: a market that restricts or does not release a
 * service says Not in market, one that is not recorded says Quote only, and a market that is not named leaves the packaging as it is. Never more generous than the plan. */
export function cellFor(packaging: PackagingState, market: PublicState | undefined): CellState {
  if (market === "NOT_AVAILABLE" || market === "RESTRICTED") return "NOT_IN_MARKET";
  if (market === "SALES_ASSISTED") return "QUOTE_ONLY";
  return FROM_PACKAGING[packaging];
}

export const MARKET_LABEL: Record<PublicState, string> = {
  AVAILABLE: "Available",
  SALES_ASSISTED: "Sales-assisted",
  RESTRICTED: "Restricted",
  NOT_AVAILABLE: "Not available",
  MARKET_REQUIRED: "Choose a market",
};

export const MARKET_HELP: Record<PublicState, string> = {
  AVAILABLE: "Released for this market and payer.",
  SALES_ASSISTED: "Zoiko sets this up with you; nothing is recorded for self-service yet.",
  RESTRICTED: "A right or the legal basis for this market is not in force.",
  NOT_AVAILABLE: "Designed, but not released for this market.",
  MARKET_REQUIRED: "Name a market to see availability.",
};

export function stateByFamily(a: PublicAvailability | undefined): Record<string, PublicState> {
  return Object.fromEntries((a?.cards ?? []).map((c) => [c.family, c.state]));
}

/** The services a set of use cases preloads, sorted, and any whose prerequisite is not among them (a signature without a verified identity). */
export function scopeFromUseCases(page: PublicPricingPage, selected: string[]): { families: string[]; missing: { family: string; needs: string }[] } {
  const set = new Set<string>();
  for (const u of page.use_cases) if (selected.includes(u.code)) for (const f of u.families) set.add(f);
  const families = [...set].sort();
  const missing: { family: string; needs: string }[] = [];
  for (const f of page.families) {
    if (!set.has(f.family)) continue;
    for (const need of f.depends_on) if (!set.has(need)) missing.push({ family: f.family, needs: need });
  }
  return { families, missing };
}

/** Services in a scope whose prerequisite is not also in it. Computed on the whole scope, so what a buyer added by hand counts. */
export function missingFor(page: PublicPricingPage, families: string[]): { family: string; needs: string }[] {
  const set = new Set(families);
  const out: { family: string; needs: string }[] = [];
  for (const f of page.families) {
    if (!set.has(f.family)) continue;
    for (const need of f.depends_on) if (!set.has(need)) out.push({ family: f.family, needs: need });
  }
  return out;
}

/** Add the prerequisites a scope is missing, so a buyer is never handed an impossible combination. */
export function withPrerequisites(page: PublicPricingPage, families: string[]): string[] {
  const set = new Set(families);
  let grew = true;
  while (grew) {
    grew = false;
    for (const f of page.families) {
      if (!set.has(f.family)) continue;
      for (const need of f.depends_on) {
        if (set.has(need)) continue;
        set.add(need);
        grew = true;
      }
    }
  }
  return [...set].sort();
}

// ------------------------------------------------------------------------------------------------------------------------ the inquiry form

export const NOTICE_VERSION = "pricing-inquiry-notice-2026-11-draft";
export const VOLUME_BANDS: { id: PublicLeadIn["volume_band"]; label: string }[] = [
  { id: "UNDER_1K", label: "Under 1,000 checks a year" },
  { id: "FROM_1K_TO_10K", label: "1,000 to 10,000 a year" },
  { id: "FROM_10K_TO_100K", label: "10,000 to 100,000 a year" },
  { id: "OVER_100K", label: "Over 100,000 a year" },
  { id: "UNSURE", label: "Not sure yet" },
];
export const PARTNER_TYPES: { id: NonNullable<PublicLeadIn["partner_type"]>; label: string }[] = [
  { id: "RESELLER", label: "Reseller" },
  { id: "REFERRAL", label: "Referral partner" },
  { id: "EMBEDDED_ISV", label: "Embedded software vendor" },
];

export type LeadDraft = {
  kind: "ENTERPRISE" | "PARTNER";
  name: string;
  email: string;
  company: string;
  country: string;
  region: string;
  volume: PublicLeadIn["volume_band"] | "";
  entities: string;
  referral: string;
  needs: string;
  partnerType: NonNullable<PublicLeadIn["partner_type"]> | "";
  website: string;
};

export const EMPTY_LEAD: LeadDraft = { kind: "ENTERPRISE", name: "", email: "", company: "", country: "", region: "", volume: "", entities: "", referral: "", needs: "", partnerType: "", website: "" };

const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63}){1,5}$/;
const DIGIT_RUN = /\d[\d\s-]{7,}\d/;
const LINK = /https?:\/\/|www\./i;

/** The first problem per field, mirroring the server so a person is told before sending. The server checks again. */
export function leadErrors(d: LeadDraft): Record<string, string> {
  const out: Record<string, string> = {};
  const text = (key: string, value: string, label: string, lo: number, hi: number, required: boolean) => {
    const v = value.trim();
    if (!v) {
      if (required) out[key] = `Enter ${label}.`;
      return;
    }
    if (v.length < lo || v.length > hi) out[key] = `${label[0]!.toUpperCase()}${label.slice(1)} must be ${lo} to ${hi} characters.`;
    else if (DIGIT_RUN.test(v)) out[key] = "Please do not include identity, document or account numbers.";
    else if (LINK.test(v)) out[key] = "Please do not include links.";
  };
  text("name", d.name, "your name", 2, 120, true);
  text("company", d.company, "your company", 2, 160, true);
  text("region", d.region, "a region", 2, 40, false);
  text("referral", d.referral, "where you heard of us", 2, 80, false);
  text("needs", d.needs, "your security needs", 2, 500, false);
  if (!EMAIL.test(d.email.trim())) out.email = "Enter a work email address.";
  if (!isCountry(cleanCode(d.country))) out.country = "Choose a country.";
  if (!d.volume) out.volume = "Choose an expected volume.";
  if (d.entities.trim() && !/^\d{1,5}$/.test(d.entities.trim())) out.entities = "Enter a whole number of legal entities, or leave blank.";
  else if (d.entities.trim() && (Number(d.entities) < 1 || Number(d.entities) > 10_000)) out.entities = "Enter 1 to 10,000.";
  if (d.kind === "PARTNER" && !d.partnerType) out.partnerType = "Choose the kind of partner.";
  return out;
}

export function buildLead(d: LeadDraft, capabilities: string[]): PublicLeadIn | null {
  if (Object.keys(leadErrors(d)).length || !d.volume) return null;
  return {
    kind: d.kind,
    contact_name: d.name.trim(),
    work_email: d.email.trim().toLowerCase(),
    company: d.company.trim(),
    country: cleanCode(d.country),
    region: d.region.trim() || null,
    capabilities: [...capabilities].sort(),
    volume_band: d.volume,
    entities: d.entities.trim() ? Number(d.entities) : null,
    referral_source: d.referral.trim() || null,
    security_needs: d.needs.trim() || null,
    partner_type: d.kind === "PARTNER" ? (d.partnerType as NonNullable<PublicLeadIn["partner_type"]>) : null,
    notice_version: NOTICE_VERSION,
    website: d.website,
  };
}

/** The only rule that may put a "Recommended" badge on a plan (P31): the lowest plan in which every service of the buyer's chosen use cases is included or optional. A scope that
 * needs a service every plan leaves to Sales, or no scope at all, recommends nothing. */
export function recommendedTier(page: PublicPricingPage, families: string[]): PublicPricingPage["tiers"][number]["code"] | null {
  if (families.length === 0) return null;
  for (const t of page.tiers) if (families.every((f) => t.family_state[f] === "included" || t.family_state[f] === "optional")) return t.code;
  return null;
}
