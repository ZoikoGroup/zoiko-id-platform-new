/** The Command Center API (ZID-CPS-WF-02 section 08): governed aggregates only. Every figure carries its definition version and quality; a
 * withheld figure (`suppressed`) has no value at all, never a zero. */

export type Quality = "OBSERVED" | "PROVISIONAL" | "NO_DATA" | "NO_PRICE" | "SEE_OPERATIONS" | string;

export type Governed = {
  metric_id: string;
  name: string;
  definition_version: string;
  unit: "count" | "ratio" | "seconds" | "money" | string;
  quality: Quality;
  suppressed: boolean;
  value: number | null;
  numerator?: number | null;
  denominator?: number | null;
  p95?: number | null;
  [extra: string]: unknown;
};

export type Row = Record<string, string | number | boolean | null> & { suppressed?: boolean };

export type Scope = {
  environment: string;
  market: string | null;
  from: string;
  to: string;
  timezone: string;
  watermark_utc: string | null;
  min_group: number;
  data_label: "SANDBOX" | "OBSERVED";
};

export type Freshness = {
  source: string;
  last_event_at: string;
  last_ingested_at: string;
  lag_seconds: number;
  stale: boolean;
  accepted: number;
  late: number;
  rejected: number;
};

export type Overview = {
  scope: Scope;
  quality: Quality;
  freshness: Freshness[];
  tiles: Governed[];
  decisions: Row[];
  review: Governed;
  query_fingerprint: string;
};

export type QueryResult = {
  scope: Scope;
  quality: Quality;
  results: Governed[];
  breakdowns: Record<string, Row[]>;
  funnel: { cohort: string; matured_only: boolean; steps: Row[]; drop_off: Row[] };
  first_attempt_causes: Row[];
  query_fingerprint: string;
  snapshot_id?: string;
};

export type QueryIn = {
  metric_ids: string[];
  dimension?: "market" | "method" | "environment" | "day";
  cohort?: "created" | "started";
  from?: string;
  to?: string;
  tz?: string;
  market?: string;
  snapshot?: boolean;
};

export type Window = { from?: string; to?: string; tz?: string; market?: string };

export type Definition = {
  metric_id: string;
  name: string;
  version: string;
  owner: string;
  unit: string;
  numerator: string;
  denominator: string;
  population: string;
  exclusions: string[];
  sources: string[];
  status: string;
  finance: boolean;
};

export type Access = { permissions: string[]; finance: boolean; bundles: Record<string, string[]>; catalogue: Record<string, string> };

export type Operations = {
  availability: Governed & { window_days: number };
  slos: { service_id: string; name: string; owner: string; slos: { slo_id: string; sli: string; target: number }[] }[];
  incidents: { incident_id: string; severity: string; status: string; services: string[]; declared_at: string; resolved_at: string | null; title: string; updates: { at: string; note: string }[] }[];
  freshness: { quality: Quality; sources: Freshness[] };
};

export type Economics = {
  approved_subjects: number | null;
  billable_units: { meter: string; month: string; net?: string; frozen?: boolean };
  finance_visible: boolean;
  M13?: (Governed & { currency?: string; cost_total?: string; note?: string })[];
  M14?: Governed & { note?: string };
};

export type Coverage = { markets: { market: string; policy_version: string; routes: { route: string; kind: string }[]; face_comparison_authorized: boolean; review_sla_hours: number | null }[] };

export type Correction = { correction_id: string; event_id: string; reason: string; requested_by: string; requested_at: string; approved_by: string | null; approved_at: string | null };

export type ExportJob = { export_id: string; snapshot_id: string; format: string; purpose: string; status: string; requested_by: string; created_at: string; expires_at: string };

export type ExportCreated = { export_id: string; status: string; expires_at: string; download_token: string; format: string };

export type Grant = { grant_id: string; grantee: string; bundle: string; permissions: string[]; granted_at: string; expires_at: string | null };

export type Timeline = { session_ref: string; timeline: ({ event: string; at: string } & Record<string, unknown>)[] };
