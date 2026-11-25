/** What the public Trust Center feeds carry (ZID-CP-WF12; CR-174). */
export type PublicClaim = {
  claim_id: string; text: string; locale: string; scope: { entity_id: string; service: string; markets: string[]; environment: string };
  effective_as_of: string; valid_until: string | null; channels: string[];
  evidence: { scheme: string; type: string; issuer: string; period_end: string; held_by: string; restricted: boolean }[];
};
export type ClaimsFeed = { as_of: string; items: PublicClaim[]; note: string };
export type CertRow = {
  scheme: string; kind: string; issuer: string; assessor: string | null; issued_to: string; is_zoiko: boolean; legal_entity: string; products: string[]; regions: string[]; exclusions: string[];
  period_start: string; period_end: string; state: string; full_report: string | null;
};
export type CertFeed = { as_of: string; current: CertRow[]; history: CertRow[]; note: string };
export type PrivacyFeed = {
  as_of: string; market: string | null; empty: Record<string, string>;
  subprocessor: Record<string, unknown>[]; retention: Record<string, unknown>[]; biometric: Record<string, unknown>[]; transfer: Record<string, unknown>[];
};
export type StatusRow = { service: string; region: string | null; window: string | null; state: string; percent: number | null; note: string };
export type StatusFeed = { as_of: string; services: StatusRow[]; definition: string; sla: string };
export type AdvisoryVersion = { seq: number; kind: string; title: string; body: string; correction_reason: string | null; created_at: string };
export type AdvisoryFeed = { items: { advisory_id: string; versions: AdvisoryVersion[]; latest_title: string }[] };

export const KIND_LABEL: Record<string, string> = { subprocessor: "Subprocessors", retention: "Retention", biometric: "Biometrics", transfer: "Transfers" };

/** The words for a measured figure. An unmeasured service never reads as 100%. */
export function availabilityText(r: Pick<StatusRow, "state" | "percent">): string {
  return r.state === "MEASURED" && r.percent !== null ? `${r.percent}% available` : "Not yet reported";
}

export function fieldLabel(k: string): string {
  return k.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

export function valueText(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (v === null || v === undefined) return "";
  return String(v);
}
