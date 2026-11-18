/** ZID-DTV-WF-05 verification checks (CR-112 to CR-116): cases, contacts, address, proof of address, postal codes, device, evidence. */

export type CheckMethod =
  | "EMAIL_CHALLENGE"
  | "PHONE_SMS_CHALLENGE"
  | "PHONE_VOICE_CHALLENGE"
  | "CARRIER_INTELLIGENCE"
  | "ADDRESS_LOOKUP"
  | "ADDRESS_DOCUMENT"
  | "ADDRESS_DATA_SOURCE"
  | "ADDRESS_POSTAL_CHALLENGE"
  | "DEVICE_RISK";

export const CHECK_METHODS: readonly CheckMethod[] = [
  "EMAIL_CHALLENGE",
  "PHONE_SMS_CHALLENGE",
  "PHONE_VOICE_CHALLENGE",
  "CARRIER_INTELLIGENCE",
  "ADDRESS_LOOKUP",
  "ADDRESS_DOCUMENT",
  "ADDRESS_DATA_SOURCE",
  "ADDRESS_POSTAL_CHALLENGE",
  "DEVICE_RISK",
];

export interface CheckMethodAnswer {
  method: CheckMethod;
  available: boolean;
  state: string;
  reasons: string[];
  route: string | null;
  engine: string | null;
  produces: string[];
  scope_ref: string | null;
}

export interface CheckEligibility {
  country: string;
  purpose: string;
  available_methods: CheckMethod[];
  methods: CheckMethodAnswer[];
  policy_hash: string;
}

export interface CheckCase {
  case_id: string;
  status: "CREATED" | "ELIGIBLE" | "IN_PROGRESS" | "REVIEW" | "COMPLETE" | "CLOSED";
  subject_ref: string;
  purpose: string;
  market: string;
  region: string;
  requested_methods: CheckMethod[];
  available_methods: CheckMethod[];
  unavailable_reasons: string[];
  policy_hash: string;
  message: string | null;
  created_at: string;
}

export interface CheckCaseIn {
  subject_ref: string;
  purpose: string;
  market: string;
  methods: CheckMethod[];
}

export interface CheckRequirements {
  case_id: string;
  status: CheckCase["status"];
  methods: (CheckMethodAnswer & { available: boolean })[];
  usable_methods: CheckMethod[];
  message: string | null;
}

export interface CheckAssertion {
  type: string;
  outcome: string;
  method: CheckMethod;
  source: string | null;
  reason_codes: string[];
  observed_at: string;
  expires_at: string | null;
  earlier_records: number;
  proves: string;
  does_not_prove: string;
}

export interface CheckEvidence {
  case_id: string;
  status: CheckCase["status"];
  market: string;
  region: string;
  policy_hash: string;
  assertions: CheckAssertion[];
  not_yet_shown: string[];
  note: string;
}

export interface CheckContact {
  contact_id: string;
  case_id: string;
  type: "EMAIL" | "PHONE";
  revision: number;
  masked: string;
  unusual_number: boolean | null;
  note: string;
}

export interface CheckChallenge {
  challenge_id: string;
  status: string;
  channel: "EMAIL" | "SMS" | "VOICE";
  masked_target: string | null;
  expires_at: string;
  attempts_left: number;
  sends_left: number;
  assertion?: string;
}

export interface CheckAddressIn {
  line1: string;
  line2?: string;
  city: string;
  region?: string;
  postal_code?: string;
  country: string;
}

export interface CheckAddressResult {
  address_id: string;
  revision: number;
  country: string;
  postcode: "OK" | "BAD_SHAPE" | "NOT_CHECKED";
  format_outcome: string | null;
  location_outcome: string | null;
  source_outcome: string | null;
  note: string;
}

export interface CheckDocumentIn {
  upload_ref: string;
  doc_type: string;
  issuer: string;
  issued_on: string;
  name_on_document: string;
  address_on_document: CheckAddressIn;
  subject_name: string;
}

export interface CheckDocument {
  document_id: string;
  doc_type: string;
  assessment: "SUPPORTS" | "REVIEW" | "DOES_NOT_SUPPORT";
  findings: string[];
  policy_status: string;
  status: "AWAITING_REVIEW" | "AWAITING_CHECKER" | "DONE";
  created_at: string;
}

export interface CheckPostal {
  challenge_id: string;
  status: string;
  expires_at: string;
  note?: string;
  verified?: boolean;
  attempts_left?: number;
}

/** Operator plane: a check method scope (same lifecycle as biometric methods). */
export interface CheckMethodScope {
  method: CheckMethod;
  engine_id: string;
  engine_version: string;
  country: string;
  purpose: string;
  pilot_tenant: string | null;
}

export interface CheckMethodVersion {
  scope_id: string;
  version: number;
  state: "OFF" | "PAUSED" | "LAB_ONLY" | "PILOT_GATED" | "PRODUCTION_ELIGIBLE";
  scope: CheckMethodScope;
  dpia_ref: string | null;
  evaluation_ref: string | null;
  reason: string;
  evidence_ref: string;
  recorded_by: string;
  effective_at: string;
  exposure_percent: number;
}

export interface CheckMethodProposal {
  proposal_id: string;
  scope_id: string;
  scope: CheckMethodScope;
  target: "LAB_ONLY" | "PILOT_GATED" | "PRODUCTION_ELIGIBLE";
  dpia_ref: string | null;
  evaluation_ref: string | null;
  reason: string;
  proposed_by: string;
  proposed_at: string;
}

export interface CheckMethodsList {
  items: { current: CheckMethodVersion; versions: CheckMethodVersion[] }[];
  open_proposals: CheckMethodProposal[];
}

export interface CheckMethodProposeIn {
  scope_id?: string;
  scope: Omit<CheckMethodScope, "pilot_tenant"> & { pilot_tenant?: string };
  target: CheckMethodProposal["target"];
  dpia_ref?: string;
  evaluation_ref?: string;
  reason: string;
  evidence_ref: string;
}
