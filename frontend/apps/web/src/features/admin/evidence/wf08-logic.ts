import type { AuthoritySnapshot, ExportRequest, ExportTier, TrustBundleView } from "@zoiko/api-client";

/** Plain-language rules for the WF08 evidence screens (ZID-CP-WF08 v2.0, CR-170). Nothing here claims more than the platform proved. */

export const TIERS: { id: ExportTier; label: string; promise: string; needsRecipient: boolean }[] = [
  { id: "PUBLIC_MINIMAL", label: "Public proof", promise: "Proof the receipt is intact. The receipt itself is withheld. No recipient is recorded.", needsRecipient: false },
  { id: "CUSTOMER_EVIDENCE", label: "Customer evidence", promise: "The decision, without the internal handles behind it, for a named customer or person it concerns.", needsRecipient: true },
  { id: "AUDITOR_DOSSIER", label: "Auditor dossier", promise: "The full receipt for a named auditor, regulator or counsel. A second person must approve first.", needsRecipient: true },
];

export const RECIPIENT_KINDS: Record<ExportTier, { id: string; label: string }[]> = {
  PUBLIC_MINIMAL: [],
  CUSTOMER_EVIDENCE: [
    { id: "CUSTOMER", label: "Customer" },
    { id: "DATA_SUBJECT", label: "Person it concerns" },
  ],
  AUDITOR_DOSSIER: [
    { id: "AUDITOR", label: "Auditor" },
    { id: "REGULATOR", label: "Regulator" },
    { id: "COUNSEL", label: "Counsel" },
  ],
};

export const LEGAL_BASES = ["CONSENT", "CONTRACT", "LEGAL_OBLIGATION", "LEGITIMATE_INTEREST", "AUDIT_ENGAGEMENT", "COURT_OR_REGULATOR_ORDER"];
export const MAX_RETENTION: Record<ExportTier, number> = { PUBLIC_MINIMAL: 30, CUSTOMER_EVIDENCE: 90, AUDITOR_DOSSIER: 365 };

const words = (v: string): string => v.toLowerCase().replaceAll("_", " ");

/** What is still missing from an export request form, in the order a person fills it in. Empty means it can be sent. */
export function exportFormProblems(f: { tier: ExportTier; receiptId: string; recipientName: string; recipientKind: string; contact: string; purpose: string; legalBasis: string; country: string; retention: number }): string[] {
  const p: string[] = [];
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(f.receiptId.trim())) p.push("Enter a receipt ID.");
  if (!(f.retention >= 1 && f.retention <= MAX_RETENTION[f.tier])) p.push(`Keep it between 1 and ${MAX_RETENTION[f.tier]} days.`);
  if (f.tier === "PUBLIC_MINIMAL") return p;
  if (!f.recipientName.trim()) p.push("Name the recipient.");
  if (!f.recipientKind) p.push("Say what kind of recipient it is.");
  if (!f.contact.trim()) p.push("Give a contact for the recipient.");
  if (f.purpose.trim().length < 10) p.push("Say why, in at least a sentence.");
  if (!f.legalBasis) p.push("Choose the legal basis.");
  if (!/^[A-Za-z]{2}$/.test(f.country.trim())) p.push("Give the recipient's country as two letters.");
  return p;
}

/** What a person may do next with a request, and why not. The service enforces the same rules; this only keeps the buttons honest. */
export function exportNextStep(r: ExportRequest, me: string | null): { approve: boolean; issue: boolean; why: string } {
  const mine = me !== null && r.requested_by === me;
  if (r.state === "ISSUED") return { approve: false, issue: false, why: "Issued." };
  if (r.state === "REFUSED") return { approve: false, issue: false, why: "Refused." };
  if (r.state === "REQUESTED" && r.needs_second_person) {
    return { approve: !mine, issue: false, why: mine ? "A second person must decide this. You asked for it, so you cannot." : "Waiting for a second person's decision." };
  }
  return { approve: false, issue: true, why: r.state === "APPROVED" ? "Approved. Ready to issue." : "No second person is needed. Ready to issue." };
}

const SNAPSHOT_REASON: Record<string, string> = {
  MISSING_EDGE: "A required link in the chain is missing",
  CONTRADICTORY_EDGES: "Two records about the same thing disagree",
  EDGE_NOT_IN_EFFECT: "A record was not in effect at the time of the decision",
  EDGE_OBTAINED_AFTER_DECISION: "A record was only collected after the decision, so this is a reconstruction",
  EDGE_EVIDENCE_DIGEST_MISSING: "A record has no usable evidence behind it",
  EDGE_NOT_ACTIVE_AT_DECISION: "A record was not active at the time",
  EDGE_VERSION_INVALID: "A record has no valid version",
  EDGE_TIME_INVALID: "A record has an unreadable time",
  EDGE_INCOMPLETE: "A record is missing its source or issuer",
  EDGE_TYPE_UNKNOWN: "A record is of a kind this version does not know",
  TOO_MANY_EDGES: "Too many records to assess",
};
export function snapshotReason(code: string): string {
  const [head, ...rest] = code.split(":");
  const base = SNAPSHOT_REASON[head ?? ""] ?? code;
  return rest.length ? `${base} (${rest.join(" ")}).` : `${base}.`;
}

export function snapshotHeadline(s: Pick<AuthoritySnapshot, "verdict" | "integrity">): { tone: "ok" | "warn" | "danger"; text: string } {
  if (s.integrity === "INVALID") return { tone: "danger", text: "This record does not match its own signature. Do not rely on it." };
  if (s.verdict === "INDETERMINATE") return { tone: "warn", text: "Who held authority at that moment cannot be shown from the records. Nothing is assumed." };
  return { tone: "ok", text: "Every link in the chain is on record for that moment." };
}

/** The trust bundle as a reader needs it: each key's standing, and whether the bundle itself is still current. */
export function trustSummary(b: TrustBundleView["bundle"], now: Date): { current: boolean; keys: { kid: string; standing: string }[]; note: string } {
  const current = new Date(b.expires_at).getTime() > now.getTime();
  return {
    current,
    keys: b.keys.map((k) => ({ kid: k.kid, standing: k.state === "COMPROMISED" ? `compromised from ${k.compromised_from ?? "an unknown time"}: signatures from then on are void` : words(k.state) })),
    note: current ? "Current. Check again before it expires." : "Expired. Do not trust a key list that has expired.",
  };
}

export const CLAIMS: { claim: string; allowed: "yes" | "yes, with limits" | "not yet" | "no"; backing: string; missing: string }[] = [
  { claim: "Receipts and the ledger are tamper-evident", allowed: "yes, with limits", backing: "Hash chain, signed checkpoints, scheduled re-checks, tamper tests", missing: "An independent review" },
  { claim: "A receipt can be checked offline", allowed: "yes", backing: "Two verifiers, Python and TypeScript, agree on shared test cases", missing: "A third party running it" },
  { claim: "Independently verifiable by anyone", allowed: "not yet", backing: "Two implementations by the same team", missing: "One outside partner verifying with their own code" },
  { claim: "A signed decision proves it was right", allowed: "no", backing: "Every result says correctness is not assessed", missing: "Cannot be claimed" },
  { claim: "A timestamp proves existence at a time", allowed: "not yet", backing: "The checker exists", missing: "A contracted timestamp authority" },
  { claim: "Qualified, notarized, non-repudiable or court-admissible", allowed: "no", backing: "None", missing: "A counsel-approved profile for each country" },
  { claim: "Post-quantum secure", allowed: "no", backing: "The trust bundle says not offered", missing: "A designed, tested, approved suite" },
  { claim: "Revocation is reflected everywhere at once", allowed: "no", backing: "The gateway re-reads state on every call", missing: "Acknowledgement from other enforcement points" },
];
