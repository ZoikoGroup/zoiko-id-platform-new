/** The company-verification pack as a form instead of pasted JSON: every choice is a dropdown, a tick or a short text, and `buildPack` turns the form into exactly the object the platform's `policy_from_dict` reads.
 * The platform still checks the pack and names everything incomplete; nothing here makes a pack valid, it only stops a typing mistake from being the reason it is not. */

export const DAY = 86_400;

export const MARKETS: { id: string; label: string }[] = [
  { id: "GB", label: "United Kingdom (GB)" },
  { id: "IE", label: "Ireland (IE)" },
  { id: "FR", label: "France (FR)" },
  { id: "DE", label: "Germany (DE)" },
  { id: "US", label: "United States (US)" },
  { id: "IN", label: "India (IN)" },
  { id: "NL", label: "Netherlands (NL)" },
  { id: "ES", label: "Spain (ES)" },
  { id: "IT", label: "Italy (IT)" },
  { id: "AU", label: "Australia (AU)" },
  { id: "CA", label: "Canada (CA)" },
  { id: "SG", label: "Singapore (SG)" },
];

/** The company's legal form as the sources report it, upper case. "Any" is the pack that applies when a company states none. */
export const LEGAL_FORMS: { id: string; label: string }[] = [
  { id: "", label: "Any, or not stated" },
  { id: "LTD", label: "Private limited company (LTD)" },
  { id: "PLC", label: "Public limited company (PLC)" },
  { id: "LLP", label: "Limited liability partnership (LLP)" },
];
export const RISK_TIERS = ["LOW", "MEDIUM", "HIGH"] as const;

export type SourceChoice = {
  adapter: string;
  label: string;
  tier: number;
  credentialRequired: boolean;
  help: string;
};
/** The sources the platform has adapters for. A tier says how authoritative it is: 1 is an official live register, 4 a licensed commercial supplier. */
export const SOURCES: SourceChoice[] = [
  { adapter: "companies-house", label: "Companies House (UK official register)", tier: 1, credentialRequired: true, help: "Needs an API key in the key store." },
  { adapter: "sirene", label: "Sirene (French official register)", tier: 1, credentialRequired: false, help: "Open, no key." },
  { adapter: "gleif", label: "GLEIF (LEI reference records)", tier: 3, credentialRequired: false, help: "Open, no key. A reference, not a register." },
  { adapter: "creditsafe", label: "Creditsafe (licensed supplier)", tier: 4, credentialRequired: true, help: "Paid; needs a contract and a key." },
];

export type DocumentChoice = { docClass: string; label: string; claim: string; help: string; defaultAgeDays: number | null };
export const DOCUMENTS: DocumentChoice[] = [
  { docClass: "CERTIFICATE_OF_INCORPORATION", label: "Certificate of incorporation", claim: "LEGAL_EXISTENCE_HISTORICAL", help: "Proves the company was formed. It does not age.", defaultAgeDays: null },
  { docClass: "REGISTER_EXTRACT", label: "Register extract", claim: "CURRENT_STATUS", help: "Proves the company is active now, so it goes stale.", defaultAgeDays: 90 },
  { docClass: "LETTER_OF_AUTHORITY", label: "Letter of authority", claim: "REPRESENTATIVE_AUTHORITY", help: "Proves a person may act for the company.", defaultAgeDays: 180 },
  { docClass: "OWNERSHIP_CHART", label: "Ownership chart", claim: "OWNERSHIP_STRUCTURE", help: "Shows who owns or controls the company.", defaultAgeDays: 180 },
];
export const AGE_OPTIONS: { id: string; label: string }[] = [
  { id: "", label: "No age limit" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
  { id: "180", label: "180 days" },
  { id: "365", label: "1 year" },
];

export const RECORD_CLASSES: { id: string; label: string }[] = [
  { id: "RAW_DOCUMENTS", label: "Uploaded documents" },
  { id: "EXTRACTED_DATA", label: "Data read from documents" },
  { id: "DECISION_METADATA", label: "Decisions and reasons" },
  { id: "SCREENING_EVIDENCE", label: "Screening evidence" },
];
export const RETENTION_OPTIONS = [30, 90, 180, 365, 730, 1825, 2555].map((d) => ({ id: String(d), label: d < 365 ? `${d} days` : d === 365 ? "1 year" : `${Math.round(d / 365)} years` }));
export const UBO_THRESHOLDS = ["10", "20", "25", "50"].map((p) => ({ id: p, label: `${p}%` }));
export const RECHECK_K1 = [{ id: "90", label: "90 days" }, { id: "180", label: "180 days (ceiling)" }];
export const RECHECK_K2 = [{ id: "180", label: "180 days" }, { id: "365", label: "1 year (ceiling)" }];

export type SourceForm = { on: boolean; legalBasis: string; credentialRef: string; terms: string };
export type DocumentForm = { on: boolean; ageDays: string };
export type PackForm = {
  jurisdiction: string;
  legalForm: string;
  riskTier: string;
  version: string;
  sources: Record<string, SourceForm>;
  documents: Record<string, DocumentForm>;
  reviewers: string; // "2" | "3"
  uboPercent: string;
  uboOtherControl: boolean;
  uboSignedBy: string;
  retentionDays: Record<string, string>;
  retentionBasis: string;
  legalHold: boolean;
  deletionJob: string;
  recheckK1: string;
  recheckK2: string;
};

/** The purpose the platform looks packs up by (config/kyb/market-defaults.yaml). A pack for another purpose is never found by a claim. */
export const PURPOSE = "ORG_ONBOARDING";

export function defaultForm(): PackForm {
  return {
    jurisdiction: "GB",
    legalForm: "",
    riskTier: "LOW",
    version: "1",
    sources: Object.fromEntries(SOURCES.map((s) => [s.adapter, { on: s.adapter === "gleif", legalBasis: "", credentialRef: "", terms: "" }])),
    documents: Object.fromEntries(DOCUMENTS.map((d) => [d.docClass, { on: d.docClass === "CERTIFICATE_OF_INCORPORATION", ageDays: d.defaultAgeDays === null ? "" : String(d.defaultAgeDays) }])),
    reviewers: "2",
    uboPercent: "25",
    uboOtherControl: true,
    uboSignedBy: "",
    retentionDays: Object.fromEntries(RECORD_CLASSES.map((r) => [r.id, "365"])),
    retentionBasis: "",
    legalHold: true,
    deletionJob: "",
    recheckK1: "180",
    recheckK2: "365",
  };
}

const text = (v: string): string | null => (v.trim() ? v.trim() : null);

/** The form as the pack object the platform reads. Empty text becomes null (the platform then names it as missing), never an invented value. */
export function buildPack(f: PackForm): Record<string, unknown> {
  return {
    jurisdiction: f.jurisdiction,
    region: null,
    legal_form: f.legalForm,
    purpose: PURPOSE,
    risk_tier: f.riskTier,
    version: f.version.trim() || "1",
    sources: SOURCES.filter((s) => f.sources[s.adapter]?.on).map((s) => {
      const v = f.sources[s.adapter]!;
      return { adapter: s.adapter, tier: s.tier, legal_basis: text(v.legalBasis), credential_ref: text(v.credentialRef), credential_required: s.credentialRequired, terms_version: text(v.terms) };
    }),
    documents: DOCUMENTS.filter((d) => f.documents[d.docClass]?.on).map((d) => {
      const age = f.documents[d.docClass]!.ageDays;
      return { doc_class: d.docClass, claim: d.claim, max_age_s: age ? Number(age) * DAY : null };
    }),
    ubo: { threshold_percent: f.uboPercent, other_control_tests: f.uboOtherControl, signed_by: text(f.uboSignedBy) },
    reviewers_for_document_first: Number(f.reviewers),
    recheck_days: { K1: Number(f.recheckK1), K2: Number(f.recheckK2) },
    recheck_ratified: false,
    retention: {
      days: Object.fromEntries(RECORD_CLASSES.map((r) => [r.id, Number(f.retentionDays[r.id] ?? "365")])),
      legal_basis: text(f.retentionBasis),
      legal_hold_supported: f.legalHold,
      deletion_job_ref: text(f.deletionJob),
    },
  };
}

/** What the person still has to fill in before the platform will call the pack complete: the same gaps the platform names, said before it is stored. */
export function gaps(f: PackForm): string[] {
  const out: string[] = [];
  const picked = SOURCES.filter((s) => f.sources[s.adapter]?.on);
  if (!picked.length && !DOCUMENTS.some((d) => f.documents[d.docClass]?.on)) out.push("Pick at least one source or one document.");
  for (const s of picked) {
    const v = f.sources[s.adapter]!;
    if (!v.legalBasis.trim()) out.push(`${s.label}: the reference of its lawful-use record.`);
    if (s.credentialRequired && !v.credentialRef.trim()) out.push(`${s.label}: the name of its key in the key store.`);
    if (!v.terms.trim()) out.push(`${s.label}: the version of its terms of use.`);
  }
  if (DOCUMENTS.some((d) => f.documents[d.docClass]?.on) && Number(f.reviewers) < 2) out.push("Document-first approval needs two reviewers.");
  if (!f.uboSignedBy.trim()) out.push("The ownership threshold needs the Compliance owner who signs it.");
  if (!f.uboOtherControl) out.push("Control by other means must be assessed.");
  if (!f.retentionBasis.trim()) out.push("The retention schedule needs its legal basis.");
  if (!f.legalHold) out.push("The retention schedule must support legal holds.");
  if (!f.deletionJob.trim()) out.push("Name the deletion job.");
  return out;
}
