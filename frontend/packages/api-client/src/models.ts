/** Response models for the cell API.
 *
 * The published OpenAPI contracts type every 2xx body as a free-form object, so these shapes are
 * transcribed from the route handlers in backend/app/src/zid_platform/modules/*\/api/*.py. Keep
 * them in step with those handlers; fields the UI does not rely on are left out. */

/* ---------------------------------------------------------------- shared */

export type Items<T> = { items: T[] };
/** A list that can be continued: `next_cursor` is absent or null on the last page. */
export type Page<T> = Items<T> & { next_cursor?: string | null };
export type Iso = string;
export type RiskClassNumber = 0 | 1 | 2 | 3;
export type AssuranceLevel = "IAL-Z1" | "IAL-Z2" | "IAL-Z3";
export type Algorithm = "ES256" | "Ed25519" | "RS256" | "PS256";

/* ---------------------------------------------------------------- sessions & authenticators */

export type SessionStatus = "ACTIVE" | "REAUTH_REQUIRED" | "REVOKED" | "EXPIRED";
export type SessionView = {
  session_id: string;
  status: SessionStatus;
  aal: string | number | null;
  authentication_context: Record<string, unknown> | string | null;
  auth_time: Iso | null;
  expires_at: Iso | null;
  last_seen_at: Iso | null;
};

export type AuthenticatorStatus = "PENDING_REGISTRATION" | "ACTIVE" | "SUSPENDED" | "REVOKED" | "LOST" | "REPLACED";
export type AuthenticatorView = {
  authenticator_id: string;
  authenticator_type: string;
  status: AuthenticatorStatus;
  label: string | null;
  backup_eligible: boolean;
  registered_at: Iso | null;
  last_used_at: Iso | null;
};

/** Result of passkey / federation sign-in (the cell also sets its session cookie). */
export type SignInResult = {
  session_id: string;
  principal_id: string;
  aal: string | number;
  amr?: string[];
  auth_time: Iso;
  expires_at: Iso;
  step_up_recommended?: boolean;
};

export type RegisteredAuthenticator = {
  authenticator_id: string;
  authenticator_type: string;
  backup_eligible: boolean;
  status: AuthenticatorStatus;
};

export type RecoveryClass = "RC-1" | "RC-2" | "RC-3" | "RC-4";
export type RecoveryStatus = "PENDING_REPROOF" | "PENDING_APPROVAL" | "COOLING_OFF" | "READY" | "DENIED";
export type RecoveryProgress = {
  recovery_id: string;
  recovery_class: RecoveryClass;
  status: RecoveryStatus;
  missing: string[];
  /** Present once, on start; required to continue. */
  recovery_handle?: string;
  enrollment_ticket?: string;
  cooling_off_until?: Iso | null;
};

export type RecoveryCodes = { codes: string[]; note: string };

export type RecoveryPolicy = {
  principal_id: string;
  recovery_class: RecoveryClass | null;
  approvers?: string[];
  k?: number;
  cooling_off_hours?: number;
  revision?: number;
};

/* ---------------------------------------------------------------- oauth */

export type ConsentRequest = { client_id: string; scopes: string[]; expires_at: Iso };
export type ConsentGrant = { client_id: string; scopes: string[]; granted_at: Iso };
export type UserInfo = { sub: string; tenant_id: string };

/* ---------------------------------------------------------------- federation */

export type FederationProtocol = "OIDC" | "SAML";
export type FederationIntegration = {
  connection_id: string;
  protocol: FederationProtocol;
  display_name: string;
  issuer: string;
  status: "ACTIVE" | "QUARANTINED" | string;
  config_revision: number;
  metadata_expires_at: Iso | null;
  link_policy: "EXISTING_ONLY" | "JIT";
  allow_idp_initiated: boolean;
  idp_initiated_targets: string[];
  require_encrypted_assertions: boolean;
  trust_amr: boolean;
  acr_levels: Record<string, number>;
  algorithms: string[];
  /** OIDC only. Secret and key references are names in the secret store, never values. */
  client_id?: string | null;
  client_auth?: "private_key_jwt" | "client_secret_basic" | null;
  client_secret_ref?: string | null;
  scopes?: string[];
  authorization_endpoint?: string | null;
  token_endpoint?: string | null;
  pinned_keys?: number;
  trust_anchor_id?: string | null;
  federation_entity_id?: string | null;
  /** SAML only. */
  sso_url?: string | null;
  sp_entity_id?: string | null;
  pinned_certificates?: number;
  sp_decryption_key_ref?: string | null;
  /** SAML: the address the provider's administrator enters (list, create); with IdP-initiated SSO on, the connection-specific one. */
  acs_url?: string;
};
export type TrustAnchor = { anchor_id: string; entity_id: string; status: string };

/* ---------------------------------------------------------------- verification */

export type VerificationStatus =
  | "CREATED"
  | "IN_PROGRESS"
  | "PENDING_EXTERNAL"
  | "RESUBMISSION_REQUIRED"
  | "MANUAL_REVIEW"
  | "VERIFIED"
  | "FAILED"
  | "CANCELED"
  | "EXPIRED";
export type VerificationSession = {
  verification_id: string;
  status: VerificationStatus;
  outcome: string | null;
  profile_id?: string;
  subject_principal_id?: string;
  purpose?: string;
  jurisdiction?: string;
  adapter_id?: string | null;
  expires_at?: Iso | null;
  supersedes?: string;
};
export type VerificationAssertion = {
  assertion_id: string;
  claim_type: string;
  verification_method: string;
  evidence_types: string[];
  assurance_level: string;
  status: string;
  verified_at: Iso | null;
  fresh_until: Iso | null;
  reason_codes: string[];
};
export type Assurance = {
  principal_id: string;
  identity_proofing?: { level: string | null; claims?: Record<string, unknown> };
  [k: string]: unknown;
};
export type VerificationProfile = {
  profile_id: string;
  name: string;
  version: number;
  level: AssuranceLevel;
  status: "DRAFT" | "ACTIVE" | string;
  jurisdictions?: string[];
};
export type BulkVerificationIn = { profile_id: string; subject_principal_ids: string[]; purpose: string; jurisdiction: string };
export type BulkVerificationItem = { subject_principal_id: string; started: boolean; code?: string; verification_id?: string; status?: string; handoff?: unknown };
export type BulkVerificationResult = { requested: number; started: number; failed: number; items: BulkVerificationItem[] };
export type VerificationAdapter = { adapter_id: string; status: string; callback_path?: string };
export type RelianceDecision = { reliance_decision_id: string; outcome: string; reason_codes: string[]; assertion_ids: string[] };
export type ReviewOutcome = { case_id: string; outcome: string };

/* ---------------------------------------------------------------- credentials */

export type CredentialFormat = "SD_JWT_VC" | "MDOC" | "VC_JWT";
export type CredentialConfiguration = { configuration_id: string; status: "DRAFT" | "ACTIVE" | string; adapter_version?: string };
export type IssuerTrust = { trust_id?: string; issuer_identifier: string; status: string };
export type RpRegistration = { registration_id: string; client_id: string; purpose: string };
export type CredentialStatusChange = { credential_id: string; status: "REVOKED" | "SUSPENDED" | "VALID" };
export type Presentation = {
  presentation_id: string;
  status: string;
  reason_codes?: string[];
  issuer?: string | null;
  credential_status?: string | null;
  claims?: Record<string, unknown> | null;
  request_uri?: string;
  [k: string]: unknown;
};

/** POST /presentations: the OpenID4VP request to hand to a wallet (status is read separately). */
export type PresentationCreated = {
  presentation_id: string;
  request_uri: string;
  /** `openid4vp://?client_id=…&request_uri=…` */
  authorization_request: string;
  claims: string[];
  expires_at: Iso;
};

/* ---------------------------------------------------------------- authority & mandates */

export type GrantType =
  | "PERSONAL"
  | "ROLE_ISSUED"
  | "ORGANIZATION_DIRECT"
  | "SERVICE_DELEGATION"
  | "AGENT_DELEGATION"
  | "EXTERNAL_DELEGATION";
export type ContinuityMode = "PERSON_BOUND" | "ROLE_CONTINUOUS" | "ORGANIZATION_CONTINUOUS" | "CONTRACT_BOUND";
export type GrantStatus =
  | "DRAFT"
  | "PENDING_APPROVAL"
  | "ACTIVE"
  | "REVALIDATION_PENDING"
  | "SUSPENDED"
  | "REVOKED"
  | "EXPIRED"
  | "SUPERSEDED";
export type Money = { amount: string; currency: string };
export type AuthorityGrant = {
  authority_grant_id: string;
  status: GrantStatus;
  grant_type: GrantType;
  continuity_mode: ContinuityMode;
  grantor_principal_id: string;
  grantee_principal_id: string;
  represented_principal_id: string;
  authority_root_assertion_id: string | null;
  parent_grant_id: string | null;
  /** null = any action / resource. */
  actions: string[] | null;
  resources: string[] | null;
  value_limit: Money | null;
  valid_from: Iso | null;
  valid_to: Iso | null;
  delegation: { allowed: boolean; max_depth: number };
  revision: number;
};
export type GrantChain = { valid: boolean; chain_ids: string[]; authority_root_assertion_id: string | null; codes: string[] };

export type ApprovalMode = "SINGLE" | "ALL_OF" | "K_OF_N" | "ROLE_PAIR" | "EXTERNAL_CONFIRMATION";
export type ApprovalState = "PENDING" | "APPROVED" | "DENIED" | "EXPIRED" | "STALE";
export type ApprovalCreated = { approval_request_id: string; state: ApprovalState; expires_at: Iso };
export type ApprovalView = { approval_request_id: string; state: ApprovalState; codes: string[]; counted_approvers: string[] | number };
export type ApprovalDecisionResult = ApprovalView & { authority_valid: boolean };

export type MandateStatus = "PENDING_APPROVAL" | "ACTIVE" | "PAUSED" | "EXHAUSTED" | "COMPLETED" | "EXPIRED" | "REVOKED";
export type LedgerAmounts = { value?: string | number | null; count?: number | null; data_bytes?: number | null };
export type MandateBalance = {
  mandate_id: string;
  status: MandateStatus;
  currency: string | null;
  committed: LedgerAmounts;
  reserved: LedgerAmounts;
  remaining: LedgerAmounts;
  ledger_version: number;
};
export type ReservationStatus = "RESERVED" | "COMMITTED" | "RELEASED" | "EXPIRED" | "RECONCILIATION_REQUIRED";
export type Reservation = {
  /** Not echoed by the cell's reservation view. */
  mandate_id?: string;
  reservation_id: string;
  status: ReservationStatus;
  expires_at: Iso | null;
  amounts: LedgerAmounts;
};

/* ---------------------------------------------------------------- decisions */

export type DecisionOutcome = "ALLOW" | "DENY" | "STEP_UP" | "APPROVAL_REQUIRED" | "STATE_UNAVAILABLE";
/** For a request the cell cannot evaluate (e.g. an uncatalogued action) only `decision_id` and
 * `reason_codes` are present. */
export type EvaluationResult = {
  decision: boolean;
  context: {
    decision_id: string;
    reason_codes: string[];
    outcome?: DecisionOutcome;
    obligations?: unknown[];
    risk_class?: string;
    policy_release_id?: string | null;
    relation_revision?: number | null;
    security_revision?: number | null;
    valid_until?: Iso | null;
  };
};

/* ---------------------------------------------------------------- signals */

export type SecurityStateValue =
  | "TRUSTED"
  | "ELEVATED_RISK"
  | "STEP_UP_REQUIRED"
  | "REAUTH_REQUIRED"
  | "REPROOF_REQUIRED"
  | "RESTRICTED"
  | "SUSPENDED"
  | "COMPROMISED"
  | "REVOKED"
  | "PURGED";
export type SubjectType = "PRINCIPAL" | "SESSION" | "CREDENTIAL";
export type SecurityState = {
  subject_type: SubjectType;
  subject_id: string;
  state: SecurityStateValue;
  revision: number;
  effective_at?: Iso | null;
  committed_at?: Iso | null;
};
export type SignalAccepted = { signal_id: string; status: string; state: SecurityStateValue; revision: number };
export type SsfStream = {
  stream_id: string;
  stream_ref: string;
  direction: "RECEIVER" | "TRANSMITTER";
  issuer: string | null;
  audience: string;
  events: string[];
  subject_formats: string[];
  delivery_method: string;
  source_trust: "INFORMATIONAL" | "TRUSTED" | "AUTHORITATIVE";
  status: "PENDING" | "ACTIVE" | "QUARANTINED" | string;
  metadata_revision: number;
  /** Shown once on creation. */
  push_authorization?: string;
};
export type StreamPropagation = {
  stream_id: string;
  acknowledged: number;
  pending: number;
  p50_ms: number | null;
  p99_ms: number | null;
  max_ms: number | null;
  oldest_pending_ms: number | null;
  slo_breaches: number;
  /** Restricting (R2/R3) events the receiver has not acknowledged inside the target; the receiver must fail closed. */
  overdue_restricting?: number;
  remote_fail_closed?: boolean;
  /** Propagation SLO per risk class (ms). */
  slo_ms_by_risk: Record<string, number>;
  [k: string]: unknown;
};

/* ---------------------------------------------------------------- provisioning */

export type ProvisioningSource = { source_id: string; client_id: string; display_name: string; status: string };
/** Returned once by register: the connector's operator configures these; `client_created` says the cell made the OAuth client from the pasted public keys. */
/** One connected company directory: the provider's own addresses and ids, the state, and how the last run went (a code and counts, never provider text). */
export type DirectorySync = {
  sync_id: string;
  source_id: string;
  provider: "AUTH0";
  display_name: string;
  settings: Record<string, string>;
  status: "ACTIVE" | "PAUSED";
  interval_seconds: number;
  next_run_at: Iso;
  last_run_at: Iso | null;
  last_outcome: "OK" | "FAILED" | null;
  last_error: string | null;
  last_counts: Record<string, number>;
  consecutive_failures: number;
};

export type ProvisioningSourceCreated = ProvisioningSource & { client_created: boolean; scim_base_url: string; token_endpoint: string };

/* ---------------------------------------------------------------- admin lists (CR-010) */

/** A company's ask for a different plan; the commercial team answers it. It never changes the entitlement. */
export type PlanRequestStatus = "OPEN" | "ACCEPTED" | "DECLINED" | "WITHDRAWN";
export type PlanRequest = {
  request_id: string;
  plan: string;
  current_plan: string;
  note: string | null;
  status: PlanRequestStatus;
  created_at: Iso;
  decided_at: Iso | null;
  decision_note: string | null;
};

export type ApprovalListItem = {
  approval_request_id: string;
  subject_ref: string;
  requester_principal_id: string;
  mode: ApprovalMode;
  roles: string[];
  state: ApprovalState;
  codes: string[];
  required_approvals: number;
  approvals_received: number;
  /** What the request is for, in words (requests opened the older way have none). */
  summary: string | null;
  action: string | null;
  resource: string | null;
  change: string | null;
  created_at: Iso;
  expires_at: Iso;
};
/** A request the signed-in person may approve now, with the grant the server will act under. */
export type ApprovalAwaiting = ApprovalListItem & { authority_grant_id: string };

export type Mandate = {
  mandate_id: string;
  status: MandateStatus;
  authority_grant_id: string;
  executor_principal_id: string;
  represented_principal_id: string;
  purpose_id: string;
  allowed_actions: string[];
  resource_scopes: string[];
  tools: string[];
  jurisdictions: string[];
  currency: string | null;
  value_limit: string | null;
  per_transaction_value_limit: string | null;
  count_limit: number | null;
  data_limit_bytes: number | null;
  valid_from: Iso;
  valid_to: Iso;
  delegation_allowed: boolean;
  max_delegation_depth: number;
  parent_mandate_id: string | null;
  created_at: Iso;
};

/** A mandate as its own people see it (`GET /v1/mandates/mine`): what they are to it and what they may do to it now. */
export type MyMandate = Mandate & {
  i_am: ("executor" | "creator")[];
  my_moves: ("pause" | "revoke")[];
};

export type IssuedCredentialStatus = "VALID" | "SUSPENDED" | "REVOKED" | "EXPIRED" | "UNKNOWN";
export type IssuedCredential = {
  credential_id: string;
  configuration_id: string;
  credential_type: string;
  format: CredentialFormat | string;
  issuer_identifier: string;
  subject_principal_id: string | null;
  status: IssuedCredentialStatus;
  issued_at: Iso;
  expires_at: Iso | null;
};

export type CredentialConfigurationItem = {
  configuration_id: string;
  credential_type: string;
  format: CredentialFormat | string;
  adapter_version: string;
  required_claims: string[];
  optional_claims: string[];
  holder_binding_required: boolean;
  status_method: string;
  status_mandatory: boolean;
  risk_class: RiskClassNumber;
  assurance_profile_id: string | null;
  issuance_enabled: boolean;
  status: string;
  created_at: Iso;
};

export type IssuerTrustItem = {
  trust_id: string;
  issuer_identifier: string;
  credential_types: string[];
  jurisdictions: string[];
  trust_source: string;
  trust_anchor_id: string | null;
  algorithms: string[];
  status: string;
  valid_to: Iso;
  revision: number;
  created_at: Iso;
};

export type RpRegistrationItem = {
  registration_id: string;
  client_id: string;
  purpose: string;
  allowed_attributes: string[];
  retention_days: number;
  status: string;
  valid_to: Iso;
};

export type ProvisioningSourceItem = { source_id: string; client_id: string; display_name: string; status: string };

export type PrincipalType = "HUMAN" | "ORGANIZATION" | "ORG_UNIT" | "DEVICE" | "APPLICATION" | "SERVICE" | "WORKLOAD" | "AI_AGENT";
export type PrincipalStatus = "PENDING" | "ACTIVE" | "SUSPENDED" | "DISABLED" | "SUPERSEDED" | "ERASED";
export type PrincipalItem = {
  principal_id: string;
  principal_type: PrincipalType;
  status: PrincipalStatus;
  home_cell: string;
  security_revision: number;
  created_at: Iso;
};

export type TrustAnchorItem = {
  anchor_id: string;
  entity_id: string;
  allowed_domains: string[];
  /** Number of pinned keys; the keys themselves are never returned. */
  pinned_keys: number;
  status: string;
  revision: number;
  created_at: Iso;
};

export type VerificationAdapterItem = {
  adapter_id: string;
  provider: string;
  mapping_version: string;
  submit_endpoint: string;
  callback_algorithms: string[];
  equivalence_group: string;
  supported_evidence: string[];
  jurisdictions: string[];
  status: string;
  consecutive_failures: number;
  open_until: Iso | null;
};

/* ---------------------------------------------------------------- health */

export type Health = { success: boolean; data?: { status?: string; checks?: Record<string, unknown> } & Record<string, unknown> };

/* ---------------------------------------------------------------- evidence (Phase F, /v1/evidence) */

export type EvidenceClass = "E1" | "E2" | "E3";
export type ReceiptSummary = {
  receipt_id: string;
  decision_id: string;
  evidence_class: EvidenceClass;
  decision_type: string;
  outcome: string;
  reason_codes: string[];
  actor_handle: string;
  action: string;
  resource_class: string;
  risk_class: string;
  policy_release_id: string | null;
  receipt_hash: string;
  effective_at: Iso;
  created_at: Iso;
};
export type ReceiptAnnotation = { annotation_id: string; kind: string; body: Record<string, unknown>; record_hash: string; created_at: Iso };
export type ReceiptDetail = ReceiptSummary & { receipt: Record<string, unknown>; annotations: ReceiptAnnotation[] };
export type VerificationCheck = { name: string; ok: boolean; detail: string };
export type ReceiptVerification = {
  receipt_id: string;
  ok: boolean;
  receipt_hash_valid: boolean;
  ledgered: boolean;
  sealed: boolean;
  checks: VerificationCheck[];
};
export type ReplayResultValue =
  | "VERIFIED_MATCH"
  | "VERIFIED_STRUCTURAL_ONLY"
  | "MISMATCH"
  | "INCOMPLETE_BY_LAWFUL_ERASURE"
  | "UNVERIFIABLE_INTEGRITY_FAILURE";
export type ReplayOutcome = {
  receipt_id: string;
  replay_id: string;
  result: ReplayResultValue;
  detail?: string;
  differences?: string[];
  verification: ReceiptVerification;
};
export type EvidencePackage = {
  manifest: Record<string, unknown> & { package_id: string; bundle_hash: string; mode: string };
  manifest_signature: string;
  bundle: Record<string, unknown>;
  keys: Record<string, unknown>[];
};
/** One bulk export: several receipts in one signed package that stops being downloadable at `expires_at` (then it is erased). */
export type BulkExport = {
  export_id: string;
  requested_by: string;
  purpose: string;
  mode: string;
  receipt_count: number;
  package_hash: string;
  status: "AVAILABLE" | "EXPIRED" | string;
  downloads: number;
  created_at: Iso;
  expires_at: Iso;
  erased_at: Iso | null;
};
export type BulkExportIn = { receipt_ids: string[]; mode: string; purpose: string; case_ref: string; ttl_minutes?: number | null };
export type AuditEvent = {
  audit_id: string;
  event_type: string;
  actor_type: string;
  actor_handle: string | null;
  target_type: string;
  target_ref: string;
  action: string;
  outcome: "SUCCESS" | "FAILURE" | "DENIED" | "PARTIAL";
  reason_code: string | null;
  evidence_class: EvidenceClass;
  record_hash: string;
  occurred_at: Iso;
  created_at: Iso;
};
export type PartitionReport = {
  partition_id: string;
  ok: boolean;
  entries: number;
  checkpoints: number;
  sealed_through: number;
  unsealed: number;
  first_broken_sequence: number | null;
  problems: { kind: string; sequence_no: number | null; detail: string }[];
};
export type LegalHold = {
  legal_hold_id: string;
  basis: string;
  reference: string;
  requested_by: string;
  approved_by: string;
  subject_count: number;
  object_types: string[];
  effective_at: Iso;
  review_at: Iso;
  review_overdue: boolean;
  expires_at: Iso | null;
  status: "ACTIVE" | "RELEASED" | "EXPIRED";
  released_at: Iso | null;
};

/* ---------------------------------------------------------------- privacy (Phase F, /v1/privacy) */

export type PurposeView = { purpose_code: string; purpose_version: number; description: string; data_categories: string[]; recipient_classes: string[] };
export type ConsentRecord = {
  consent_id: string;
  purpose_code: string;
  purpose_version: number;
  notice_version: string;
  status: "ACTIVE" | "WITHDRAWN" | "EXPIRED" | "SUPERSEDED" | "INVALIDATED";
  granted_at: Iso;
  withdrawn_at: Iso | null;
  capture_method: string;
};
export type ConsentOption = {
  activity_code: string;
  activity_name: string;
  purpose_code: string;
  description: string;
  data_categories: string[];
  /** `CODE@version` of the notice to cite when consenting. */
  notice_version: string;
  notice_uri: string | null;
  consented: boolean;
};
export type PreferenceRecord = { preference_type: string; value: boolean; source: string; revision: number; updated_at: Iso };
export type RightsType =
  | "ACCESS"
  | "RECTIFICATION"
  | "ERASURE"
  | "RESTRICTION"
  | "OBJECTION"
  | "PORTABILITY"
  | "OPT_OUT_SALE_SHARING"
  | "LIMIT_SENSITIVE_PI"
  | "ADMT_ACCESS"
  | "ADMT_OPT_OUT"
  | "COMPLAINT"
  | "HUMAN_REVIEW";
export type FulfilmentItem = { item_id: string; state: string; reason?: string } & Record<string, unknown>;
export type RightsRequest = {
  request_id: string;
  request_type: RightsType;
  subject_principal_id: string;
  requester_principal_id: string | null;
  jurisdiction_pack_id: string;
  status: string;
  identity_verification_state: "NOT_REQUIRED" | "PENDING" | "VERIFIED" | "FAILED";
  received_at: Iso;
  deadline_at: Iso | null;
  deadline_source: string;
  deadline_consumed: number | null;
  deadline_warning: number | null;
  exemptions: Record<string, unknown>[];
  fulfilment_items: FulfilmentItem[];
  response_package_ref: string | null;
  completed_at: Iso | null;
};
export type JurisdictionPackView = {
  pack_id: string;
  version: string;
  framework: string;
  deployable: boolean;
  approval_ref: string | null;
  deadlines_configured: boolean;
};
export type PrivacyRow = Record<string, unknown>;
export type ReleaseCheck = { ready: boolean; blockers: Record<string, string>[] };

/* ---------------------------------------------------------------- operator access (customer side) */

export type LockboxRequest = {
  lockbox_request_id: string;
  jit_grant_id: string;
  requester_principal_id: string;
  capability: string;
  resource_scope: string;
  case_ref: string;
  reason: string;
  duration_minutes: number;
  state: "PENDING" | "APPROVED" | "DENIED" | "REVOKED" | "EXPIRED";
  created_at: Iso;
  expires_at: Iso;
  decided_by: string | null;
  decided_at: Iso | null;
};
export type OperatorAccessPolicy = { lockbox_required: boolean; allowed_operator_jurisdictions: string[]; revision: number };

/* ---------------------------------------------------------------- residency and workloads */

export type CellView = {
  cell_id: string;
  region: string | null;
  jurisdiction: string | null;
  legal_zone: string | null;
  supported_profiles: string[];
  approved_recovery_cells: string[];
  recovery_only: boolean;
};
export type TenantResidency = {
  residency_profile_id: "SRP-0" | "SRP-1" | "SRP-2" | "SRP-3";
  home_cell_id: string;
  permitted_recovery_cells: string[];
  prohibited_cells: string[];
  operator_jurisdictions: string[];
  key_profile: string;
  jurisdiction_pack_id: string;
  placement_epoch: number;
  revision: number;
};
export type ResidencyMatrixRow = {
  subsystem: string;
  cell_id: string;
  region: string | null;
  jurisdiction: string | null;
  legal_zone: string | null;
  recovery_cells: string[];
  evidence: string;
};
export type TransferRule = {
  rule_id: string;
  data_class: "DC1" | "DC2" | "DC3";
  destination_cell_id: string;
  purpose_code: string;
  mechanism: string;
  field_allowlist: string[];
  valid_from: Iso;
  valid_to: Iso | null;
  status: "ACTIVE" | "SUSPENDED" | "RETIRED";
};
export type TransferDecision = { allowed: boolean; code: string; rule_id: string | null; field_allowlist: string[]; cross_border: boolean };
export type WorkloadBindingView = {
  binding_id: string;
  spiffe_id: string;
  logical_principal_id: string;
  environment_id: string;
  artifact_digest: string;
  attestation_required: number;
  status: "ACTIVE" | "SUSPENDED" | "REVOKED" | "EXPIRED";
  security_revision: number;
  valid_from: Iso;
  valid_to: Iso;
};

/* ---------------------------------------------------------------- operator console (/operator) */

export type OperatorCapability = {
  capability: string;
  description: string;
  risk_class: string;
  evidence_class: string;
  scope: "PLATFORM" | "TENANT" | "EITHER";
  customer_data: boolean;
  required_approvals: number;
  max_duration_minutes: number;
  break_glass_allowed: boolean;
};
export type OperatorCapabilities = {
  items: OperatorCapability[];
  separation_of_duties: { rule: string; capabilities: string[]; rationale: string }[];
  break_glass_max_minutes: number;
  /** `direct`: a registered operator needs no access request or second approver for non-customer-data work. */
  mode: "governed" | "direct";
};
export type JitGrantState = "REQUESTED" | "APPROVED" | "ACTIVE" | "DENIED" | "CANCELLED" | "REVOKED" | "EXPIRED";
export type JitGrant = {
  jit_grant_id: string;
  requester_principal_id: string;
  capability: string;
  target_tenant_id: string | null;
  environment: string;
  cell_id: string;
  resource_scope: string;
  case_ref: string;
  reason: string;
  risk_class: string;
  evidence_class: string;
  duration_minutes: number;
  required_approvals: number;
  lockbox_required: boolean;
  lockbox_request_id: string | null;
  break_glass: boolean;
  state: JitGrantState;
  created_at: Iso;
  request_expires_at: Iso | null;
  approved_at: Iso | null;
  activated_at: Iso | null;
  expires_at: Iso | null;
  ended_at: Iso | null;
  end_reason: string | null;
  approvals?: { approver_principal_id: string; verdict: string }[];
  approvals_received?: number;
};
export type BreakGlassReview = {
  review_id: string;
  jit_grant_id: string;
  requester_principal_id: string;
  state: string;
  reviewer_principal_id: string | null;
  outcome: string | null;
  created_at: Iso;
  reviewed_at: Iso | null;
};

/** A fraud or adverse-media signal waiting for a person (reviewer plane). `detail` carries hashes only, never an address or a device identifier. */
export type SignalReviewStatus = "OPEN" | "CHALLENGED" | "RESOLVED";
export type SignalNotifyState = "PENDING" | "SENT" | "FAILED";
export type SignalSource = "VELOCITY" | "ADVERSE_MEDIA" | "IDENTITY_SIGNAL";
export type SignalRiskTier = "LOW" | "ELEVATED" | "HIGH";
export type SignalOutcome = "CLEARED_BY_REVIEWER" | "CONFIRMED_RISK" | "ESCALATED";
export type VelocityDetail = {
  rule?: string;
  dimension?: "DEVICE" | "CONTACT" | "NETWORK";
  action?: "SIGNUP" | "VERIFICATION" | "CLAIM";
  count?: number;
  threshold?: number;
  window_seconds?: number;
  key_hash?: string;
};
export type AdverseMediaDetail = {
  provider?: string;
  queried_at?: Iso;
  outcome?: string;
  hit_count?: number;
  severity?: string;
  rationale?: string;
  reasons?: string[];
};
/** An identity signal (CR-101, CR-102): people linked by a shared device or contact. Counts only: never an identifier, a name or a verdict. */
export type IdentitySignalDetail = {
  people?: number;
  kinds_shared?: number;
  identifiers_shared?: number;
  recommendation_is_review_only?: number;
};
export type SignalReview = {
  review_id: string;
  signal_id: string;
  dedupe_key: string;
  status: SignalReviewStatus;
  notify_state: SignalNotifyState;
  outcome: string | null;
  resolved_by: string | null;
  rationale: string | null;
  evidence_ref: string | null;
  source: SignalSource;
  rule: string;
  detail: VelocityDetail & AdverseMediaDetail & IdentitySignalDetail;
  risk_tier: SignalRiskTier;
  created_at: Iso;
};
export type SignalChallengeIn = { statement: string; evidence_ref: string };
export type SignalResolutionIn = { outcome: SignalOutcome; rationale: string; evidence_ref: string };

/* Phase G: tenant lifecycle, team and invitations (ZID-OVERLAY-001 §2-4). */
export type TenantStatus =
  | "DRAFT"
  | "ORGANIZATION_VERIFYING"
  | "CONFIGURING"
  | "BLOCKED"
  | "INTEGRATING"
  | "SHADOW"
  | "PILOT"
  | "ACTIVE"
  | "SUSPENDED"
  | "OFFBOARDING"
  | "CLOSED";
export type TenantRoleName = "OWNER" | "ADMIN" | "APPROVER" | "OPERATOR" | "VIEWER" | "MEMBER";
export type InvitableRole = "MEMBER" | "ADMIN" | "OPERATOR" | "VIEWER";
export type TenantRoleStatus = "PENDING_VERIFICATION" | "INVITED" | "ACTIVE" | "REVOKED";
export type TenantRole = {
  assignment_id: string;
  principal_id: string;
  role: TenantRoleName;
  status: TenantRoleStatus;
  origin: "BOOTSTRAP" | "INVITATION" | "OWNER_CHANGE" | "PROVISIONING";
  granted_by: string | null;
  approved_by: string | null;
  created_at: Iso;
  activated_at: Iso | null;
};
/** X execute, S prepare or request, A independent approval, R scoped read, T own account only, - none. */
export type RoleMode = "X" | "S" | "A" | "R" | "T" | "-";
export type RoleMatrixRow = { action: string; feature: string; permissions: Record<string, RoleMode>; rule: string };
export type RoleMatrix = {
  version: string;
  policy_hash: string;
  roles: string[];
  matrices: { matrix: string; title: string; actions: RoleMatrixRow[] }[];
};
export type MyPermissions = { roles: string[]; policy_hash: string; enforcement: boolean; rights: Record<string, RoleMode[]> };
/** Where an invitee is: link sent -> passkey created -> signed in -> accepted (ACTIVE). */
export type MemberProgress = "INVITED" | "PASSKEY_CREATED" | "SIGNED_IN" | "ACTIVE" | "PENDING_VERIFICATION" | "REVOKED";
/** How a person is shown to colleagues: "Name (M-0007)". The number is permanent inside a company; the name is optional. */
export type Person = {
  principal_id: string;
  member_no: number | null;
  member_code: string | null;
  name: string | null;
  label: string;
};
export type TenantMember = TenantRole & {
  person: Person;
  passkeys: number;
  passkey_registered_at: Iso | null;
  last_sign_in_at: Iso | null;
  progress: MemberProgress;
};
export type Invitation = TenantRole & { person: Person; enrollment_ticket: string; enrollment_ticket_expires_at: Iso };
export type TenantView = {
  tenant_id: string;
  display_name: string;
  tenant_slug: string;
  status: TenantStatus;
  deployment_mode: string;
  home_cell_id: string;
  configuration_revision: number;
  security_revision: number;
  organization_id: string | null;
  /** What the person chose at sign-up; it only decides what the dashboard offers first and grants nothing. Absent on workspaces made before it was kept. */
  signup_journey?: "PERSONAL" | "ORGANIZATION" | null;
  onboarding_plan_id: string | null;
  created_at: Iso;
  state_changed_at: Iso | null;
  my_roles: TenantRole[];
};

/* ---------------------------------------------------------------- onboarding (OVERLAY-001 §2, §17, §25) */

export type GateStatus = "PASS" | "WARN" | "FAIL" | "PENDING";
/** One machine-readable onboarding requirement and its result. */
export type OnboardingGate = {
  gate_id: string;
  stage: string; // the tenant status whose exit the gate guards
  status: GateStatus;
  code: string | null;
  detail: string;
  blocking: boolean;
};
export type GateReport = { tenant_status: TenantStatus; gates: OnboardingGate[] };
/** The national register an organization root comes from (GB-COH: UK Companies House). */
export type RegistryNamespace = "GB-COH";
export type OrganizationSubmission = { namespace: RegistryNamespace; identifier: string; lei?: string | null };
export type OrganizationResult = GateReport & {
  organization_id: string | null;
  authority_root_assertion_id: string | null;
  legal_name: string | null;
  root_fresh_until: Iso | null;
  admin_grant_id: string | null;
};
export type DeploymentMode = "O0" | "O1" | "O2" | "O3";
export type PlanStatus = "DRAFT" | "APPROVED" | "SUPERSEDED";
export type OnboardingPlan = {
  plan_id: string;
  version: number;
  supersedes: string | null;
  status: PlanStatus;
  target_mode: DeploymentMode;
  scope: Record<string, unknown>;
  exit_criteria: Record<string, number>;
  cutover_windows: Record<string, unknown>[];
  rollback_triggers: Record<string, unknown>;
  support: Record<string, unknown>;
  data_migration: string;
  author_principal_id: string;
  approved_by: string | null;
  shadow_started_at: Iso | null;
  pilot_started_at: Iso | null;
  rollback_tested_at: Iso | null;
};
export type PlanCreate = {
  target_mode: DeploymentMode;
  scope?: Record<string, unknown>;
  exit_criteria?: Record<string, number>;
  data_migration?: string;
};

/* ---------------------------------------------------------------- domains (OVERLAY-001 §5) */

export type DomainMethod = "DNS_TXT" | "HTTPS_WELL_KNOWN";
export type DomainStatus = "PENDING" | "VERIFIED" | "STALE" | "EXPIRED" | "REVOKED";
export type TenantDomain = {
  domain_id: string;
  domain: string;
  method: DomainMethod;
  status: DomainStatus;
  expires_at: Iso;
  verified_at: Iso | null;
  last_checked_at: Iso | null;
  reverify_due_at: Iso | null;
  /** What to publish to prove control; present only while the challenge is pending. */
  publish?: Record<string, string>;
};

/* ---------------------------------------------------------------- integration registry (OVERLAY-001 §14) */

export type AppEnvironment = "DEV" | "TEST" | "STAGE" | "PROD";
export type AppProtocol = "OIDC" | "SAML" | "SCIM" | "AUTHZEN" | "WEBHOOK" | "API" | "MCP" | "OTHER";
export type AppStatus = "DRAFT" | "TESTING" | "ACTIVE" | "SUSPENDED" | "RETIRED";
/** Obligations a policy enforcement point (PEP) may declare it enforces. */
export type PepObligation = "COMMIT_RECHECK" | "MANDATE_RESERVE" | "TOOL_ALLOWLIST" | "HUMAN_APPROVAL" | "STEP_UP";
export type PepContextField = "purpose" | "represented_principal_id" | "mandate_id" | "transaction_context_id" | "tool_id" | "value";
export type IntegrationApplication = {
  application_id: string;
  name: string;
  environment: AppEnvironment;
  protocols: AppProtocol[];
  redirect_uris: string[];
  resource_ids: string[];
  credential_profile: string;
  pep_profile: { obligations: PepObligation[]; context_fields: PepContextField[] };
  risk_floor: string;
  evidence_floor: string;
  owner_principal_id: string | null;
  status: AppStatus;
  configuration_revision: number;
  pep_client_id: string | null;
};
export type ApplicationCreate = {
  name: string;
  environment: AppEnvironment;
  protocols: AppProtocol[];
  pep_client_id?: string | null;
  pep_obligations?: PepObligation[];
  pep_context_fields?: PepContextField[];
};

export type SandboxIdentity = {
  principal_id: string;
  label: string;
  status: "LIVE" | "EXPIRED" | "RETIRED";
  created_at: string;
  expires_at: string;
};
export type SandboxIdentitiesIn = { prefix: string; count: number; days: number };

export type CustomRole = {
  role_id: string;
  name: string;
  description: string;
  base_role: "OPERATOR" | "VIEWER";
  kind: "NARROW" | "EXTEND";
  actions: string[];
  status: "ACTIVE" | "RETIRED";
  members?: number;
  created_at: string;
};
/** NARROW keeps part of the base role; EXTEND adds reads and requests the base role lacks (never approvals or administration). */
export type CustomRoleIn = { name: string; description: string; base_role: "OPERATOR" | "VIEWER"; kind: "NARROW" | "EXTEND"; actions: string[] };
export type CustomRoleAction = { action_id: string; feature: string; matrix: string; mode: string };
export type CustomRoleMember = { principal_id: string; assigned_at: string };

export type DelegationTemplate = {
  template_id: string;
  name: string;
  description: string;
  actions: string[];
  resources: string[];
  purposes: string[] | null;
  value_limit: { amount: string; currency: string } | null;
  max_days: number;
  delegation: { allowed: boolean; max_depth: number };
  min_assurance: number;
  risk_class: number;
  status: "ACTIVE" | "RETIRED";
  created_at: string;
};
export type DelegationTemplateIn = {
  name: string;
  description?: string;
  actions: string[];
  resources: string[];
  value_amount?: string | null;
  value_currency?: string | null;
  max_days: number;
  delegation_allowed?: boolean;
  max_depth?: number;
  risk_class?: number;
};
export type DelegationTemplateApply = { grantee_principal_id: string; represented_principal_id: string; authority_root_assertion_id?: string | null; days?: number | null };

export type RiskInsights = {
  days: number;
  since: string;
  total: number;
  refusal_rate: number;
  by_outcome: Record<string, number>;
  by_risk_class: Record<string, Record<string, number>>;
  top_refusal_reasons: { code: string; count: number }[];
  top_actions: { action: string; count: number; refused: number }[];
  daily: { day: string; decisions: number; refused: number }[];
};
export type AssuranceControl = { control: string; label: string; passed: number; of: number; rate: number | null; met: boolean | null };
export type AssuranceReport = {
  days: number;
  since: string;
  decisions: number;
  controls: AssuranceControl[];
  state_unavailable: number;
  policy_releases_in_force: { policy_release_id: string; decisions: number }[];
};

export type OrgUnitKind = "GROUP" | "SUBSIDIARY" | "DIVISION" | "DEPARTMENT";
export type OrgUnit = { unit_id: string; parent_unit_id: string | null; name: string; kind: OrgUnitKind; depth: number; members: number };
export type OrgUnitIn = { name: string; kind: OrgUnitKind; parent_unit_id?: string | null };

export type ServiceKind = "DEDICATED_ISOLATION" | "SOVEREIGN_SERVICE" | "KEY_CUSTODY" | "NAMED_SUPPORT";
export type ServiceRequest = {
  request_id: string;
  service: ServiceKind;
  status: "REQUESTED" | "IN_PROGRESS" | "ACTIVE" | "DECLINED" | "WITHDRAWN";
  details: string;
  contact: string;
  note: string;
  created_at: string;
  updated_at: string;
};
export type ServiceRequestIn = { service: ServiceKind; details: string; contact: string };

export type OAuthClientType = "PUBLIC" | "CONFIDENTIAL";
export type OAuthClientRecord = {
  client_id: string;
  client_type: OAuthClientType;
  redirect_uris: string[];
  scopes: string[];
  token_auth_method: "none" | "private_key_jwt";
  key_ids: string[];
  status: "ACTIVE" | "SUSPENDED" | "RETIRED";
  first_party: boolean;
  /** The identity a machine client acts as, which grants are issued to; null for a client people sign in through. */
  service_principal_id?: string | null;
};
/** `purpose` SIGN_IN (default): a client people sign in through, with redirect addresses. DECISIONS: the machine client an application uses to ask the
 * decision API (a signed key, no redirect address, no scopes to choose). BUILD_PIPELINE: the machine client a build pipeline uses to register builds
 * (the same shape, its own scope). A sign-in client cannot get a machine token. */
export type OAuthClientCreate = {
  name: string;
  client_type: OAuthClientType;
  purpose?: "SIGN_IN" | "DECISIONS" | "BUILD_PIPELINE";
  redirect_uris?: string[];
  scopes?: string[];
  jwks?: { keys: Record<string, unknown>[] } | null;
};

export type ConnectorType = "OIDC_IDP" | "SAML_IDP" | "SCIM" | "HRIS" | "SIGNALS" | "WEBHOOK" | "DIRECTORY";
export type ConnectorCapability = "AUTHN" | "PROVISIONING" | "GROUPS" | "HR_LIFECYCLE" | "SIGNALS" | "WEBHOOK" | "DIRECTORY_LOOKUP";
export type ConnectorAuthProfile = "OAUTH_CLIENT" | "MTLS" | "WORKLOAD_IDENTITY" | "SECRET";
export type ConnectorHealth = "HEALTHY" | "DEGRADED" | "FAILED" | "QUARANTINED";
export type DisableBehavior = "FAIL_CLOSED_HIGH_RISK" | "SUSPEND_DEPENDENTS";
export type Connector = {
  connector_id: string;
  connector_type: ConnectorType;
  display_name: string;
  source_ref: string | null;
  capabilities: ConnectorCapability[];
  source_of_truth_scope: string[];
  auth_profile: ConnectorAuthProfile;
  health: ConnectorHealth;
  consecutive_failures: number;
  last_success_at: Iso | null;
  fresh_until: Iso | null;
  owner_principal_id: string | null;
  disable_behavior: DisableBehavior;
  quarantine_reason: string | null;
  revision: number;
};
export type ConnectorCreate = {
  connector_type: ConnectorType;
  display_name: string;
  source_ref?: string | null;
  capabilities: ConnectorCapability[];
  auth_profile?: ConnectorAuthProfile;
  freshness_minutes?: number | null;
  disable_behavior?: DisableBehavior;
};
export type MappingRule = {
  source: string;
  target: string;
  transform?: { op: string; table?: Record<string, string>; [k: string]: unknown }[];
  missing?: "IGNORE" | "CLEAR" | "ERROR";
  multi?: "FIRST" | "ALL" | "ERROR";
};
export type SimulationReport = {
  records: number;
  mapped: number;
  quarantined: number;
  conflicts: number;
  errors: number;
  unknown_sources: string[];
  samples: { record: string; facts: Record<string, unknown>; conflicts: unknown[]; errors: unknown[] }[];
};
export type MappingRelease = {
  release_id: string;
  connector_id: string;
  version: number;
  status: "DRAFT" | "REVIEW" | "ACTIVE" | "RETIRED";
  rules: MappingRule[];
  content_hash: string;
  author_principal_id: string;
  approved_by: string | null;
  simulation_report: SimulationReport | null;
  activated_at: Iso | null;
};
export type RouteKind = "APPLICATION" | "DOMAIN";
export type LoginRoute = { rule_id: string; kind: RouteKind; match: string; connection_ref: string };

/** A pilot stage: the slice of applications, actions, risk and people that is enforced (OVERLAY-001 §19). */
export type StageScope = { applications: string[]; actions: string[]; max_risk: number; principals: string[] };
export type PilotStage = {
  stage_id: string;
  plan_id: string;
  name: string;
  scope: StageScope;
  status: "PLANNED" | "ACTIVE" | "COMPLETED" | "ROLLED_BACK";
  approved_by: string | null;
  activated_at: Iso | null;
};

export type TenantTransition = {
  transition_id: string;
  from_status: TenantStatus | null;
  to_status: TenantStatus;
  move: string;
  reason_code: string;
  actor_type: string;
  actor_principal_id: string | null;
  gate_report: unknown;
  occurred_at: Iso;
};
export type ConfigurationRevision = {
  revision: number;
  change_kind: string;
  change_commitment: string;
  actor_principal_id: string | null;
  created_at: Iso;
};

/* ---------------------------------------------------------------- operator: cells and recovery (RESILIENCE-001) */

export type CellReadiness = {
  cell_id: string;
  status: string; // ACTIVE | DEGRADED | DRAINING | QUARANTINED
  replica_lag_s?: number | null;
  backup_age_s?: number | null;
  recovery_keys_ready?: boolean;
  one_az_loss_headroom?: number | null;
  reported?: boolean;
};
export type ReadinessReport = {
  replica_lag_s?: number | null;
  backup_age_s?: number | null;
  recovery_keys_ready: boolean;
  one_az_loss_headroom?: number | null;
};
export type RecoveryKind = "FAILOVER" | "MIGRATION" | "FAILBACK" | "RESTORE";
export type RecoveryOperationStatus = "OPEN" | "BLOCKED" | "COMPLETED" | "ABORTED";
export type RunbookStep = { name: string; type: "CONFIRM" | "AUTO"; detail: string; done?: boolean };
export type GateCheck = { gate_id: string; status: "PASS" | "FAIL"; code: string | null; detail: string };
export type RecoveryStepLog = {
  seq: number;
  step: string;
  outcome: "DONE" | "BLOCKED" | "ABORTED";
  actor: string | null;
  evidence_ref: string | null;
  report: GateCheck[];
  at: Iso;
};
export type RecoveryOperation = {
  operation_id: string;
  tenant_id: string | null;
  kind: RecoveryKind;
  source_cell_id: string;
  target_cell_id: string;
  status: RecoveryOperationStatus;
  blocked_code: string | null;
  steps_done: number;
  next_step: RunbookStep | null;
  runbook: RunbookStep[];
  epoch_before: number | null;
  epoch_after: number | null;
  declared_by: string;
  declared_at: Iso;
  completed_at: Iso | null;
  observed_rto_s: number | null;
  steps?: RecoveryStepLog[];
  report?: GateCheck[];
};
export type DrExercise = {
  exercise_id: string;
  kind: string;
  cell_id: string;
  scenario: string;
  operation_id: string | null;
  observed_rto_s: number | null;
  observed_rpo_s: number | null;
  target_rto_s: number | null;
  target_rpo_s: number | null;
  passed: boolean;
  findings: string[];
  started_at: Iso;
  completed_at: Iso;
};
export type DrExerciseCreate = {
  kind: string;
  exercised_cell_id: string;
  scenario: string;
  operation_id?: string | null;
  observed_rto_s?: number | null;
  observed_rpo_s?: number | null;
  findings?: string[];
  started_at: Iso;
  completed_at: Iso;
};

/* ---------------------------------------------------------------- operator: SRE operations (SRE-001 section 29) */

export type ServiceTier = "T0" | "T1" | "T2" | "T3";
export type CatalogueService = {
  service_id: string;
  name: string;
  tier: ServiceTier;
  owner: string;
  oncall: string;
  dashboard: string;
  journeys: { prefix: string; latency_s: number }[];
  slos: { slo_id: string; sli: string; target: number }[];
  runbooks: string[];
};
export type SloWindow = { window: string; long_burn: number; short_burn: number; factor: number; class: "PAGE" | "TICKET"; firing: boolean };
export type SloState = {
  slo_id: string;
  sli: string;
  target: number;
  state?: "UNKNOWN";
  events?: number;
  good_ratio?: number | null;
  budget_remaining?: number;
  budget_state?: string;
  alert?: "PAGE" | "TICKET" | "NONE";
  windows?: SloWindow[];
};
export type ServiceHealth = { service_id: string; tier: ServiceTier; state: string; frozen: boolean; open_incidents: string[]; slos: SloState[] };
export type ChangeClass = "STANDARD" | "RISKY" | "FIX" | "SECURITY";
export type ChangePolicy = {
  service_id: string;
  change_class: ChangeClass;
  budget_state: string;
  frozen: boolean;
  allowed: boolean;
  requires: string | null;
  reason: string;
};
export type IncidentSeverity = "SEV0" | "SEV1" | "SEV2" | "SEV3" | "SEV4";
export type IncidentStatus = "DECLARED" | "MITIGATING" | "RECOVERY_VALIDATION" | "RESOLVED" | "CLOSED";
export type IncidentRole = "INCIDENT_COMMANDER" | "OPERATIONS_LEAD" | "COMMUNICATIONS_LEAD" | "SCRIBE" | "SECURITY_LEAD";
export type IncidentEntry = { seq: number; kind: string; actor: string; note: string; data: Record<string, unknown>; at: Iso };
export type IncidentActionItem = { item_id: string; title: string; owner: string | null; due: string | null; verified_by: string | null };
export type Incident = {
  incident_id: string;
  title: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  security: boolean;
  services: string[];
  cells: string[];
  roles: Partial<Record<IncidentRole, string>>;
  declared_by: string;
  declared_at: Iso;
  mitigated_at: Iso | null;
  resolved_at: Iso | null;
  closed_at: Iso | null;
  pir_ref: string | null;
  version: number;
  timeline?: IncidentEntry[];
  action_items?: IncidentActionItem[];
};
export type IncidentCreate = {
  title: string;
  severity: IncidentSeverity;
  security?: boolean;
  services?: string[];
  cells?: string[];
  roles?: Partial<Record<IncidentRole, string>>;
};
export type IncidentUpdate = {
  severity?: IncidentSeverity;
  status?: "MITIGATING" | "RECOVERY_VALIDATION";
  roles?: Partial<Record<IncidentRole, string>>;
  reason: string;
};
export type PrrGate = {
  gate: string;
  status: "PASS" | "FAIL" | "ACCEPTED";
  evidence_ref?: string | null;
  risk_owner?: string | null;
  risk_expires?: string | null;
  compensating_control?: string | null;
};
export type PrrBlocker = { gate: string; reason: string };
export type ReadinessVerdict = {
  service_id: string;
  review_id: string | null;
  release_ref?: string;
  production_ready: boolean;
  blocking: PrrBlocker[];
  recorded_at?: Iso;
};

/** What the company declared at sign-up, stored with the tenant (tenant_registration). */
export type DeclaredOrganization = {
  namespace: RegistryNamespace;
  identifier: string;
  lei: string | null;
  notice_version: string;
  notice_accepted_at: Iso;
  home_cell_id: string;
  declared_at: Iso;
};
export type RecoveryCodeStatus = { codes_remaining: number; issued_at: Iso | null };

/* ---------------------------------------------------------------- evidence cases and provenance (EVIDENCE-001 §14, §21) */

export type EvidenceCaseKind = "INVESTIGATION" | "AUDIT" | "LEGAL" | "INCIDENT" | "DATA_RIGHTS";
export type EvidenceCase = {
  case_id: string;
  case_kind: EvidenceCaseKind;
  purpose: string;
  legal_basis: string | null;
  status: "OPEN" | "CLOSED" | string;
  opened_at: Iso;
  expires_at: Iso;
  closed_at: Iso | null;
};
export type CustodyRecord = {
  custody_id: string;
  evidence_item_id: string;
  custody_action: string;
  purpose: string;
  export_mode: string | null;
  package_hash: string | null;
  created_at: Iso;
};
export type EvidenceCaseDetail = EvidenceCase & { items: CustodyRecord[] };
export type ProvenanceEdge = {
  relation: string;
  subject_kind: string;
  subject_type: string;
  subject_ref: string;
  object_kind: string;
  object_type: string;
  object_ref: string;
  receipt_id: string | null;
  recorded_at: Iso;
};
export type ProvenanceGraph = {
  root: { type: string; ref: string };
  nodes: { type: string; ref: string }[];
  edges: ProvenanceEdge[];
  truncated: boolean;
};
export type EvidenceArtifact = {
  artifact_id: string;
  tenant_id: string | null;
  artifact_type: string;
  artifact_ref: string;
  version: string | number;
  content_hash: string;
  body: unknown;
  registered_at: Iso;
};

/* ---------------------------------------------------------------- privacy governance (PRIV-001 §5, §11, §19) */

export type ProcessorRole = "PROCESSOR" | "SUBPROCESSOR" | "INDEPENDENT_CONTROLLER" | "RECIPIENT";
export type PrivacyProcessor = {
  processor_id: string;
  name: string;
  role: ProcessorRole;
  data_categories: string[];
  locations: string[];
  processing_activity_codes: string[];
  purposes: string[];
  transfer_mechanism: string | null;
  deletion_sla_days: number | null;
  security_assurance: string | null;
  contract_ref: string | null;
  status: string;
  revision: number;
};
export type ProcessorCreate = {
  processor_id: string;
  name: string;
  role: ProcessorRole;
  data_categories: string[];
  locations: string[];
  purposes?: string[];
  transfer_mechanism?: string | null;
  deletion_sla_days?: number | null;
  contract_ref?: string | null;
};
export type PrivacyNotice = {
  notice_id: string;
  notice_code: string;
  version: number;
  audience: string;
  jurisdictions: string[];
  content_hash: string;
  content_uri: string | null;
  status: string;
  effective_at: Iso | null;
};
export type AdmSignificance = "NONE" | "MATERIAL" | "LEGAL_OR_SIMILARLY_SIGNIFICANT";
export type AdmProfile = {
  profile_id: string;
  decision_class: string;
  version: number;
  purpose_code: string;
  input_data_categories: string[];
  model_or_rules_ref: string;
  solely_automated: boolean;
  significance_class: AdmSignificance;
  special_category_use: boolean;
  explanation_profile: string;
  human_review_route: string;
  status: string;
  created_at: Iso;
};
export type AdmCreate = {
  decision_class: string;
  purpose_code: string;
  input_data_categories: string[];
  model_or_rules_ref: string;
  solely_automated: boolean;
  significance_class: AdmSignificance;
  explanation_profile: string;
  human_review_route: string;
  special_category_use?: boolean;
  risk_assessment_ref?: string | null;
};

/* ---------------------------------------------------------------- policy administration (AUTHZ-001 §18-19) */

export type PolicyReleaseStatus = "DRAFT" | "VALIDATING" | "REVIEW" | "SCHEDULED" | "ACTIVE" | "RETIRED" | "REVOKED";
export type PolicyTestCase = {
  name: string;
  principal: string;
  action: string;
  resource: string;
  context: Record<string, unknown>;
  expect_allow: boolean;
};
export type PolicyReleaseSummary = {
  release_id: string;
  policy_name: string;
  version: number;
  status: PolicyReleaseStatus;
  content_hash: string;
  author_principal_id: string;
  reviewers: string[];
  released_at: Iso | null;
  case_count: number;
};
export type PolicyReleaseView = {
  release_id: string;
  status: PolicyReleaseStatus;
  policies: string;
  schema_version: string;
  content_hash: string;
  cases: PolicyTestCase[];
  author_principal_id: string;
  reviewers: string[];
  released_at: Iso | null;
  high_risk: boolean;
};
export type PolicyValidation = PolicyReleaseView & {
  problems: string[];
  privilege_diff: { newly_allowed: string[]; newly_denied: string[] };
};
export type PolicyOutcomeView = { allowed: boolean; determining: string[]; codes: string[] };
export type PolicySimulation = { candidate: PolicyOutcomeView | null; active: PolicyOutcomeView | null };

/* Webhooks (CR-018) */
export type WebhookEndpointStatus = "PENDING_VERIFICATION" | "ACTIVE" | "PAUSED" | "DISABLED" | "RETIRED";
export type WebhookEndpoint = {
  endpoint_id: string;
  url: string;
  event_types: string[];
  status: WebhookEndpointStatus | string;
  signing_key_id: string;
  max_inflight: number;
  created_by: string;
  created_at: Iso | null;
  updated_at: Iso | null;
};
export type WebhookDeliveryStatus = "QUEUED" | "IN_FLIGHT" | "RETRY_SCHEDULED" | "DELIVERED" | "FAILED" | "EXHAUSTED";
export type WebhookDelivery = {
  delivery_id: string;
  endpoint_id: string;
  event_id: string;
  event_type: string;
  payload_digest: string;
  status: WebhookDeliveryStatus | string;
  attempts: number;
  next_attempt_at: Iso | null;
  last_status_code: number | null;
  last_error: string | null;
  replay_of: string | null;
  created_at: Iso | null;
  delivered_at: Iso | null;
};
export type WebhookEventType = { type: string; description: string };
export type WebhookEndpointCreate = { url: string; event_types: string[]; max_inflight?: number };

/* Alerts and tasks (CR-019) */
export type AlertSeverity = "CRITICAL" | "HIGH" | "MEDIUM" | "INFO";
export type AlertState = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
export type PlatformAlert = {
  alert_id: string;
  severity: AlertSeverity;
  kind: string;
  title: string;
  body: string;
  object_type: string;
  object_ref: string;
  /** Console path of the affected object. */
  href: string;
  recommended_action: string;
  is_task: boolean;
  mandatory: boolean;
  state: AlertState;
  occurrences: number;
  first_seen_at: Iso;
  last_seen_at: Iso;
  acknowledged_by: string | null;
  acknowledged_at: Iso | null;
  resolved_by: string | null;
  resolved_at: Iso | null;
};
export type AlertSummary = {
  open: number;
  unacknowledged: number;
  by_severity: Record<AlertSeverity, number>;
};

/* Global search (CR-020) */
export type SearchHit = { type: string; group: string; id: string; label: string; hint: string; href: string };

/* Agent, blueprint and tool registry (CR-021) */
export type AgentStatus = "DRAFT" | "PENDING_APPROVAL" | "ACTIVE" | "SUSPENDED" | "REVOKED" | "RETIRED";
export type AgentItem = {
  agent_principal_id: string;
  agent_name: string;
  blueprint_id: string;
  blueprint_version: string;
  /** Publisher, operator, sponsor and owning organization are separate principals (UX-11). */
  publisher_principal_id: string;
  operator_principal_id: string;
  sponsor_principal_id: string | null;
  owning_organization_id: string | null;
  parent_agent_id: string | null;
  model_provider: string;
  allowed_tools: string[];
  max_subagent_depth: number;
  status: AgentStatus;
  security_revision: number;
};
export type AgentGate = "SECURITY_REVIEW" | "KILL_PATH_TESTED" | "SHORT_LIVED_CREDENTIALS" | "OBSERVABILITY";
export type AgentReadiness = {
  agent_principal_id: string;
  status: AgentStatus;
  /** Codes of the activation gates still blocking, e.g. AGENT_SECURITY_REVIEW_REQUIRED. */
  blockers: string[];
  attestations: Partial<Record<AgentGate, { attested_by: string; note: string; attested_at: Iso }>>;
  attestable_gates: AgentGate[];
  live_bindings: number;
};
export type AgentDetail = AgentItem & { readiness: AgentReadiness; sub_agents: number };
export type AgentCreate = {
  agent_name: string;
  blueprint_id: string;
  blueprint_version: string;
  operator_principal_id: string;
  sponsor_principal_id: string;
  owning_organization_id?: string | null;
  model_provider?: string;
  allowed_tools: string[];
  max_subagent_depth: number;
};
export type BlueprintReview = "DRAFT" | "APPROVED" | "REVOKED";
export type BlueprintRelease = {
  blueprint_id: string;
  version: string;
  artifact_digest: string;
  tool_manifest_digest: string;
  publisher_principal_id: string;
  review_status: BlueprintReview;
  effective_at: Iso | null;
};
export type BlueprintCreate = {
  blueprint_id: string;
  version: string;
  artifact_digest: string;
  tool_manifest_digest: string;
  publisher_principal_id: string;
};
export type BuildReview = "DRAFT" | "APPROVED" | "REVOKED";
/** The reviewed software a blueprint release may run: registered by one person, approved by another (CR-064). */
export type BuildItem = {
  build_id: string;
  name: string;
  artifact_digest: string;
  source_ref: string | null;
  provenance_ref: string | null;
  registered_by: string;
  review_status: BuildReview;
  approved_by: string | null;
  registered_at: Iso;
  approved_at: Iso | null;
  revoked_at: Iso | null;
  revoke_reason: string | null;
};
export type BuildList = Items<BuildItem> & {
  /** Whether a blueprint release must run an approved build in this environment. */
  enforced: boolean;
  /** Whether this environment can look an image's digest up by name (an operator named registries). */
  lookup_enabled: boolean;
  /** Image repositories an operator offers to register builds from (ZID_BUILD_IMAGES): picked, not typed. */
  images?: string[];
};
/** What an image name points at today, as its registry says. Nothing is registered by asking. */
export type BuildLookup = { image: string; registry: string; repository: string; reference: string; digest: string; media_type: string };
/** The versions (tags) of an image repository, from its registry. */
export type BuildVersions = { image: string; registry: string; repository: string; tags: string[] };
export type BuildCreate = { name: string; artifact_digest: string; source_ref?: string | null; provenance_ref?: string | null };
export type BuildRevoked = BuildItem & { releases_revoked: number; bindings_suspended: number; agents_suspended: number };

export type ToolStatus = "DRAFT" | "ACTIVE" | "SUSPENDED" | "RETIRED";
export type ToolSideEffect = "NONE" | "REVERSIBLE" | "IRREVERSIBLE" | "EXTERNAL_TRANSACTION";
export type ToolItem = {
  tool_id: string;
  version: string;
  server_principal_id: string;
  canonical_action: string;
  default_risk_class: RiskClassNumber;
  side_effect: ToolSideEffect;
  requires_mandate: boolean;
  requires_human_approval: boolean;
  status: ToolStatus;
  /** Only ACTIVE tools can run: appearing in a server's tool list never makes one executable (UX-12). */
  executable: boolean;
};
export type ToolCreate = {
  tool_id: string;
  version: string;
  server_principal_id: string;
  canonical_action: string;
  default_risk_class: RiskClassNumber;
  side_effect: ToolSideEffect;
  requires_mandate: boolean;
  requires_human_approval: boolean;
};

/* Tenant settings (CR-022) */
export type SettingsArea = "AUTHENTICATION" | "NOTIFICATIONS" | "BRANDING";
export type SettingSnapshot<T> = {
  area: SettingsArea;
  /** 0 = never set (platform defaults). Send this back as `expected_revision`. */
  revision: number;
  value: T;
  commitment: string | null;
  created_by: string | null;
  created_at: Iso | null;
};
export type AuthenticationSettings = { session_absolute_hours: number | null; session_idle_minutes: number | null };
export type NotificationSettings = { muted_kinds: string[] };
export type BrandAccent = "blue" | "teal" | "green" | "violet" | "orange" | "slate";
/** `accent_hex`, `welcome_text` and `logo` (an inline PNG, JPEG or WebP data address of at most 48 KB) are the fuller theme, a plan feature; empty means the platform's own. */
export type BrandingSettings = {
  display_name: string;
  accent: BrandAccent;
  support_url: string;
  accent_hex: string;
  welcome_text: string;
  logo: string;
};
export type SettingsAll = {
  areas: {
    AUTHENTICATION: SettingSnapshot<AuthenticationSettings>;
    NOTIFICATIONS: SettingSnapshot<NotificationSettings>;
    BRANDING: SettingSnapshot<BrandingSettings>;
  };
};
export type SettingsHistoryItem = SettingSnapshot<Record<string, unknown>>;
export type NotificationKind = { kind: string; title: string; severity: AlertSeverity; mandatory: boolean };
export type CryptoOverview = {
  key_mode: string;
  customer_managed_keys: { byok: { available: boolean; state: string }; hyok: { available: boolean; state: string } };
  key_provider: string;
  residency_key_profile: string | null;
  policy_registry_version: string;
  purposes: { purpose: string; profile: string; algorithms: string[] }[];
  published_signing_key_ids: Record<string, (string | undefined)[]>;
  note: string;
};

/* API usage and rate limits (CR-023) */
export type UsageRow = { day: string; surface: string; status_class: "2xx" | "3xx" | "4xx" | "5xx"; calls: number };
export type UsageClient = { client_id: string; calls: number; errors: number };
export type UsageSummary = { days: number; items: UsageRow[]; clients: UsageClient[] };
export type RateLimitStatus = {
  enforced: boolean;
  scope: "tenant";
  limit_per_minute: number;
  used_this_minute: number;
  resets_in_s: number;
  on_exceed?: string;
};

/* Billing and the commercial catalogue (ZID-COM-001; CR-024 to CR-030). Money is a decimal string. */
export type CatalogPlan = {
  plan: string;
  label: string;
  platform_fee_is_floor: boolean;
  limits: {
    mah_included: number | null;
    auth_events_included: number | null;
    policy_decisions_included: number | null;
    m2m_grants_included: number | null;
    sso_included: number | null;
    production_environments: number | null;
    support_level: string;
  };
  capabilities: { capability: string; mode: string; tier: string | null }[];
};
export type CatalogPriceLine = { sku: string; plan: string | null; unit: string; unit_rate: string | null; unit_divisor: number; released: boolean };
export type Catalog = {
  status: "PRICED" | "UNPRICED";
  plans: CatalogPlan[];
  pricing: { version: number; currency: string; effective_from: string; lines: CatalogPriceLine[] } | null;
  note: string;
};
export type EstimateIn = {
  plan: string;
  mah: number;
  auth_events: number;
  policy_decisions: number;
  m2m_grants: number;
  sso_connections: number;
  scim_connections: number;
};
export type BillEstimate = {
  status: "ESTIMATE" | "UNPRICED";
  plan?: string;
  price_book_version?: number;
  currency?: string;
  monthly_estimate?: string;
  fee_is_floor?: boolean;
  lines?: { kind: string; meter: string | null; formula: string; net: string }[];
  warnings?: string[];
  assumptions: string[];
};
export type EntitlementView = {
  plan: string;
  plan_label: string;
  state: string;
  source: string;
  effective_from: string | null;
  version: number;
  limits: CatalogPlan["limits"];
  capabilities: { capability: string; mode: string; tier: string | null; released: boolean; granted_by_exception: boolean; limit: number | null; used: number | null }[];
  note: string;
};
export type StepUpDraft = { action: string; resource: string; change?: Record<string, unknown>; critical?: boolean };
export type StepUpChallenge =
  | { required: false; tier: "S0"; action: string; reasons: string[] }
  | {
      required: true;
      challenge_id: string;
      tier: "S1" | "S2";
      action: string;
      resource: string;
      summary: string;
      command_hash: string;
      requested_assurance: string;
      reasons: string[];
      expires_at: string;
      policy_version: string;
      why: string;
      public_key: Record<string, unknown>;
    };
export type StepUpProof = {
  proof_id: string;
  tier: "S1" | "S2";
  action: string;
  resource: string;
  single_use: boolean;
  verified_at: string;
  expires_at: string;
};
export type StepUpTiers = {
  version: string;
  tiers: Record<string, { max_age_seconds: number | null; single_use: boolean; phishing_resistant: boolean; action_bound: boolean }>;
  s2_never_accepts: string[];
  actions: { action: string; name: string; tier: string; escalates_to: string | null; family: string; gate: string; control: string }[];
};
export type ApprovalTier = "A1" | "A2";
export type ApprovalCaseState =
  | "PENDING_APPROVAL"
  | "PARTIALLY_APPROVED"
  | "APPROVED_READY"
  | "EXECUTED"
  | "FAILED"
  | "REJECTED"
  | "EXPIRED"
  | "INVALIDATED"
  | "CANCELLED"
  | "NEEDS_CHANGES";
export type ApprovalHold = "BLOCKED_INSUFFICIENT_APPROVERS" | "PENDING_INDEPENDENT_REVIEW";
export type ApprovalVoteRow = {
  vote_id: string;
  approver_principal_id: string;
  approver?: Person | null;
  person_ref: string;
  authority_basis: string;
  vote: "APPROVE" | "REJECT";
  step_up_tier: string | null;
  step_up_proof_ref: string | null;
  decided_at: string;
};
/** One immutable request version. The server decides quorum; `hold` and `you.reasons` explain a disabled Approve. */
export type ApprovalCase = {
  case_id: string;
  version: number;
  supersedes_case_id: string | null;
  action_id: string;
  tier: ApprovalTier;
  state: ApprovalCaseState;
  hold: ApprovalHold | null;
  approvals_received: number;
  approvals_required: number;
  independent_people_available: number;
  summary: string;
  reason: string;
  payload: Record<string, unknown>;
  gate: string;
  /** Additional gates the platform can check: blocking ones refuse the request, the others inform the approver. */
  gates: { code: string; ok: boolean; blocking: boolean; note: string }[];
  request_hash: string;
  policy_version: string;
  region: string;
  requester_principal_id: string;
  beneficiary_principal_id: string | null;
  /** The same people as "Name (M-0007)"; null when the case is not about a person in this company. */
  requester?: Person | null;
  beneficiary?: Person | null;
  expires_at: string;
  created_at: string;
  approved_at: string | null;
  applied_at: string | null;
  votes: ApprovalVoteRow[];
  notice: string;
  you?: { may_vote: boolean; reasons: string[]; is_requester: boolean };
};
export type ApprovalCaseDraft = {
  action_id: string;
  target_id: string;
  payload?: Record<string, unknown>;
  reason: string;
  ttl_seconds?: number;
  evidence_ref?: string;
};
export type ApprovalExecution = {
  outcome: "EXECUTED" | "FAILED";
  replayed: boolean;
  attempt: number;
  reason_code: string | null;
  result: Record<string, unknown>;
  case: ApprovalCase;
  /** An enrollment link: shown to the first caller only and never stored. */
  shown_once?: Record<string, unknown>;
  guidance?: string;
};
export type ApprovalPolicyRow = {
  action_id: string;
  tier: "A0" | "A1" | "A2" | "FORBIDDEN" | "CONTAINMENT";
  scope: "CUSTOMER" | "PLATFORM";
  votes_required: number;
  approver_roles: string[];
  executable: boolean;
  /** Why nothing can apply this action (a platform rule or no such capability), or null. */
  no_operation: string | null;
  gate: string;
};
export type ApprovalPolicy = {
  version: string;
  policy_version: string;
  enforcement: "SHADOW" | "ENFORCE";
  assured_review: "ACTIVE" | "INACTIVE";
  tiers: Record<string, number>;
  actions: ApprovalPolicyRow[];
};
export type SoloStatus = {
  eligible_human_member_count: number;
  solo_mode: boolean;
  region: string;
  policy_version: string;
  legal_form_resolved: boolean;
  independent_routes: string[];
  a2_requires: string;
  message: string | null;
};
export type GovernanceReviewer = {
  reviewer_id: string;
  principal_id: string;
  relationship: "EXTERNAL_PROFESSIONAL" | "CO_DIRECTOR" | "TRUSTEE" | "AUTHORIZED_DELEGATE";
  status: "PROPOSED" | "ACTIVE" | "REVOKED" | "EXPIRED";
  region: string;
  expires_at: string;
  activated_via: "A1_APPROVAL" | "PLATFORM_VERIFICATION" | null;
  conflicts_declared: number;
  kind: "REVIEWER";
  person?: Person;
};
export type ReviewerAppointIn = {
  principal_id: string;
  relationship: GovernanceReviewer["relationship"];
  authority_basis: string;
  independence_declaration: string;
  declared_conflicts?: string[];
};
export type ApprovalEvidence = {
  case_id: string;
  version: number;
  supersedes_case_id: string | null;
  action_id: string;
  tier: ApprovalTier;
  request_hash: string;
  policy_version: string;
  region: string;
  maker: { principal_id: string; person_ref: string };
  editors: number;
  checkers: ApprovalVoteRow[];
  distinct_checkers: number;
  transitions: { from: string; to: string; actor: string | null; reason_code: string; at: string }[];
  executions: { attempt: number; outcome: string; reason_code: string | null; executed_at: string; idempotency_key_hash: string; quorum: Record<string, unknown> }[];
  state: ApprovalCaseState;
  audit: { target_type: string; target_ref: string; event_prefix: string };
};
export type RegistrationCountry = {
  code: string;
  account: "AVAILABLE" | "AVAILABLE_IN_ANOTHER_REGION" | "NOT_AVAILABLE_YET";
  routing: { state: "HERE" | "OTHER_CELL" | "UNAVAILABLE"; url: string | null };
  organization_claims: "AVAILABLE" | "NOT_AVAILABLE_YET";
  message: string | null;
  /** Where to go when registration is not open for the country. */
  contact?: string | null;
};
export type RegistrationCapabilities = {
  notice_version: string;
  locale: string;
  nonce: string;
  journeys: ("PERSONAL" | "ORGANIZATION")[];
  identity_providers: string[];
  authenticators: { primary: string; fallback: string };
  country: RegistrationCountry | null;
};
export type RegistrationIntentIn = {
  country: string;
  journey: "PERSONAL" | "ORGANIZATION";
  email: string;
  locale?: string;
  notice_version: string;
  nonce: string;
};
export type RegistrationStarted = { request_id: string; status: "VERIFICATION_REQUESTED"; message: string };
export type RegistrationNextStep =
  | "VERIFY_EMAIL"
  | "ENROLL_AUTHENTICATOR"
  | "PERSONAL_WORKSPACE"
  | "ORGANIZATION_CLAIM"
  | "ACCEPT_INVITATION"
  | "REQUEST_NEW_LINK"
  | "CONTACT_SUPPORT";
export type RegistrationStatus = {
  request_id: string;
  state: "PENDING_VERIFICATION" | "VERIFIED" | "COMPLETED" | "EXPIRED" | "ON_HOLD";
  next_step: RegistrationNextStep;
  account: "NONE" | "ACTIVE";
  identity: "NOT_VERIFIED";
  organization: "NONE";
  message: string | null;
  expires_at: string;
};
/** The link was redeemed: an active account that is NOT identity verified, and a single-use enrollment ticket shown once. */
export type RegistrationConfirmed = {
  request_id: string;
  account: "ACTIVE";
  identity: "NOT_VERIFIED";
  next_step: "ENROLL_AUTHENTICATOR";
  then: "PERSONAL_WORKSPACE" | "ORGANIZATION_CLAIM" | "ACCEPT_INVITATION";
  enrollment_ticket: string;
  enrollment_ticket_expires_at: string;
  console_client_id: string | null;
  workspace: string | null;
  address: string;
  authenticator_fallback: "GOVERNED_RECOVERY";
};
/** An approval case as the organization routes return it: the L-10 case plus the plain status word, the level, the derived execution hold
 * and the evidence reference (restricted evidence is separately authorized and never inline). */
export type OrgApprovalCase = ApprovalCase & {
  status_label: string;
  level: ApprovalTier;
  execution_hold: string | null;
  evidence: { reference: string | null; access: "SEPARATELY_AUTHORIZED" };
};
export type OrgApprovalSummary = {
  request_id: string;
  title: string;
  requester_principal_id: string;
  requester?: Person | null;
  level: ApprovalTier;
  state: ApprovalCaseState;
  status_label: string;
  approvals_received: number;
  approvals_required: number;
  shortage: string | null;
  policy_version: string;
  expires_at: string;
  created_at: string;
  next_action: string;
};
export type ApprovalScope = "review" | "mine" | "history";
export type OrgApprovalInbox = {
  scope: ApprovalScope;
  items: OrgApprovalSummary[];
  counts: Record<ApprovalScope, number>;
  next_cursor: string | null;
};
export type OrgApprovalQuery = {
  scope?: ApprovalScope;
  state?: ApprovalCaseState;
  risk?: ApprovalTier;
  created_from?: string;
  created_to?: string;
  q?: string;
  cursor?: string;
  limit?: number;
};
export type OrgDecision = "APPROVE" | "REJECT" | "REQUEST_CHANGES";
export type OnboardingItem = {
  key: string;
  title: string;
  status: "Required" | "In review" | "Verified" | "Needs action" | "Restricted";
  required: boolean;
  done: boolean;
  reason: string | null;
  next_action: string | null;
};
export type OrgReadiness = "DRAFT" | "LIMITED_SETUP" | "READY_FOR_ACTIVATION" | "ACTIVE" | "ACTIVE_LIMITED" | "RESTRICTED";
export type OrgOnboarding = {
  organization_id: string;
  readiness: OrgReadiness;
  trust: OnboardingItem[];
  required: OnboardingItem[];
  approvers: OnboardingItem;
  solo: boolean;
  can_activate: boolean;
  blockers: string[];
  restrictions: string[];
  activated_at: string | null;
  region: string;
  acknowledgements: { kind: "ORGANIZATIONAL_CONTROLS" | "REGION_POLICY"; version: string; title: string; summary: string; accepted: boolean }[];
  policy_version: string;
};
export type OrgAcknowledgementIn = { kind: "ORGANIZATIONAL_CONTROLS" | "REGION_POLICY"; version: string; locale?: string };
export type PlanFeature = {
  feature: string;
  section: "baseline" | "standard" | "advanced";
  name: string;
  band_state: "BASELINE" | "INCLUDED" | "ADD_ON" | "UPGRADE" | null;
  label: string | null;
  available: boolean;
  reasons: string[];
  upgrade_band: string | null;
  note: string;
};
export type EffectiveEntitlements = {
  plan: string;
  band: string | null;
  subscription_state: string;
  source: string;
  effective_from: string | null;
  catalog_version: string;
  commercial_enforcement: string;
  band_matrix_enforcement: string;
  features: PlanFeature[];
  note: string;
};
export type DowngradeImpact = {
  from_band: string;
  to_band: string;
  becomes_inactive: { feature: string; name: string }[];
  becomes_add_on: { feature: string; name: string }[];
  preserved: string[];
  effective: string;
  deletes_anything: boolean;
  note: string;
};
export type UsageMeter = {
  meter: string;
  counts: string;
  committed: number;
  reserved: number;
  released: number;
  limit: number | null;
  remaining: number | null;
  quota_defined: boolean;
  priced: boolean;
};
export type ReservationUsage = {
  period: string;
  resets_at: string;
  meters: UsageMeter[];
  treatment: { incomplete: string; duplicate: string; unpriced: string };
};
export type BillableMeter = { meter: string; events: number; quantity: string; adjustments: string; net: string; frozen: boolean; hash_total: string | null };
export type BillableSummary = { month: string; meters: BillableMeter[] };
export type BillableEvent = { event_id: string; meter: string; quantity: string; occurred_at: Iso; late: boolean; recovered: boolean };
export type BillableEvents = { items: BillableEvent[]; truncated: boolean };
export type RatedLine = { kind: string; sku: string | null; meter: string | null; plan: string | null; segment: string | null; formula: string; net: string };
export type RatedUsage = {
  run_id: string;
  attempt: number;
  period: string;
  currency: string;
  subtotal: string;
  credit_balance: string;
  warnings: string[];
  inputs_hash: string;
  outputs_hash: string;
  lines: RatedLine[];
};
export type IssuerKeysRotated = Record<string, unknown>;
export type BillPreview = {
  month: string;
  status: "ESTIMATE" | "UNPRICED";
  reason?: string;
  projection_confidence?: "NONE" | "LOW" | "NORMAL";
  currency?: string;
  usage_to_date?: Record<string, string>;
  bill_to_date?: string;
  projected_bill?: string | null;
  lines_to_date?: { kind: string; meter: string | null; formula: string; net: string }[];
  warnings?: string[];
  assumptions: string[];
};
export type InvoiceLine = { seq: number; kind: string; sku: string | null; meter: string | null; description: string; net: string; tax_code: string | null; tax: string | null; gross: string };
export type Invoice = {
  invoice_id: string;
  number: string | null;
  status: string;
  period: string;
  currency: string;
  subtotal: string;
  tax_total: string;
  gross: string;
  tax_status: string;
  issued_at: string | null;
  due_at: string | null;
  document_digest: string | null;
  credited: string;
  paid: string;
  outstanding: string | null;
  /** The credit notes issued against this invoice: references only, never an unapproved staff adjustment. */
  credit_notes?: { number: string; amount: string; reason_code: string; issued_at: string }[];
  lines: InvoiceLine[];
};
export type PaymentAttempt = { at: string; amount: string; currency: string; status: "CAPTURED" | "FAILED"; failure_code: string | null; invoice_number: string | null };
/** The saved card as the company may see it: never the number. */
export type PaymentMethod = { brand: string; last4: string; exp_month: number; exp_year: number };
/** What is owed now (open issued invoices, per currency) and the saved card (CR-034). */
/** An order the operator activated that waits for the first month's platform fee (CR-062). The plan switches only after this is paid. */
export type ActivationWait = { order_id: string; plan: string | null; status: "AWAITING" | "PAID" | "CREDITED" | "CANCELLED"; amount: string; currency: string; created_at: string | null; paid_at: string | null };
export type ActivationConfirm = { activated: boolean; status: string; reason?: string; already_active?: boolean; plan?: string | null };
export type BillingPayment = {
  configured: boolean;
  account_linked: boolean;
  /** Whether this person may save or replace the card (the owner). */
  can_manage: boolean;
  /** The latest attempts to collect an invoice, newest first. */
  payments: PaymentAttempt[];
  method: PaymentMethod | null;
  method_error: string | null;
  amount_due: { currency: string; amount: string }[];
  open_invoices: { invoice_id: string; number: string | null; currency: string; outstanding: string; due_at: string | null }[];
};
/** How this company's two-person onboarding steps are approved, and whether the caller may approve them alone (the owner, in owner mode). */
export type OnboardingApprovalMode = { approval: "second_admin" | "owner"; self_approve: boolean };
/** Where an application sends its access questions, and how far shadow has got (present in SHADOW and PILOT). */
export type OnboardingConnection = {
  api_base: string;
  evaluation_url: string;
  token_url: string;
  stage: string | null;
  shadow: {
    observations: number;
    observations_needed: number;
    context_coverage: number;
    context_coverage_needed: number;
    r3_context_coverage: number;
    r3_context_coverage_needed: number;
    agreement: number | null;
    agreement_needed: number;
    false_allows: number;
    ready: boolean;
  } | null;
};
/** What an identity provider publishes about itself, found from its address (CR-038). */
export type ProviderSettings =
  | { protocol: "OIDC"; issuer: string; authorization_endpoint: string; token_endpoint: string; jwks: { keys: Record<string, unknown>[] } }
  | { protocol: "SAML"; entity_id: string; sso_url: string; certificates: string[] };
/** What this person can do in billing (ZID-COM-CL-002). The server decides every call; this only decides what the page shows. */
export type BillingAccess = {
  permissions: string[];
  catalogue: { key: string; label: string }[];
  bundles: Record<BillingBundle, string[]>;
  enforced: boolean;
};
export type BillingBundle = "BILLING_ADMIN" | "BILLING_VIEWER" | "INVOICE_READER";
export type BillingGrant = {
  grant_id: string;
  grantee_principal_id: string;
  bundle: BillingBundle;
  permissions: string[];
  reason: string;
  granted_by: string;
  expires_at: Iso | null;
  created_at: Iso;
  revoked_at: Iso | null;
};
export type BillingState = {
  state: string;
  reason: string;
  safety_hold: boolean;
  since: string | null;
  version: number;
  restricted: string[];
  never_restricted: string[];
};
export type BillingOrderLine = { seq: number; sku: string; plan: string | null; quantity: string; net_price: string };
export type BillingOrder = {
  order_id: string;
  account_id: string;
  tenant_id: string;
  plan: string;
  status: string;
  pilot: boolean;
  pilot_ends_at: Iso | null;
  effective_from: Iso;
  term_months: number;
  amends_order_id: string | null;
  activated_at: Iso | null;
  lines: BillingOrderLine[];
  [k: string]: unknown;
};
export type UsageBudget = { budget_id: string; meter: string | null; monthly_limit: string; currency: string | null; thresholds: number[] };
export type BudgetIn = { meter: string | null; monthly_limit: string; currency: string | null; thresholds: number[] };

/* Operator commercial surface (ZID-COM-001; CR-024 to CR-030). */
export type ActivationRefund = { order_id: string; tenant_id: string; amount: string; currency: string; payment_ref: string | null; reason: string | null; decided_at: string | null };
export type PriceBookSummary = { price_book_id: string; version: number; currency: string; status: string; effective_from: string | null; approved_at: string | null; note: string | null };
export type PriceBookLine = { sku: string; plan: string; unit_rate: string; unit_divisor: number; included_quantity: number | null; minimum_amount?: string | null; unit?: string | null };
export type PriceBookDetail = PriceBookSummary & { lines: PriceBookLine[] };
export type AssuranceException = { exception_id: string; kind: string; ref: string; tenant_id: string | null; detail: unknown; status: string; raised_at: string };
/** Something the payment provider reported that needs a person: a refund, a dispute, or a payment that names no invoice of the customer. */
export type PaymentEvent = {
  event_id: string;
  event_type: string;
  outcome: "NEEDS_REVIEW" | "UNMATCHED";
  detail: string | null;
  received_at: string;
  tenant_id: string | null;
  invoice_id: string | null;
  amount: string | null;
  currency: string | null;
  resolved: boolean;
  resolution: string | null;
};
export type VendorCostRow = Record<string, unknown>;
/** The answer to checking a signed audit pack against the trusted evidence keys. */
export type AuditPackVerdict = { valid: boolean; reason?: string; kid?: string; issuer?: string };

/* Email contacts (Communication Estate; CR-031). Addresses are returned masked, never in full. */
export type EmailContactPurpose = "OWNER" | "BILLING" | "SECURITY" | "TECHNICAL";
export type EmailContact = { contact_id: string; purpose: EmailContactPurpose; address: string };

/* Organization claims (WF-13B; ED-03, ED-04). The applicant sees four independent checks and one outcome, never a list entry. */
export type OrgClaimCandidate = {
  jurisdiction: string;
  registration_number: string;
  legal_name: string;
  status: string;
  /** Ranking only: no candidate is ever chosen for the applicant. */
  confidence: number;
  register: string;
};
export type OrgClaimSearch = { candidates: OrgClaimCandidate[]; manual_review_available: boolean };
export type OrgClaimCreate = {
  country: string;
  registration_number: string;
  register_id?: string | null;
  legal_name: string;
  entity_form?: string | null;
  lei?: string | null;
};
export type OrgClaimCheckState = "COMPLETED" | "PENDING" | "NEEDS_ACTION" | "HELD" | "STALE" | "RESTRICTED" | "DECLINED";
export type OrgClaimOutcome = "VERIFIED" | "PROVISIONAL" | "NEEDS_EVIDENCE" | "IN_REVIEW" | "RESTRICTED" | "DECLINED" | "STALE" | "WITHDRAWN" | "ABANDONED";
/** Where a claim is in its own life, apart from what its checks say: not yet sent, sent for review, or closed. */
export type OrgClaimState = "DRAFT" | "SUBMITTED" | "WITHDRAWN" | "ABANDONED";
/** How the person says they act for the company (WF-13B-02). Only an officer is what the register says; the others rest on a mandate. */
export type OrgClaimRelationship = "DIRECTOR_OR_OFFICER" | "DELEGATED_EMPLOYEE" | "EXTERNAL_REPRESENTATIVE";
export type OrgClaimEstablished = { tenant_status: string; organization_id: string | null; legal_name: string | null; established: boolean };
export type OrgClaimStatus = {
  claim_id: string;
  jurisdiction: string;
  registration_number: string;
  state: OrgClaimState;
  outcome: OrgClaimOutcome;
  /** Local and test environments only: the claim reads as verified from the claimant's identity check alone, with no company check or review. */
  development_shortcut?: boolean;
  relationship: OrgClaimRelationship | null;
  submitted_at: Iso | null;
  closed_at: Iso | null;
  checks: { company: OrgClaimCheckState; person_assurance: OrgClaimCheckState; authority: OrgClaimCheckState; risk: OrgClaimCheckState };
  company: { status: string; review_reason: string | null; reasons: string[]; expires_at: Iso | null };
  screening: { status: string; next_action: string; expires_at: Iso | null; tasks: OrgClaimTask[] } | null;
};
/** Something the customer can do next. Codes, counts and plain words only: never a name or any watchlist content. */
export type OrgClaimTask = { code: string; count: number; text: string };
export type OrgClaimAppealKind = "APPEAL" | "CORRECTION";
export type OrgClaimAppeal = { kind: OrgClaimAppealKind; statement: string };
export type OrgClaimProofKind =
  | "CERTIFICATE_OF_INCORPORATION"
  | "REGISTER_EXTRACT"
  | "LETTER_OF_AUTHORITY"
  | "OWNERSHIP_CHART"
  | "BOARD_RESOLUTION"
  | "SIGNING_MATRIX"
  | "OTHER";
export type OrgClaimProof = {
  proof_id: string;
  kind: OrgClaimProofKind;
  media_type: string;
  size_bytes: number;
  label: string;
  scan_status: "CLEAN" | "NOT_SCANNED";
  content_hash: string;
  uploaded_at: Iso;
  /** Only on the upload answer for a certificate or register extract: what the platform's own reading found, compared with the claim. Evidence, never a verification. */
  read?: OrgClaimProofRead;
};
export type OrgClaimProofRead = {
  verdict: "CONSISTENT" | "CONTRADICTS" | "NOT_ENOUGH";
  reading: string;
  document?: string;
  number?: "MATCH" | "MISMATCH" | "NOT_READ";
  name?: "MATCH" | "MISMATCH" | "NOT_READ";
  incorporated_on?: string | null;
};
/** A document on an approval request (WF-013C AT-12). The list carries metadata only; opening one is for an approver and is audited. */
export type ApprovalAttachmentKind = "SUPPORTING_DOCUMENT" | "AUTHORITY_DOCUMENT" | "IDENTITY_REPROOF_RECORD" | "OTHER";
export type ApprovalAttachment = {
  attachment_id: string;
  kind: ApprovalAttachmentKind;
  media_type: string;
  size_bytes: number;
  label: string;
  scan_status: "CLEAN" | "NOT_SCANNED";
  content_hash: string;
  uploaded_at: Iso;
  access_ends_at: Iso;
};
export type ApprovalAttachments = { request_id: string; attachments: ApprovalAttachment[]; limit: number; may_attach: boolean; may_open: boolean };
export type OrgClaimProofs = { claim_id: string; proofs: OrgClaimProof[]; limit: number };
export type OrgClaimParty = {
  ref: string;
  kind?: "PERSON" | "ENTITY";
  name: string;
  birth?: string | null;
  countries?: string[];
  identifiers?: string[];
};
export type OrgClaimHolding = {
  owner: string;
  owned?: string;
  shares_pct?: string | null;
  votes_pct?: string | null;
  controls?: ("APPOINT_BOARD_MAJORITY" | "SIGNIFICANT_INFLUENCE" | "OTHER_CONTROL" | "NOMINEE" | "TRUST")[];
};
export type OrgClaimScreening = {
  relationship?: OrgClaimRelationship;
  /** A letter of authority, board resolution or signing matrix uploaded to this claim, for a delegated employee or an outside representative. */
  mandate_proof_id?: string | null;
  mandate_expires_at?: string | null;
  representative: OrgClaimParty;
  directors?: OrgClaimParty[];
  owners?: OrgClaimParty[];
  holdings?: OrgClaimHolding[];
  elevated_risk?: boolean;
};

/** What "New request" asks for, per governed action (WF-013C screen 2). The server still classifies the action, checks who may ask and decides who must approve. */
export type ApprovalFormFieldKind = "member" | "owner" | "text" | "textarea" | "select";
export type ApprovalFormField = {
  key: string;
  label: string;
  kind: ApprovalFormFieldKind;
  required: boolean;
  help: string | null;
  max_length: number;
  options: { id: string; label: string }[];
};
export type ApprovalForm = {
  action_id: string;
  level: "A1" | "A2";
  label: string;
  summary: string;
  target: { from: string | null; prefix: string; literal: string | null };
  fields: ApprovalFormField[];
};
export type ApprovalForms = { forms: ApprovalForm[] };
export type OrgApprovalCreate = { action_id: string; target_id: string; payload: Record<string, string>; reason: string };
/* ---------------------------------------------------------------------------------------------------------------------------------------------
 * Customer billing workspace, commitment approval and Zoiko's finance authority (ZID-UX-COM-008/009/010; ZID-COM-CL-004; CR-054).
 * Money is a decimal string or whole minor units; the server decides every state and every next action.
 * ------------------------------------------------------------------------------------------------------------------------------------------- */

export type BillingOverviewState = "FREE" | "PENDING_APPROVAL" | "ACTIVE" | "PAST_DUE" | "PILOT" | "RESTRICTED";
export type NextActionKind = "VIEW_PLANS" | "COMPLETE_APPROVAL" | "PAY_INVOICE" | "CONTACT_SALES" | "NONE";
export type BillingOverview = {
  as_of: string;
  state: BillingOverviewState;
  message: string;
  billing_state: string;
  safety_hold: boolean;
  restricted: string[];
  plan: { code: string | null; source: string | null; effective_from: string | null; term_months: number | null; contract_ref: string | null };
  next_action: { kind: NextActionKind; label: string; target: string | null; ref: string | null; reason: string };
  payer: { legal_name: string | null; country: string | null; currency: string | null; issuer: string | null };
  pilot: { ends_at: string | null; automatic_conversion: false } | null;
  recent_activity: { kind: "INVOICE"; ref: string | null; invoice_id: string; at: string | null; amount: string; currency: string; status: "PAID" | "OPEN" }[];
};

export type TaxIdType = "VAT" | "GST" | "EIN" | "COMPANY_REGISTRATION" | "OTHER";
export type BillingProfile = {
  version: number;
  payer: { legal_name: string | null; country: string | null; currency: string | null; change_note: string };
  address: { line1: string; line2: string; city: string; region: string; postal_code: string; country: string } | null;
  tax_ids: { type: TaxIdType; value: string; verification: "UNVERIFIED" }[];
  contacts: { invoice_email: string; notices_email: string; tax_document_email: string } | null;
  contact_verification: "UNVERIFIED";
  updated_at: string | null;
};
export type BillingProfileIn = {
  expected_version: number;
  address: Record<string, string>;
  tax_ids: { type: string; value: string }[];
  contacts: Record<string, string>;
};
export type BillingProfileVersion = { version: number; changed_fields: string[]; changed_by: string; changed_at: string };

export type CommitmentTier = "T1" | "T2" | "T3";
export type CommitmentState = "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELED";
export type CommitmentRequest = {
  request_id: string;
  order_id: string;
  currency: string;
  term_months: number;
  monthly: string;
  exposure: string;
  exposure_usd_minor: number;
  tier: CommitmentTier;
  needs: string[];
  review_required: boolean;
  reviewed_at: string | null;
  snapshot_hash: string;
  policy_version: string;
  state: CommitmentState;
  requested_by: string;
  created_at: string;
  expires_at: string;
  approved_at: string | null;
  approval_expires_at: string | null;
  approvals: { authority: "OWNER" | "BILLING_SPEND" | "PROCUREMENT_APPROVER"; principal_id: string; decision: "APPROVE" | "REJECT"; decided_at: string }[];
};
export type CommitmentDelegateKind = "BILLING_SPEND" | "PROCUREMENT_APPROVER";
export type CommitmentDelegate = {
  delegate_id: string;
  principal_id: string;
  kind: CommitmentDelegateKind;
  cap_usd: string;
  effective_from: string;
  effective_to: string | null;
  revoked_at: string | null;
};

export type FinanceKind = "CREDIT_NOTE" | "PILOT_CREDIT";
export type FinanceState = "PENDING_APPROVAL" | "APPROVED" | "APPLYING" | "APPLIED" | "APPLICATION_FAILED" | "REJECTED" | "EXPIRED" | "CANCELED";
export type FinanceRequest = {
  request_id: string;
  kind: FinanceKind;
  tenant_id: string;
  invoice_id: string | null;
  currency: string;
  amount: string;
  usd_minor: number;
  exposure_usd_minor: number;
  category: "CORRECTION" | "GOODWILL" | "PILOT";
  reason: string;
  snapshot_hash: string;
  policy_version: string;
  required_roles: string[];
  state: FinanceState;
  requested_by: string;
  created_at: string;
  expires_at: string;
  approved_at: string | null;
  approval_expires_at: string | null;
  approvals: { role: string; principal_id: string; decision: "APPROVE" | "REJECT"; decided_at: string }[];
  result: Record<string, unknown> | null;
};
export type FinanceTier = { max_usd: string | null; roles: string[] };
export type FinancePolicy = {
  version: string;
  content_hash: string;
  effective: boolean;
  awaiting: string[];
  signatures: { role: string; signed_at: string }[];
  environment: string;
  locked: boolean;
  unit: "USD";
  request_roles: string[];
  execute_roles: string[];
  request_valid_days: number;
  approval_valid_days: number;
  credit_note: { window_days: number; tiers: FinanceTier[] };
  pilot_credit: { window_days: number; max_days: number; tiers: FinanceTier[] };
};
export type FinanceAppointment = {
  appointment_id: string;
  principal_id: string;
  role: string;
  effective_from: string;
  effective_to: string | null;
  revoked_at: string | null;
};
export type FinanceOverview = {
  as_of: string;
  counts: { pending_approval: number; approved_unexecuted: number; failed_application: number; expiring_soon: number; payment_events_open: number };
  mine: string[];
  policy: { version: string; effective: boolean; awaiting: string[] };
  exceptions: { key: string; severity: "warn" | "danger"; text: string }[];
  definitions: string;
};
export type FinanceOpenIn = {
  kind: FinanceKind;
  target_tenant_id: string;
  invoice_id?: string | null;
  amount: string;
  currency: string;
  category: "CORRECTION" | "GOODWILL" | "PILOT";
  reason: string;
  ends_at?: string | null;
  evidence_ref?: string | null;
  disposition?: "INVOICE_BALANCE" | "REFUND" | "CUSTOMER_BALANCE" | "OUT_OF_BAND" | null;
  program_ref?: string | null;
  sponsor?: string | null;
};
export type EligibilityGate = { key: string; label: string; passed: boolean; detail: string; remedy: string | null };
export type Eligibility = {
  tenant_id: string;
  classification: "FREE" | "COMMERCIAL" | "PILOT_NON_BILLABLE" | "INTERNAL";
  state: "NOT_ELIGIBLE" | "ELIGIBLE" | "AWAITING_APPROVAL" | "ACTIVE";
  gates: EligibilityGate[];
  blockers: string[];
  schedule: { order_id: string; quote_hash: string; effective_from: string; pilot_ends_at: string | null } | null;
  commitment_state: string | null;
  note: string;
};

/* Progressive verification (ZID-EDR-006). The tier a person is at, what the next one needs, and their claims with how each was made. */
export type VerificationTierName = "NONE" | "R0" | "R1" | "R2" | "R3";
export type VerificationClaimView = {
  claim: string;
  method: string;
  source: string | null;
  policy: { level: string; profile: string | null; version: number | null };
  scope: string[];
  issued_at: string | null;
  valid_until: string | null;
  revocation: { state: string; revoked_at: string | null; assertion_id: string };
};
export type VerificationTier = {
  policy_version: string;
  tier: VerificationTierName;
  label: string;
  meaning: string;
  next_tier: Exclude<VerificationTierName, "NONE"> | null;
  missing: { check: string; why: string }[];
  reasons: string[];
  facts: { email_proved: boolean; identity_proofed: boolean; company_verified: boolean; strong_authentication: boolean; fresh_strong_sign_in: boolean };
  claims: VerificationClaimView[];
  alternatives: { id: string; for: string[]; effect: string; status: string }[];
  never_identity_proof: string[];
  note: string;
};

// ---- Payment operations (ZID-UX-COM-009 F03, F04; CR-058)
export type RefundState = "REQUESTED" | "APPROVED" | "EXECUTION_PENDING" | "PROCESSOR_PENDING" | "PARTIAL" | "SUCCEEDED" | "FAILED" | "UNKNOWN" | "REJECTED" | "EXPIRED" | "CANCELED";
export type RefundCategory = "DUPLICATE_PAYMENT" | "CORRECTION" | "CANCELLATION" | "CONTRACT_RIGHT" | "GOODWILL";
export type RefundCase = {
  refund_id: string;
  tenant_id: string;
  invoice_id: string;
  payment_attempt_id: string;
  issuing_entity: string;
  currency: string;
  amount_minor: number;
  usd_minor: number;
  exposure_usd_minor: number;
  category: RefundCategory;
  reason: string;
  evidence_ref: string | null;
  credit_note_id: string | null;
  snapshot_hash: string;
  policy_version: string;
  required_roles: string[];
  state: RefundState;
  label: string;
  never_claim: string;
  requested_by: string;
  created_at: string;
  expires_at: string;
  approved_at: string | null;
  approval_expires_at: string | null;
  approvals: { role: string; principal_id: string; decision: "APPROVE" | "REJECT"; decided_at: string }[];
  processor: { refund_ref: string | null; state: string | null; amount_minor: number | null; last_verified_at: string | null; failure_code: string | null; attempts: number };
  overlap: { open_dispute: boolean; credited_minor: number; refunded_other_minor: number; refundable_minor: number };
};
export type RefundOpenIn = {
  target_tenant_id: string;
  payment_attempt_id: string;
  amount: string;
  category: RefundCategory;
  reason: string;
  evidence_ref?: string | null;
  credit_note_id?: string | null;
};
export type DisputeEvidenceItem = "terms_or_consent" | "transaction_proof" | "usage_proof" | "communications" | "cancellation_refund_chronology";
export type DisputeCase = {
  dispute_id: string;
  provider_dispute_ref: string;
  merchant_ref: string;
  livemode: boolean;
  tenant_id: string | null;
  invoice_id: string | null;
  issuing_entity: string | null;
  payment_intent_ref: string | null;
  reason: string | null;
  amount_minor: number;
  currency: string;
  provider_status: string;
  deadline: { due_by: string | null; zone: string; seconds_left: number | null; status: "OK" | "NEAR" | "PASSED" | "NO_DEADLINE_FROM_PROCESSOR" };
  state: string;
  owner: string | null;
  strategy: "ACCEPT" | "CONTEST" | null;
  strategy_reason: string | null;
  evidence: { version: number; items: Record<DisputeEvidenceItem, { present: boolean; ref: string | null }>; complete: boolean };
  submission: { reference: string | null; recorded_at: string | null; verified_at: string | null };
  outcome: { provider_status: string | null; fee_minor: number | null; funds_withdrawn_minor: number };
  overlap: { refunds: { state: string; amount_minor: number }[] };
  timeline: { kind: string; actor: string | null; detail: Record<string, unknown>; at: string }[];
  last_synced_at: string;
};
export type ReconRow = {
  kind: "PAYMENT" | "REFUND" | "DISPUTE";
  item_id: string;
  ref: string;
  amount_minor: number;
  currency: string;
  occurred_on: string;
  state: "UNMATCHED" | "PROVISIONALLY_MATCHED" | "MATCHED";
  reason: string | null;
  difference_minor: number;
  payout_ref: string | null;
  legs: string[];
  resolved: boolean;
};
export type ReconBoard = {
  issuing_entity: string;
  period: string;
  state: "OPEN" | "READY_FOR_CLOSE" | "CLOSED" | "REOPEN_REQUESTED";
  rows: ReconRow[];
  summary: { UNMATCHED: number; PROVISIONALLY_MATCHED: number; MATCHED: number; items: number };
  settlement_note: string;
  totals_minor: Record<string, { payments: number; refunds: number; disputes: number }>;
  blockers: string[];
  ready: boolean;
  snapshot_hash: string;
  signed_roles?: string[];
  receipt?: Record<string, unknown>;
};
export type ReconException = { exception_id: string; source_kind: string; source_id: string; reason: string; owner: string | null; resolution: string | null; resolved_at: string | null };

/** Read-only trust registry (WF-04): scope and state only, no contacts, price references or certificate references. */
export type TrustRegistry = {
  issuer_registry_version: string;
  packs_version: string;
  tier_rules: number;
  issuers: {
    issuer_id: string;
    kind: string;
    name: string;
    countries: string[];
    claim_types: string[];
    authority: string;
    revoked_on: string | null;
    outreach_contacts_registered: number;
    accreditations: number;
  }[];
  sources: {
    source_id: string;
    claim_types: string[];
    countries: string[];
    purposes: string[];
    processing_regions: string[];
    valid_from: string;
    valid_until: string | null;
    sensitivity: string;
    primary: boolean;
    simulated: boolean;
  }[];
  policies: { policy_id: string; version: number; country: string; subdivision: string | null; profession_code: string; service_code: string }[];
  certifications: { record_id: string; entity: string; scheme: string; country: string; service_code: string; valid_from: string; valid_until: string | null; status: string }[];
};

/** WF-02 dossier: one person's current credential checks, the decisions made from them and any appeals. */
export type CredentialDossier = {
  subject: string;
  checks: {
    check_id: string;
    claim_type: string;
    decision: string;
    checked_at: string | null;
    assertion: Record<string, unknown>;
  }[];
  decisions: { decision_id: string; kind: string; policy_id: string; policy_version: number; outcome: string; reasons: string[]; restrictions: string[]; check_ids: string[]; consumer: string | null; created_at: string }[];
  appeals: { appeal_id: string; check_id: string; state: string; created_at: string; resolved_at: string | null }[];
};

/** An uploaded evidence document as the cell describes it (ZID-INT-002 section 05): metadata only, never the bytes. */
export type UploadManifest = {
  evidence_ref: string;
  state: "AWAITING_CONTENT" | "READY" | "REJECTED" | "DELETED" | "EXPIRED";
  reject_reason: string | null;
  declared_type: string;
  sniffed_type: string | null;
  size_bytes: number;
  sha256: string;
  metadata_stripped: boolean;
  scan: { state: "NOT_SCANNED" | "CLEAN" | "INFECTED" | "SCAN_ERROR"; scanner: string | null };
  expires_at: string;
  erased_at: string | null;
};
export type UploadList = { items: UploadManifest[] };
export type UploadAnnounce = { declared_type: string; size_bytes: number; sha256: string; subject_ref?: string };
export type UploadAnnounced = { evidence_ref: string; upload_url: string; upload_token: string; token_expires_at: string; retained_until: string; max_bytes: number };
export type DocumentCheckResult = { check: string; status: "VERIFIED" | "NOT_VERIFIED" | "INDETERMINATE" | "UNAVAILABLE"; reasons: string[]; detail: Record<string, unknown> };
export type DocumentValidation = { validation_id: string | null; sha256: string; sniffed_type: string | null; stored: boolean; document_results: DocumentCheckResult[]; limitations: string[] };

// ---------------------------------------------------------------------------------------- regulatory (ZID-RCG-003; CR-067 to CR-085)
// The operator plane's regulatory records. Hand-written like the rest of the operator surface: these routes are operator-only and are not in a
// published contract.

export type PackStatus = "DRAFT" | "APPROVED" | "RETIRED";
export type PackSource = { instrument: string; url: string; status: "LAW" | "GUIDANCE" | "PROPOSAL"; retrieved_at: Iso; effective_from: Iso; effective_to?: Iso | null; owner: string };
export type PackRules = { evidence_minimum: string[]; alternate_routes?: string[]; lawful_basis?: string; validity_days?: number | null; escalation?: string | null };
export type PolicyPack = {
  pack_id: string;
  version: number;
  status: PackStatus;
  market: { country: string; subdivision: string | null; profession: string | null; product: string; purpose: string };
  sources: PackSource[];
  rules: PackRules;
  author: string;
  pack_hash: string;
  approvals: Record<string, { approver: string; pack_hash: string }>;
  missing_approvals: string[];
};
export type PackDraftIn = { pack_id: string; country: string; subdivision?: string | null; profession?: string | null; product: string; purpose: string; sources: PackSource[]; rules: PackRules };
export type PackApproverRole = "legal" | "grc" | "security" | "business_owner";

export type ReleaseStatus = "DRAFT" | "FROZEN" | "RELEASEABLE" | "LAUNCHED" | "WITHDRAWN";
export type ReleaseGate = { gate: string; title: string; owner_role: string; status: "PASSED" | "FAILED" | null; evidence_ref: string | null; recorded_by: string | null; recorded_at: Iso | null };
export type SupplierScope = { entity: string; scheme: string; country: string; service_code: string };
export type MarketRelease = {
  release_id: string;
  version: number;
  status: ReleaseStatus;
  market: { country: string; product: string; purpose: string };
  author: string;
  release_hash: string | null;
  pack_refs: { pack_id: string; version: number; pack_hash: string }[];
  supplier_scopes: SupplierScope[];
  gates: ReleaseGate[];
  blockers: string[];
  approvals: Record<string, { approver: string; release_hash: string }>;
  missing_approvals: string[];
};
export type GateRole = "legal" | "regulatory_ops" | "security" | "privacy" | "qa" | "vendor_mgmt";
export type ReleaseApproverRole = "legal" | "security_grc" | "qa" | "product_owner";
export type MarketStatus = { open: boolean; reason: string; release: { release_id: string; version: number } | null };

export type CertStatus = "ACTIVE" | "SUSPENDED" | "WITHDRAWN" | "EXPIRED";
export type AttestationIn = SupplierScope & {
  scheme_version: string;
  certificate_ref: string;
  component_only?: boolean;
  status: CertStatus;
  valid_from: string;
  valid_until?: string | null;
  register_url: string;
  register_entry_id: string;
  cab?: string | null;
  fetched_at: Iso;
  proof_hash: string;
  evidence_locator: string;
};
export type AttestationRow = SupplierScope & { outcome: "CERTIFIED" | "HOLD"; reason: string; register_url: string | null; fetched_at: Iso | null };
export type EntitlementIn = {
  provider: string;
  country: string;
  capability: string;
  purpose: string;
  status: "GRANTED" | "SUSPENDED" | "WITHDRAWN";
  effective_at: Iso;
  valid_from: Iso;
  valid_until?: Iso | null;
  contract_ref: string;
  terms_ref: string;
};
export type EntitlementRow = { provider: string; country: string; capability: string; purpose: string; outcome: "ENTITLED" | "DENIED"; reason: string; contract_ref: string | null; valid_until: Iso | null };

export type SuspensionScope = {
  provider: string;
  service_id?: string | null;
  scheme?: string | null;
  country?: string | null;
  subdivision?: string | null;
  purpose?: string | null;
  profession?: string | null;
  credential_class?: string | null;
};
export type SuspensionTrigger = "CERTIFICATION_WITHDRAWN" | "PROVIDER_BREACH" | "ENTITLEMENT_WITHDRAWN" | "TRUST_LIST_STALE" | "LICENSE_SUSPENDED" | "MANUAL";
export type SuspensionVersion = {
  suspension_id: string;
  version: number;
  status: "ACTIVE" | "LIFTED";
  scope: SuspensionScope;
  trigger: SuspensionTrigger;
  reason: string;
  evidence_ref: string;
  effective_at: Iso;
  recorded_by: string;
};
export type SuspensionImpact = { markets: string[]; entitlements: string[]; certifications: string[]; claims: string[] };
export type SuspensionApplyIn = { suspension_id: string; scope: SuspensionScope; trigger: SuspensionTrigger; reason: string; evidence_ref: string };

/* ----------------------------------------------------------------------------------- biometric methods (ZID-DTV-WF-04; CR-107, CR-108) */

export type BiometricMethod = "FACE_MATCH" | "LIVENESS";
export type BiometricState = "OFF" | "PAUSED" | "LAB_ONLY" | "PILOT_GATED" | "PRODUCTION_ELIGIBLE";
export type BiometricScope = {
  method: BiometricMethod;
  engine_id: string;
  engine_version: string;
  country: string;
  purpose: string;
  /** The one tenant a PILOT_GATED scope runs for; null for every tenant. */
  pilot_tenant: string | null;
};
export type BiometricMethodVersion = {
  scope_id: string;
  version: number;
  state: BiometricState;
  scope: BiometricScope;
  dpia_ref: string | null;
  evaluation_ref: string | null;
  reason: string;
  evidence_ref: string;
  recorded_by: string;
  proposed_by: string | null;
  proposal_id: string | null;
  effective_at: Iso;
  /** CR-110: the share of people a PRODUCTION_ELIGIBLE scope runs for, and the digest of the approved decision. */
  exposure_percent: number;
  decision_hash: string | null;
};
export type BiometricProposal = {
  proposal_id: string;
  scope_id: string;
  scope: BiometricScope;
  from_version: number;
  target: "LAB_ONLY" | "PILOT_GATED" | "PRODUCTION_ELIGIBLE";
  dpia_ref: string | null;
  evaluation_ref: string | null;
  reason: string;
  evidence_ref: string;
  proposed_by: string;
  proposed_at: Iso;
  status: "OPEN" | "APPROVED" | "REJECTED";
  decided_by: string | null;
  decided_at: Iso | null;
  decision_reason: string | null;
  exposure_percent: number;
};
export type BiometricMethodsList = {
  items: { current: BiometricMethodVersion; versions: BiometricMethodVersion[] }[];
  open_proposals: BiometricProposal[];
};
export type BiometricProposeIn = {
  scope_id?: string;
  scope: Omit<BiometricScope, "pilot_tenant"> & { pilot_tenant?: string | null };
  target: BiometricProposal["target"];
  dpia_ref?: string | null;
  evaluation_ref?: string | null;
  reason: string;
  evidence_ref: string;
  exposure_percent?: number;
};
export type BiometricStepDownIn = {
  target: "PAUSED" | "OFF" | "LAB_ONLY" | "PILOT_GATED" | "PRODUCTION_ELIGIBLE";
  reason: string;
  evidence_ref: string;
  /** Narrowing a production rollout (CR-110). */
  exposure_percent?: number;
};

export type BiometricMethodAnswer = {
  method: BiometricMethod;
  available: boolean;
  state: BiometricState;
  reasons: string[];
  consent_required: boolean;
  scope_ref: string | null;
  dpia_package_id: string | null;
  dpia_blockers: string[];
  breaches: string[];
  missing_retention: string[];
  /** The tenant's latest independent evaluation of this engine version, and why it does not count (CR-109). */
  evaluation_id: string | null;
  evaluation_blockers: string[];
  exposure_percent: number;
  /** CR-113 (QA-041): the live approval names another build or capture SDK; refused only in enforce mode. */
  release_drift?: string[];
  release_attestation?: string | null;
};
export type BiometricRelease = { build_ref: string; sdk_version: string; binding: "report" | "enforce" };
export type BiometricEligibility = {
  country: string;
  purpose: string;
  engine: Record<string, unknown> | null;
  available_methods: BiometricMethod[];
  methods: BiometricMethodAnswer[];
  policy_hash: string;
  notes: string[];
  release?: BiometricRelease | null;
};

/* ----------------------------------------------------------------------------------- privacy control plane v2 (ZID-PRI-004; CR-108) */

export type DpiaState = "DRAFT" | "IN_REVIEW" | "APPROVED" | "REJECTED" | "SUPERSEDED";
export type DpiaRelease = { ready: boolean; blockers: string[] };
export type DpiaRisk = {
  risk_id: string;
  title: string;
  mitigation: string;
  owner: string;
  inherent_likelihood: number;
  inherent_impact: number;
  residual_likelihood: number;
  residual_impact: number;
  inherent_score?: number;
  inherent_band?: string;
  residual_score?: number;
  residual_band?: string;
  problems?: string[];
};
export type DpiaPackageSummary = {
  package_id: string;
  market: string;
  product: string;
  version: string;
  controller: string;
  purpose_code: string;
  provider_service_ref: string | null;
  model_version: string | null;
  state: DpiaState;
  approved_until: Iso | null;
  consultation_ref: string | null;
  created_by: string;
  created_at: Iso;
  risk_count: number;
  gates_signed: string[];
  release: DpiaRelease;
};
export type DpiaPackage = Omit<DpiaPackageSummary, "risk_count" | "gates_signed"> & {
  risks: DpiaRisk[];
  gates: Record<string, Record<string, { signer: string; evidence_uri: string; signed_at?: string }>>;
  decision_rationale: string | null;
  decided_by: string | null;
};
export type DpiaPackageIn = {
  market: string;
  product: string;
  version: string;
  controller: string;
  purpose_code: string;
  provider_service_ref?: string | null;
  model_version?: string | null;
};
export type ReleaseGateView = DpiaRelease & {
  market: string;
  product: string;
  version: string;
  package_id: string | null;
  gates: Record<string, { name: string; accountable_roles: string[] }>;
};
export type BreachClock = { jurisdiction: string; state: "RUNNING" | "OVERDUE" | "NOTIFIED" | "NOT_SET_BY_COUNSEL"; hours: number | null; due_at: Iso | null; notified_at: Iso | null };
export type BreachCase = {
  breach_id: string;
  summary: string;
  severity: "LOW" | "MODERATE" | "HIGH" | "CRITICAL";
  jurisdictions: string[];
  subjects_estimate: number | null;
  detected_at: Iso;
  state: "OPEN" | "CONTAINED" | "CLOSED";
  capture_state: "NOT_PAUSED" | "PAUSED" | "RELEASED";
  capture_paused_by: string | null;
  capture_paused_at: Iso | null;
  capture_released_by: string | null;
  capture_released_at: Iso | null;
  capture_release_reason: string | null;
  opened_by: string;
  closed_at: Iso | null;
  clocks: BreachClock[];
  /** CR-113 (QA-034): the data classes the incident touched; added to, never removed. */
  data_classes?: string[];
};
export type ErasureScope = "ALL" | "PROVIDERS_ONLY" | "FIRST_PARTY_ONLY";
export type ErasureTask = {
  task_id: string;
  target_kind: "PROVIDER" | "FIRST_PARTY_STORE" | "BACKUP";
  target_ref: string;
  state: "PENDING" | "ACKNOWLEDGED" | "FAILED" | "HELD";
  receipt_hash: string | null;
  failure: string | null;
  due_at: Iso | null;
  asset_classes: string[];
  /** CR-113 (QA-023): how often the provider was asked again, when next, and when the alert was raised. */
  attempts?: number;
  next_attempt_at?: Iso | null;
  alerted_at?: Iso | null;
};
export type ErasureClassState = {
  asset_class: string;
  state: "PURGED" | "PENDING" | "HELD" | "FAILED";
  tasks: number;
  outstanding: { target_kind: string; target_ref: string; state: string }[];
};
export type ErasureJob = {
  job_id: string;
  subject_ref: string;
  scope: ErasureScope;
  reason: string;
  legal_basis: string;
  state: "PENDING" | "PARTIAL" | "COMPLETE" | "FAILED" | "HELD";
  created_at: Iso;
  completed_at: Iso | null;
  tasks: ErasureTask[];
  biometric_classes: ErasureClassState[];
};
export type ErasurePlanItem = {
  target: string;
  object_type: string;
  asset_class: string | null;
  action: string;
  why?: string;
  provider?: string;
  sla_days?: number | null;
};
export type ErasurePreview = {
  subject_ref: string;
  scope: ErasureScope;
  dry_run: true;
  plan: ErasurePlanItem[];
  backups: { action: "ATTEST_LAST"; window_days: number | null } | null;
  statement: string;
};
/** The privacy-safe verification session as the person sees it (ZID-PRI-004 section 07; WF-04 B01, B02; CR-108). Never an image or a score. */
export type SessionAlternative = { id: string; label: string; biometric: false };
export type SubjectSessionStatus = {
  session_ref: string;
  state: "OPEN" | "CANCELED";
  capture_enabled: boolean;
  route: "CAMERA" | "ALTERNATIVE" | "NONE";
  blockers: string[];
  purpose_decision_id: string;
  purpose_code: string;
  region: string | null;
  alternative_mode: string | null;
  /** CR-110: where the assisted route stands, once the person chose it. */
  assisted_state: "WAITING" | "IN_PROGRESS" | "DONE" | "NOT_COMPLETED" | null;
  notice: { notice_version: string; content_uri: string | null; content_hash: string | null; acknowledged: boolean } | null;
  alternatives_available: boolean;
  alternatives: SessionAlternative[];
  statement: string;
  checked_at: Iso;
};
/** An appeal or human review of a decision (ZID-PRI-004 sections 10, 11; WF-04 B08; CR-109). A reversal waits for a checker. */
export type ReviewState = "OPEN" | "AWAITING_CHECKER" | "UPHELD" | "OVERTURNED" | "CORRECTED";
export type DecisionReview = {
  review_id: string;
  decision_ref: string;
  kind: "APPEAL" | "HUMAN_REVIEW";
  subject_ref: string;
  statement: string;
  state: ReviewState;
  requested_by: string;
  reviewer: string | null;
  rationale: string | null;
  correction_ref: string | null;
  proposed_outcome: "OVERTURNED" | "CORRECTED" | null;
  checker: string | null;
  checked_at: Iso | null;
  created_at: Iso;
  due_at: Iso;
  decided_at: Iso | null;
};
/** The person's own view of a review: no reviewer or checker identity (CR-110). */
export type MyReview = Pick<DecisionReview, "review_id" | "decision_ref" | "kind" | "state" | "statement" | "correction_ref" | "created_at" | "due_at" | "decided_at" | "checked_at">;
/** Model inventory with fairness (ZID-PRI-004 PR-16; WF-04 B07; CR-110). Aggregate measures only, each with its confidence interval. */
export type ModelKind = "face_match" | "pad" | "fraud" | "document";
export type FairnessState = "NOT_RUN" | "DATA_INSUFFICIENT" | "PASS_WITH_LIMITS" | "FAIL" | "REEVALUATION_DUE";
export type Measure = { value: number; ci_low: number; ci_high: number };
export type ModelVersion = {
  model_id: string;
  model_ref: string;
  version: string;
  kind: ModelKind;
  provider_service_ref: string | null;
  live_features: string[];
  state: "REGISTERED" | "ACTIVE" | "HELD" | "SUSPENDED" | "RETIRED";
  state_reason: string | null;
  promoted_at: Iso | null;
  fairness: { state: FairnessState; reasons: string[] };
  latest_evaluation: {
    evaluation_id: string;
    evaluator_ref: string;
    independent: boolean;
    cohorts: number;
    metrics: Record<string, Measure | boolean>;
    accessible_alternative_confirmed: boolean;
    report_ref: string | null;
    created_at: Iso;
  } | null;
};
export type ModelEvaluationIn = {
  model_ref: string;
  version: string;
  evaluator_ref: string;
  independent: boolean;
  cohorts: number;
  metrics: Record<string, Measure | boolean>;
  accessible_alternative_confirmed: boolean;
  audit_attributes?: string[];
  report_ref?: string | null;
};
/** Transfer ledger and subprocessor changes (ZID-PRI-004 sections 08, 16, 19; WF-04 B04; CR-110). Default deny. */
export type TransferLedgerEntry = {
  decision_id: string;
  processor_id: string;
  purpose_code: string;
  data_category: string;
  origin_country: string;
  destination_country: string;
  remote_access_countries: string[];
  mechanism: string | null;
  allowed: boolean;
  reason_codes: string[];
  evaluated_at: Iso;
};
export type TransferIn = {
  processor_id: string;
  purpose_code: string;
  data_category: "D0" | "D1" | "D2" | "D3" | "D4";
  origin_country: string;
  destination_country: string;
  remote_access_countries?: string[];
};
export type SubprocessorChangeKind = "SUBPROCESSOR_ADDED" | "SUBPROCESSOR_REMOVED" | "LOCATION_ADDED" | "LOCATION_REMOVED";
export type SubprocessorChange = {
  change_id: string;
  processor_id: string;
  kind: SubprocessorChangeKind;
  detail: string;
  state: "PENDING_APPROVAL" | "APPROVED" | "REJECTED";
  notified_at: Iso;
  requested_by: string;
  decided_by: string | null;
  decided_at: Iso | null;
  created_at: Iso;
};
/** WF-04 B12 governance monitor (CR-110): counts and what needs attention; no personal data. */
export type BiometricOversight = {
  window_days: number;
  breaches_pausing_capture: number;
  dpia: { approved: number; expiring_30d: number; lapsed: number };
  models: { active: number; held: number };
  reviews: { open: number; awaiting_checker: number; overdue: number };
  erasure: { open: number; held: number; failed: number; provider_tasks_overdue: number };
  consent: { withdrawn_30d: number };
  subprocessor_changes_waiting: number;
  transfers_refused_30d: number;
  retention_missing_classes: string[];
  attention: string[];
  measured_at: Iso;
};
/** The assisted route's back office (WF-04 B02; CR-110). No reason for choosing the alternative is stored or shown. */
export type AssistedItem = {
  session_ref: string;
  subject_principal_id: string | null;
  purpose_code: string;
  country: string | null;
  alternative_mode: string | null;
  assisted_state: "WAITING" | "IN_PROGRESS" | "DONE" | "NOT_COMPLETED";
  assisted_by: string | null;
  assisted_at: Iso | null;
  assisted_outcome_ref: string | null;
  assisted_reason_code: string | null;
  created_at: Iso;
};
export type BreachIn = {
  summary: string;
  severity: BreachCase["severity"];
  jurisdictions: string[];
  subjects_estimate?: number | null;
  detected_at: Iso;
  /** Omitted: HIGH and CRITICAL pause biometric capture by default (CR-108). */
  pause_biometric_capture?: boolean | null;
  data_classes?: string[];
};
export type MarketRetirement = {
  country: string;
  retired: BiometricMethodVersion[];
  withdrawn_proposals: string[];
  already_off: string[];
};

export type IssuerStatement = {
  issuer_id: string;
  seq: number;
  statement_id: string;
  statement: "UPDATE" | "CORRECTION";
  reason: string | null;
  record: Record<string, unknown>;
  source_url: string;
  source_hash: string;
  evidence_locator: string;
  recorded_by: string;
  recorded_at: Iso;
};
/** POST /v1/regulatory/issuers (IssuerStatementIn; not in the generated credential spec). A full issuer record plus the official source it came
 * from; dates are YYYY-MM-DD. An UPDATE only adds or ends facts going forward; changing what an earlier statement said about the past is a CORRECTION. */
export type IssuerKind = "UNIVERSITY" | "REGULATOR" | "EMPLOYER" | "INSURER" | "VENDOR" | "ACCREDITOR";
export type IssuerStatementIn = {
  issuer_id: string;
  issuer_kind: IssuerKind;
  authority: "OFFICIAL" | "UNRECORDED";
  countries?: string[];
  claim_types?: string[];
  names: { name: string; from?: string | null; until?: string | null }[];
  accreditations?: { body: string; program?: string | null; from: string; until?: string | null }[];
  revoked_on?: string | null;
  successor_of?: string | null;
  statement?: "UPDATE" | "CORRECTION";
  reason?: string | null;
  source_url: string;
  source_hash: string;
  evidence_locator: string;
};
/** The saved statement, with what a CORRECTION changed about the past (empty for an update). */
export type IssuerStatementRecorded = IssuerStatement & { retroactive_changes: string[] };
export type AwardCheck = { outcome: "PASS" | "REVIEW"; reasons: string[]; accreditation: string; name_at_award?: string | null };

export type ClaimJudgement = {
  template_id: string;
  version: number;
  text: string;
  audiences: string[];
  countries: string[];
  template_hash: string;
  publishable: boolean;
  reasons: string[];
};
export type ProofBundle = { digest: string; publishable: boolean; reasons: string[]; judged_at: Iso; template: Record<string, unknown>; evidence: Record<string, unknown>[] };

export type ConformanceOutcome = "PASS" | "FAIL" | "NOT_RUN" | "PARTIAL" | "GAP";
export type ConformanceCase = { case: string; title: string; expected: string; coverage: "COVERED" | "PARTIAL" | "GAP"; tests: string[]; note: string };
export type ConformanceMatrix = { matrix_hash: string; counts: Record<string, number>; as_of: Iso; cases: ConformanceCase[] };
export type ConformanceRun = {
  run_id: string;
  commit: string;
  environment: string;
  matrix_hash: string;
  started_at: Iso;
  finished_at: Iso;
  recorded_by: string;
  run_hash: string;
  results: { case: string; outcome: ConformanceOutcome; passed: string[]; failed: string[]; not_run: string[] }[];
  reviews: { reviewer: string; decision: "ACCEPTED" | "REJECTED"; note_ref: string; reviewed_at: Iso }[];
  p0_exit: { met: boolean; reasons: string[]; counts: Record<string, number> };
};

export type PrivacyOverlayView = {
  country: string;
  version: number;
  overlay_hash: string;
  home_regions: string[];
  transfer_regions: Record<string, string>;
  provider_regions: Record<string, string>;
  biometrics: "ALLOWED" | "CONSENT_REQUIRED" | "PROHIBITED";
  dpia_required: boolean;
  problems: string[];
};
export type RouteCheck = { allowed: boolean; reasons: string[]; region: string | null; mechanism: string | null };
export type MonitoringCheck = { allowed: boolean; reasons: string[]; approval_id: string | null };
export type RightToRentCheck = { outcome: string; route: string | null; reasons: string[]; zoiko_evidence: boolean; statutory_excuse_by_zoiko: boolean; retention: string | null };
export type TrustListsView = {
  lists: { name: string; authenticated: boolean; problem: string | null; services: number; qualified_ca_services: number; granted_now: number }[];
  anchors: number;
  tsa_roots: number;
  crls: number;
  qualified_status_assessable: boolean;
  reasons: string[];
  timestamp_authority_configured: boolean;
  interpretation: string;
};
export type RegisterSource = { source_id: string; kind: string; scheme: string; base_url: string; contract_ref: string | null; usable: boolean; reasons: string[] };
export type RegisterRefresh = SupplierScope & { source_id: string; listed: boolean; snapshot_id: string | null; proof_hash: string | null; outcome: "CERTIFIED" | "HOLD"; reason: string };
export type OfferingStatus = "AVAILABLE" | "NOT_AVAILABLE";
export type ServiceOffering = {
  offering_id: string;
  title: string;
  wording: string;
  capabilities: string[];
  biometric: boolean;
  cases: string[];
  code: string[];
  countries: { country: string; status: OfferingStatus; reasons: string[] }[];
};
export type RegulatoryReadiness = {
  countries: {
    country: string;
    capabilities: { capability: string; availability: string; provider: string | null; would_refuse: string[]; missing: string[] }[];
  }[];
  modes: Record<string, string>;
  empty_configuration: string[];
};

/** Opening a feature for a country (CR-127). Each feature is one regulated capability with its own six approvals; an approval is the operator's record of a decision the owner made elsewhere. */
export type CountryApprovalKey = "legal_assessment" | "supplier_coverage" | "privacy_and_data" | "security_and_resilience" | "local_support" | "signoff";
export type CountryFeatureKey = "REGISTRATION" | "INDIVIDUAL_VERIFICATION" | "BUSINESS_VERIFICATION" | "CREDENTIAL_ISSUANCE" | "ENTERPRISE_USE";
export type CountryOpening = {
  capability: CountryFeatureKey;
  version: number;
  status: "OPEN" | "CLOSED";
  effective_from: string | null;
  approvals: Partial<Record<CountryApprovalKey, string>>;
  home_cells: string[];
  reason: string | null;
  recorded_by: string;
  recorded_at: string | null;
};
export type CountryFeatureState = {
  capability: CountryFeatureKey;
  /** What the registry file says: DISABLED, SANDBOX (counts in local and test only) or ENABLED. */
  file_status: "DISABLED" | "SANDBOX" | "ENABLED";
  /** What applies here right now, after the recorded decision. */
  in_force: "DISABLED" | "SANDBOX" | "ENABLED";
  opening: CountryOpening | null;
};
export type CountryOpeningRow = { country: string; name: string; wave: number | null; features: CountryFeatureState[] };
export type CountryOpeningsView = {
  environment: string;
  features: { capability: CountryFeatureKey; label: string; unlocks: string }[];
  approvals: { key: CountryApprovalKey; label: string; owner: string }[];
  cells: { cell_id: string; region: string | null; country: string | null }[];
  countries: CountryOpeningRow[];
};
export type CountryOpeningIn =
  | { capability: CountryFeatureKey; action: "OPEN"; effective_from: string; approvals: Partial<Record<CountryApprovalKey, string>>; home_cells: string[]; reason?: string }
  | { capability: CountryFeatureKey; action: "CLOSE"; reason?: string };

/** The Face lab (CR-128): a measurement of how alike two photographs are, for evaluation. Never an approval. */
export type FaceLabStatus = {
  configured: boolean;
  engine: { id: string; version: string };
  liveness: { available: boolean; note: string };
  gate: { mode: string; approvable: boolean; problems: string[] };
  note: string;
};
export type FaceLabSide = { faces: number; detection: number | null; face_ratio: number | null };
export type FaceLabResult = {
  cosine: number;
  score: number;
  above_suggested_threshold: boolean;
  recommended_cosine_threshold: number;
  probe: FaceLabSide;
  reference: FaceLabSide;
  engine: string;
  liveness: { available: boolean };
  note: string;
};

/** Independent assurance (ZID-UAT-005). Statuses come from signed evidence only; nothing here is typed in by a person. */
export type UatCase = { case_id: string; suite: string; risk: string; dependency: "OWN" | "THIRD_PARTY" | "PEOPLE"; assertion: string; status: string; manifest_id: string | null };
export type UatRequirements = { baseline: { baseline_id: string; version: string; source_sha256: string } | null; cases: UatCase[]; counts: Record<string, number> };
export type UatGate = { gate: string; passed: boolean; reasons: string[] };
export type UatDecision = { status: "GO" | "NO_GO"; reason: string; p0_required: number; p0_pass: number; open_critical: number; missing: string[]; gates: UatGate[] };
export type UatPreview = { release_id: string; candidate_status: string; locked_reason: string | null; decision: UatDecision; signatures: { role: string; decision: string }[] };
export type ProviderLabFault = { fault: string; expected: string; observed: string; held: boolean };
export type ProviderLabReport = { faults: ProviderLabFault[]; all_held: boolean; note: string };
export type UatFixture = { fixture_id: string; name: string; revision: number; state: "DRAFT" | "APPROVED" | "REJECTED"; content_sha256: string; author: string; reviewer: string | null; created_at: string };
export type UatFixtureList = { items: UatFixture[] };
export type UatRun = { run_id: string; environment_id: string; build_sha256: string; release_unit: Record<string, unknown>; tester_principal: string; created_at: string; events: number; manifests: number };
export type UatRunList = { items: UatRun[] };
export type UatManifestView = { manifest_id: string; case_id: string; verdict: string; manifest_sha256: string; problems?: string[]; manifest?: Record<string, unknown>; jws?: string };
export type UatEvidence = { run_id: string; audience: string; build_sha256: string; events: number; chain_head: string; chain_problems: string[]; manifests: UatManifestView[]; note: string };
export type UatVerifyResult = { valid: boolean; problems: string[]; locked_releases: string[] };
export type UatVerifyIn = { manifest_id?: string; manifest?: Record<string, unknown>; jws?: string; events?: Record<string, unknown>[] };
export type UatDecisionIn = { role: "QA_LEAD" | "CTO" | "SECURITY_GRC" | "PRIVACY" | "COUNTRY_LEGAL"; decision: "APPROVE" | "REJECT"; rationale: string };
export type UatSigned = { release_id: string; candidate_status: string; decision: UatDecision; jws: string };
export type UatRollbackIn = { scope: Record<string, string>; reason: string };
export type UatRolledBack = { release_id: string; rollback_id: string; candidate_status: string; scope: Record<string, unknown>; evidence_kept: boolean; provider_route_closed: boolean | null };
export type UatRiskConfirmed = { baseline_id: string; version: string; confirmed: number; confirmed_by: string };

/* ---- the solution builder (ZID-COM-WF-2026-11 WF-C01, WF-C02, B06): offers by market, dependencies, preview, drafts and quote requests. No price is ever on these. */
export type OfferState = "AVAILABLE" | "CONTACT_SALES" | "NOT_ELIGIBLE" | "RESEARCH_ONLY" | "MARKET_REQUIRED";
export type OfferReason = { cause: string; text: string };
export type OfferMarketTerms = { seller_entity: string; merchant_route: string; currency: string; tax_class: string; data_region: string };
export type OfferCard = {
  family: string;
  name: string;
  unit: string;
  sales_qualifier: string;
  regulated: boolean;
  depends_on: string[];
  sellable: boolean;
  next_action: string;
  causes: string[];
  refusals: { code: string; cause: string; dimension: string | null }[];
  state: OfferState;
  reasons: OfferReason[];
  market: OfferMarketTerms | null;
  provider_attribution?: string;
};
export type OffersView = { market_matrix_version: number | null; market: string | null; payer_country: string | null; operating_region: string | null; cards: OfferCard[]; note: string };
export type OffersQuery = { market?: string; payer_country?: string; operating_region?: string };
export type CapabilityDependencies = { version: string; families: { family: string; depends_on: string[]; needs_provider: boolean; regulated: boolean }[] };
export type SolutionPurpose = "ONBOARDING" | "COMPLIANCE" | "PROFESSIONAL_HIRING" | "REGULATED_TRANSACTION" | "MARKETPLACE";
export type SolutionVolumes = Record<string, Partial<Record<"expected_units" | "rechecks_per_year" | "manual_reviews", number>>>;
export type SolutionIn = {
  purpose: SolutionPurpose;
  families: string[];
  subject_country?: string | null;
  payer_country?: string | null;
  operating_region?: string | null;
  volumes: SolutionVolumes;
};
export type QuotePreview = {
  binding: false;
  configurable: boolean;
  scope: SolutionIn;
  scope_hash: string;
  market_matrix_version: number | null;
  problems: { code: string; family: string; needs: string }[];
  families: OfferCard[];
  request_quote: string[];
  assumptions: string[];
  estimate: null;
};
export type SolutionDraft = {
  draft_id: string;
  purpose: SolutionPurpose;
  families: string[];
  subject_country: string | null;
  payer_country: string | null;
  operating_region: string | null;
  volumes: SolutionVolumes;
  scope_hash: string;
  status: "DRAFT" | "QUOTE_REQUESTED";
  state: "DRAFT" | "QUOTE_REQUESTED" | "EXPIRED";
  created_at: string;
  expires_at: string;
  quote_requested_at: string | null;
};

/* ---- the market matrix, its staged rollout and the deal desk queue (ZID-COM-WF-2026-11 WF-I01, WF-I05; operator plane) */
export type MarketMatrixStatus = "DRAFT" | "ACTIVE" | "SUPERSEDED" | "WITHDRAWN";
export type MarketMatrixSummary = { matrix_id: string; version: number; status: MarketMatrixStatus; effective_from: string | null; activated_at: string | null; note: string | null };
export type MarketMatrixRow = {
  family: string; country: string; seller_entity: string; merchant_route: string; currency: string; tax_class: string; data_region: string;
  offer_ref: string; legal_basis_ref: string; release_evidence_ref: string; payment_configured: boolean; tax_configured: boolean;
  provider_rights_ref: string | null; regulated_status_ref: string | null; provider_rights_expires: string | null; regulated_status_expires: string | null;
};
export type MarketMatrixDetail = MarketMatrixSummary & {
  rows: MarketMatrixRow[];
  incomplete_rows: { family: string; country: string; problems: string[] }[];
  rights_alerts: { family: string; country: string; kind: string; state: "EXPIRED" | "EXPIRING"; expires: string }[];
  approvals: { role: string; approver: string; approved_at: string }[];
  missing_approvals: string[];
};
export type MarketRolloutItem = { family: string; country: string; stage: string | null; decided_by: string | null; decided_at: string | null };
export type QuoteRequestItem = {
  draft_id: string; tenant_id: string; purpose: string; families: string[]; subject_country: string | null; payer_country: string | null;
  operating_region: string | null; volumes: Record<string, Record<string, number>>; scope_hash: string; quote_requested_at: string | null;
  quote_snapshot: { exceptions?: { family: string; state: string; causes: string[] }[]; assumptions?: string[] } | null;
};

export type FxRateSet = { set_id: string; version: number; status: "DRAFT" | "ACTIVE" | "SUPERSEDED" | "WITHDRAWN"; source_ref: string; as_of: string; rates: Record<string, string> };

export type FamilyTextRevision = { rev_id: string; family: string; version: number; name: string; unit: string; sales_qualifier: string; reason: string; status: "DRAFT" | "ACTIVE" | "SUPERSEDED" | "WITHDRAWN"; created_by: string; created_at: string | null; activated_by: string | null; activated_at: string | null };
export type FamilyTexts = { baseline: Record<string, { name: string; unit: string; sales_qualifier: string }>; items: FamilyTextRevision[] };

/* ---- the public pricing page (ZID-UX-PRICE-004): structure, availability by market and inquiries. No price on any of these. */
export type TierCode = "ESSENTIALS" | "BUSINESS" | "ENTERPRISE";
export type PackagingState = "included" | "optional" | "quote";
export type PublicPricingPage = {
  version: string;
  packaging_status: "PROPOSED" | "APPROVED";
  approval: { signed_by: string[]; missing: string[] };
  notice: { version: string; text: string; status: "ACTIVE" | "DRAFT" };
  pricing_status: string | null;
  families: { family: string; name: string; unit: string; sales_qualifier: string; regulated: boolean; depends_on: string[] }[];
  tiers: { code: TierCode; label: string; fit: string; cta_kind: string; cta_label: string; family_state: Record<string, PackagingState> }[];
  paths: { code: "SANDBOX" | "PARTNER"; label: string; text: string; cta_kind: string; cta_label: string }[];
  use_cases: { code: string; label: string; families: string[] }[];
  disclosures: { price_basis: string; tax: string; commitment: string; no_surprise: string; plan_change: string; exit: string };
};
export type PricingPeriod = "MONTHLY" | "ANNUAL";
export type PublicRate = { currency: string; charge_unit: string; included_units: number; unit_amount: string; minimum_amount: string | null; minimum_model: "FLOOR" | "PLATFORM_FEE" | null };
export type PublicState = "AVAILABLE" | "SALES_ASSISTED" | "RESTRICTED" | "NOT_AVAILABLE" | "MARKET_REQUIRED";
export type PublicAvailability = {
  market_matrix_version: number | null;
  as_of: string;
  dimensions: { subject_country: string | null; payer_country: string | null; operating_region: string | null; issuer_country: { value: string | null; judged: boolean; note: string } };
  cards: {
    family: string;
    name: string;
    unit: string;
    regulated: boolean;
    state: PublicState;
    reason_codes: string[];
    reasons: { cause: string; text: string }[];
    terms: { seller_entity: string; currency: string; data_region: string } | null;
    provider_attribution: string | null;
    rates: Partial<Record<PricingPeriod, PublicRate>>;
  }[];
  rate_card: { version: number; effective_from: string | null; both_periods: boolean } | null;
  released_markets: string[];
  released_regions: string[];
  note: string;
};
export type PublicEstimateIn = { subject_country?: string; payer_country?: string; issuer_country?: string; operating_region?: string; period: PricingPeriod; volumes: Record<string, number> };
export type PublicEstimateLine = {
  family: string; name: string; period: PricingPeriod; currency: string; charge_unit: string; monthly_volume: number; volume: number; included_units: number; billable_units: number;
  usage: string; minimum_amount: string | null; minimum_model: "FLOOR" | "PLATFORM_FEE" | null; amount: string; formula: string;
  annual_comparison: { currency: string; twelve_months: string; annual: string; saving: string } | null;
};
export type PublicEstimate = {
  binding: false; rate_card: { version: number; effective_from: string | null } | null; period: PricingPeriod; lines: PublicEstimateLine[]; totals: Record<string, string>;
  quote_required: { family: string; name: string; state: string; reason_codes: string[]; reasons: { cause: string; text: string }[] }[];
  quote_hash: string; as_of: string; valid_until: string; tax: { state: string; note: string }; assumptions: string[]; no_rate_card: string | null;
};
export type PublicContextLink = { token: string; expires_at: string };
export type PublicContext = { subject_country: string | null; payer_country: string | null; issuer_country: string | null; operating_region: string | null; expires_at: string };
export type LeadKind = "ENTERPRISE" | "PARTNER";
export type PublicLeadIn = {
  kind: LeadKind;
  contact_name: string;
  work_email: string;
  company: string;
  country: string;
  region?: string | null;
  capabilities: string[];
  volume_band: "UNDER_1K" | "FROM_1K_TO_10K" | "FROM_10K_TO_100K" | "OVER_100K" | "UNSURE";
  entities?: number | null;
  referral_source?: string | null;
  security_needs?: string | null;
  partner_type?: "RESELLER" | "REFERRAL" | "EMBEDDED_ISV" | null;
  notice_version: string;
  website?: string | null;
};
export type PublicLeadOut = { reference: string; status: string; duplicate: boolean; message: string };

export type RateCardSummary = { card_id: string; version: number; status: "DRAFT" | "ACTIVE" | "SUPERSEDED" | "WITHDRAWN"; effective_from: string | null; activated_at: string | null; note: string | null };
export type RateCardLine = {
  family: string; country: string; period: PricingPeriod; currency: string; charge_unit: string; included_units: number; unit_amount: string;
  minimum_amount: string | null; minimum_model: "FLOOR" | "PLATFORM_FEE" | null; source_ref: string;
};
export type RateCardDetail = RateCardSummary & {
  lines: RateCardLine[]; approvals: { role: "CFO" | "CCO" | "FOUNDER"; approver: string; approved_at: string | null }[]; missing_approvals: string[];
};
export type PricingPageState = { version: string; content_hash: string; status: "PROPOSED" | "APPROVED"; signed_by: string[]; missing: string[] };
export type PublicNoticeRow = { notice_id: string; kind: string; version: string; body: string; status: "DRAFT" | "ACTIVE" | "WITHDRAWN" | "SUPERSEDED"; created_by: string; created_at: string | null; activated_by: string | null; activated_at: string | null };
export type PublicNotices = { served: { version: string; text: string; status: "ACTIVE" | "DRAFT" }; items: PublicNoticeRow[] };

export type SalesLeadStatus = "RECEIVED" | "QUALIFIED" | "SCOPED" | "REVIEW" | "OFFER_SENT" | "CLOSED" | "SPAM";
export type SalesLead = {
  lead_id: string; kind: "ENTERPRISE" | "PARTNER"; partner_type: string | null; contact_name: string; work_email: string; company: string; country: string; region: string | null;
  capabilities: string[]; volume_band: string; entities: number | null; referral_source: string | null; security_needs: string | null; notice_version: string;
  status: SalesLeadStatus; status_reason: string | null; created_at: string | null; updated_at: string | null;
};

/* Agent governance (ZID-AG-WF-002, CR-105). Money is always a decimal string, never a number. */
export type GovSlot = "SECURITY" | "AUTHORITY" | "PRIVACY" | "FINANCE";
export type GovRequestState = "PENDING" | "CLARIFICATION" | "APPROVED" | "REJECTED" | "EXPIRED" | "INVALIDATED" | "APPLIED";
export type GovAttentionItem = {
  rule: string;
  severity: "P0" | "P1" | "P2";
  agent_id: string | null;
  detail: string;
  alert_key?: string;
  state?: "OPEN" | "ACKNOWLEDGED" | "ROUTED" | "RESOLVED";
  tracked?: boolean;
  occurrences?: number;
  first_seen?: Iso | null;
  sla_due?: Iso | null;
  sla_breached?: boolean;
  acknowledged_by?: string | null;
  owner?: string | null;
  route?: string | null;
};
export type GovAgentRow = {
  agent_id: string;
  name: string | null;
  sponsor: string | null;
  state: string;
  risk: string | null;
  policy_version: number | null;
  spend_today: string;
  last_evidence_at: Iso | null;
  governed: boolean;
};
export type GovRequest = {
  request_id: string;
  ref?: string;
  agent_id: string;
  kind: "PUBLISH" | "SCOPE_EXTENSION" | "AI_ENABLE" | "BUDGET_INCREASE" | "QUOTA_INCREASE" | "RESTORE" | "PRIVILEGE_MUTATION";
  subject?: "POLICY" | "BUDGET" | "RESTORE";
  state: GovRequestState;
  requested_by: string;
  policy_hash: string;
  base_policy_hash: string | null;
  delta_hash: string;
  delta?: Record<string, unknown>;
  required_slots: GovSlot[];
  open_slots: GovSlot[];
  signatures: { slot: GovSlot; signer: string; signed_at: Iso }[];
  created_at: Iso;
  expires_at: Iso;
  expired: boolean;
  evidence_ref?: string | null;
  decided_reason?: string | null;
  can_review?: boolean;
  review_unavailable?: "SEPARATION_OF_DUTIES" | null;
};
export type GovOverview = {
  kpis: {
    active_agents: number;
    pending_approvals: number;
    denied_calls_24h: number;
    shadow_denials_24h: number;
    budget_used_today: string;
    risk_signals: number;
  };
  agents: GovAgentRow[];
  requires_attention: GovAttentionItem[];
  pending: GovRequest[];
  telemetry: { state: "CURRENT" | "STALE" | "UNKNOWN"; last_ingest: Iso | null; message: string | null };
  mode: "off" | "shadow" | "enforce";
  as_of: Iso;
  timezone: string;
  counts_source: string;
};
export type GovLease = {
  grant_id: string;
  tool_id: string;
  action: string;
  resource_kind: string;
  resource_selector: string;
  purpose: string;
  region: string;
  data_class: string;
  valid_until: Iso;
  approval_ref: string | null;
  state: "REQUESTED" | "EFFECTIVE" | "EXPIRED";
  ceiling_source?: string;
  effective?: boolean;
};
export type GovPolicy = {
  policy_id: string;
  agent_id: string;
  version: number;
  state: "DRAFT" | "PENDING_APPROVAL" | "ACTIVE" | "SUPERSEDED" | "REJECTED";
  risk_tier: "LOW" | "MEDIUM" | "HIGH";
  region: string;
  policy_hash: string;
  ai_enabled: boolean;
  quotas: Record<string, number | boolean>;
  expires_at: Iso | null;
  grants: GovLease[];
};
export type GovBaseline = {
  allowed_tools: string[];
  allowed_regions: string[];
  ai_providers: string[];
  quotas: Record<string, number | boolean>;
  schedules_allowed: boolean;
  frozen: boolean;
};
export type GovEffectiveAccess = {
  agent_id: string;
  policy: GovPolicy | null;
  approval: GovRequest | null;
  effective_leases: GovLease[];
  requested: GovPolicy[];
  company_baseline: GovBaseline | null;
  frozen: boolean;
  hard_denies: string[];
  hierarchy: string[];
  evaluated_at: Iso;
};
export type GovSimulation = {
  simulation: true;
  synthetic: true;
  effective: false;
  decision: "ALLOW" | "DENY";
  code: string | null;
  reason: string;
  policy_hash: string | null;
  trace: { gate: string; result: string }[];
  note: string;
};
export type GovBudgetRow = {
  budget_id: string;
  name: string;
  scope: string;
  currency: string;
  window: "DAY" | "MONTH" | "CALL";
  hard_cap: string;
  committed: string;
  reserved: string;
  available: string;
  level: "OFF" | "OK" | "WARN_70" | "WARN_90" | "EXHAUSTED";
  warn_pct: [number, number];
  revision: number;
  resets_at: Iso | null;
};
export type GovBudgetView = {
  agent_id: string;
  ceilings: GovBudgetRow[];
  pending: { budget_id: string; scope: string; period: string; currency: string; hard_cap: string; approval_request_id: string | null }[];
  ai_spend: string;
  external_spend: string;
  as_of: Iso;
  timezone: string;
};
export type GovEvent = {
  event_id: string;
  seq: number;
  agent_principal_id: string | null;
  action: string;
  outcome: "ALLOW" | "DENY" | "SHADOW_DENY" | "INFO";
  code: string | null;
  occurred_at: Iso;
  digest: string;
  prev_digest: string;
  tool_id?: string | null;
  resource?: string | null;
  region?: string | null;
  policy_hash?: string | null;
  correlation_id?: string;
  actor?: string | null;
  cost_receipt?: Record<string, unknown> | null;
};
export type GovEvents = { items: GovEvent[]; next_cursor: number | null; redacted: boolean };
export type GovMonitoring = {
  agent_id: string;
  name: string;
  state: string;
  summary: { allowed_24h: number; denied_24h: number; shadow_denied_24h: number };
  sponsor: { principal_id: string | null; active: boolean };
  expires_at: Iso | null;
  policy: GovPolicy | null;
  active_runs: { run_id: string; started_at: Iso; deadline_at: Iso; records: number; proposals: number }[];
  alerts: GovAttentionItem[];
  incident: { case_id: string; scope: string; generation: number; reason: string }[];
  telemetry: { state: "CURRENT" | "STALE" | "UNKNOWN"; last_ingest: Iso | null };
  as_of: Iso;
};
/** Who may sign agent-governance requests as Security, Authority, Privacy or Finance (CR-105 AG2-D02). */
export type GovDelegate = { principal_id: string; slot: GovSlot; granted_by: string; granted_at: Iso };
export type GovDelegates = { items: GovDelegate[]; slots: GovSlot[] };
export type GovFreeze = {
  case_id: string;
  scope: "AGENT" | "TENANT";
  agent_id: string | null;
  generation: number;
  initiator: string;
  reason: string;
  severity: "P0" | "P1" | "P2";
  issued_at: Iso;
  restored_at: Iso | null;
  restore_request_id: string | null;
  state: "FROZEN" | "RESTORED";
};
export type GovLeaseInput = {
  tool_id: string;
  resource_kind: string;
  resource_selector: string;
  purpose: string;
  region?: string;
  data_class?: "metadata" | "status" | "evidence_reference";
  valid_until: Iso;
};
export type GovDraftInput = {
  leases: GovLeaseInput[];
  quotas?: Record<string, number | boolean>;
  region: string;
  ai_enabled?: boolean;
  risk_tier?: "LOW" | "MEDIUM" | "HIGH";
  expires_at?: Iso | null;
};
export type GovBudgetInput = {
  scope: "COMPANY" | "WORKSPACE" | "AGENT" | "CALL";
  agent_id?: string | null;
  currency: string;
  period: "DAY" | "MONTH" | "CALL";
  hard_cap: string;
  warn_pct?: [number, number];
  reason_code: string;
};

/* The DAARA reference agent, its review queue and alert handling (ZID-AG-WF-001, CR-106) */
export type DaaraStage = "NOT_ENABLED" | "DRAFT" | "READY_TO_RUN_SANDBOX" | "SANDBOX_READY" | "PENDING_APPROVAL" | "ACTIVE";
export type DaaraTemplate = {
  template: string;
  purpose: string;
  rule_set_version: string;
  tools: { tool_id: string; action: string; side_effect: string; resource: string; why: string }[];
  does: string[];
  does_not: string[];
  release_default: string;
};
export type DaaraSetup = {
  agent_id: string;
  template: DaaraTemplate;
  enabled: boolean;
  stage: DaaraStage;
  blockers: string[];
  publish_blockers?: string[];
  setup: { label: string; rationale: string | null; warn_days: number; region: string; ai_explain: boolean; schedule: "MANUAL" | "DAILY"; config_hash: string; validated: boolean; sandbox_passed: boolean } | null;
  effective_scope?: { permitted: { tool: string; resource: string; purpose: string; region: string }[]; blocked: string[]; note: string };
  policy?: { version: number; hash: string } | null;
};
export type DaaraSetupInput = { label: string; rationale?: string | null; warn_days: number; region: string; ai_explain: boolean; schedule?: "MANUAL" | "DAILY" };
export type DaaraDryRun = { passed: boolean; synthetic: true; findings: string[]; expected: number; wrote: number };
export type ReviewProposal = {
  proposal_id: string;
  ref: string;
  agent_id: string;
  submitted_by: "AGENT";
  run_id: string;
  target: string;
  rule_id: string;
  rule_version: string;
  finding_code: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  confidence_basis: string;
  meaning: string;
  evidence_refs: string[];
  seen_count: number;
  status: "OPEN" | "DISMISSED" | "ASSIGNED" | "HANDED_OFF";
  created_at: Iso;
  decided_at: Iso | null;
  decision_reason: string | null;
  has_handoff: boolean;
  notice: string;
};
export type DaaraRun = {
  run_id: string;
  agent_id: string;
  mode: "LIVE" | "DRY_RUN";
  state: "COMPLETED" | "NEEDS_REVIEW" | "FAILED" | "DENIED" | "CANCELED" | "QUEUED";
  incomplete: boolean;
  checked: number;
  issues: number;
  proposals: number;
  privileges_changed: number;
  rule_set_version: string;
  policy_hash: string | null;
  summary: Record<string, unknown>;
  explanation: string | null;
  explanation_source: "FIXED" | "MODEL";
  reason_code: string | null;
  completed_at: Iso | null;
  replayed?: boolean;
};
export type DaaraRunDetail = DaaraRun & {
  timeline: { seq: number; at: Iso | null; step: string; outcome: string; code: string | null }[];
  policy_decisions: { seq: number; action: string; outcome: string; code: string | null; policy_hash: string | null }[];
  evidence: { complete: boolean; events: number; missing: string[] };
  proposals_for_run: ReviewProposal[];
  redacted: boolean;
  model_used: boolean;
};
export type ReviewQueue = { items: ReviewProposal[]; open: number; notice: string };
export type ReviewDecision = { status: string; handoff_digest: string | null; notice: string };

/* API-based agents (CR-093): a registered API client in place of a container image */
export type ApiClientCreate = { name: string; jwks: { keys: Record<string, string>[] }; audiences: string[]; source_ref?: string | null; provenance_ref?: string | null };
export type ApiClientRegistered = {
  build_id: string;
  name: string;
  artifact_digest: string;
  review_status: string;
  kind: "API_CLIENT";
  client_id: string;
  thumbprints: string[];
  audiences: string[];
  next: string;
};
export type ApiBinding = {
  agent_id: string;
  bound: boolean;
  binding: {
    binding_id: string;
    status: "ACTIVE" | "SUSPENDED" | "REVOKED";
    client_id: string;
    descriptor_digest: string;
    thumbprints: string[];
    audiences: string[];
    valid_to: Iso;
    created_at: Iso;
    revoke_reason: string | null;
  } | null;
  limits?: { max_risk_class: number; side_effects: string[]; attestation_cap: number; note: string };
  agent_suspended?: boolean;
};

/* ---------------------------------------------------------------- access readiness agent (CR-091, ZID-AG-WF-001) */

export type ReadinessSetupStatus = "DRAFT" | "SANDBOX_READY" | "PENDING_APPROVAL" | "APPROVED" | "PAUSED";
export type ReadinessScope = {
  resource_kinds: string[];
  max_records: number;
  warning_window_days: number;
  home_region: string;
  /** The optional AI explanation. Part of the digest an approver signs; refused while the cell has it switched off. */
  ai_explanation?: boolean;
};
export type ReadinessApproval = { approval_id: string; scope_digest: string; requested_by: string; approved_by: string | null; expires_at: Iso };
export type ReadinessSetup = {
  setup_id: string;
  agent_id: string;
  display_name: string;
  purpose: string;
  scope: ReadinessScope;
  rule_set_version: string;
  policy_version: string;
  status: ReadinessSetupStatus;
  draft_revision: number;
  /** The digest an approver signs. Any change to the scope is a different digest. */
  scope_digest: string;
  /** True only when the last passing dry run was on exactly this digest. */
  validated: boolean;
  paused_reason: string | null;
  approval: ReadinessApproval | null;
  /** What the agent may do, and what it never does; the screens show both as text. */
  permitted_tools: string[];
  blocked: string[];
  agent?: { status: string; sponsor_active: boolean; attested: boolean; blueprint: string };
};
export type ReadinessCreateIn = {
  agent_principal_id: string;
  display_name: string;
  resource_kinds?: string[];
  max_records?: number;
  warning_window_days?: number;
  ai_explanation?: boolean;
};
export type ReadinessPatchIn = {
  display_name?: string;
  resource_kinds?: string[];
  max_records?: number;
  warning_window_days?: number;
  ai_explanation?: boolean;
  expected_revision?: number;
};
export type ReadinessFinding = {
  rule_id: string;
  rule_version: string;
  finding_code: string;
  severity: string;
  confidence: string;
  target_ref: string;
  evidence_refs: string[];
  detail: Record<string, string | number | boolean>;
};
export type ReadinessDryRun = {
  synthetic: boolean;
  live_writes: number;
  passed: boolean;
  fixture: string;
  policy_version: string;
  policy_hash: string;
  rule_set_version: string;
  result_digest: string;
  rules_covered: string[];
  findings: ReadinessFinding[];
  unknown: number;
  setup_status: ReadinessSetupStatus;
};
export type ReadinessRunState = "QUEUED" | "PREFLIGHT" | "RUNNING" | "DENIED" | "COMPLETED" | "NEEDS_REVIEW" | "FAILED" | "CANCELED";
export type ReadinessRunSummary = {
  records_read?: number;
  findings?: number;
  unknown?: number;
  complete?: boolean;
  proposals_created?: number;
  proposals_existing?: number;
  privileges_changed?: number;
  result_digest?: string;
};
export type ReadinessRun = {
  run_id: string;
  agent_id: string;
  state: ReadinessRunState;
  attempt: number;
  mode: string;
  purpose: string;
  resource_selector: Record<string, unknown>;
  denial_codes: string[];
  summary: ReadinessRunSummary | null;
  rule_set_version: string;
  policy_hash: string;
  run_definition_hash: string;
  region: string;
  trace_id: string | null;
  worker_attestation_id: string | null;
  created_at: Iso;
  terminal_at: Iso | null;
  links: { self: string };
  /** Present only when the cell and the setup both opted in. Never authoritative: the summary is the result. */
  explanation?: ReadinessExplanation | null;
};
/** MODEL: a model's text that verified against the rule result. TEMPLATE: the fixed text (model off, failed, slow, unsupported claim, or the request refused by the usage and budget hook). */
export type ReadinessExplanation = {
  source: "MODEL" | "TEMPLATE";
  text: string;
  fallback_reason: "MODEL_NOT_CONFIGURED" | "MODEL_TIMEOUT" | "MODEL_UNAVAILABLE" | "UNSUPPORTED_CLAIM" | "BUDGET_POLICY_DENY" | null;
  provider: string | null;
  model: string | null;
  prompt_version: string;
  facts_digest: string;
  authoritative: false;
};
export type ReadinessProposalStatus = "OPEN" | "ASSIGNED" | "DISMISSED" | "HANDED_OFF";
export type ReadinessProposal = {
  proposal_id: string;
  submitted_by: { type: "agent"; agent_id: string };
  /** Masked to its kind and the first characters of its id. */
  target: string;
  rule_id: string;
  rule_version: string;
  finding_code: string;
  /** How serious the finding is. A different question from `confidence`. */
  severity: string;
  confidence: string;
  evidence_refs: string[];
  run_id: string;
  status: ReadinessProposalStatus;
  reviewed_by: string | null;
  assignee: string | null;
  created_at: Iso;
  handoff: { handoff_id: string; target_workflow: string; handoff_digest: string; signature: string; executes: boolean; signed_at: Iso } | null;
  notice: string;
};
export type ReadinessRunDetail = ReadinessRun & { proposals: ReadinessProposal[] };
export type ReadinessDecision = {
  decision_id: string;
  run_id: string | null;
  actor: string;
  subject: string;
  resource: string;
  action: string;
  purpose: string;
  outcome: "ALLOW" | "DENY";
  reason_code: string;
  policy_hash: string;
  decided_at: Iso;
  trace_id: string | null;
};
export type ReadinessEvidenceEnvelope = {
  event_id: string;
  run_id: string;
  seq: number;
  event_type: string;
  source_id: string;
  source_version: string;
  payload_digest: string;
  chain_digest: string;
  classification: string;
  region: string;
  retention_policy_ref: string;
  recorded_at: Iso;
};
export type ReadinessEvidence = { verified: boolean; integrity_exceptions: string[]; items: ReadinessEvidenceEnvelope[] };
/** WF-AG-03 "Export permitted evidence": references and digests only. `redacted` is true for a caller without the review role. */
export type ReadinessEvidenceExport = {
  schema: "zid.access-readiness.evidence.v1";
  run_id: string;
  agent_id: string;
  state: ReadinessRunState;
  rule_set_version: string;
  policy_hash: string;
  run_definition_hash: string;
  result_digest: string | null;
  region: string;
  exported_at: Iso;
  redacted: boolean;
  chain: { verified: boolean; integrity_exceptions: string[] };
  evidence: Omit<ReadinessEvidenceEnvelope, "event_id" | "run_id" | "retention_policy_ref">[];
  decisions: { action: string; resource: string; outcome: "ALLOW" | "DENY"; reason_code: string; policy_hash: string; decided_at: Iso }[];
  proposals: {
    proposal_id: string;
    rule_id: string;
    rule_version: string;
    finding_code: string;
    severity: string;
    status: ReadinessProposalStatus;
    target: string;
    evidence_refs: string[];
    evidence_ref_count: number;
  }[];
};

/* ------------------------------------------------------------- source of funds and wealth (DTV-28, CR-096) */

export type SofState = "DRAFT" | "INCOMPLETE" | "NEEDS_REVIEW" | "CLEARED" | "NOT_CLEARED_PENDING_APPEAL" | "APPEALED" | "NOT_CLEARED_FINAL" | "CANCELED";
export type SofDecision = "CLEAR" | "NOT_CLEAR" | "ASK_FOR_MORE";
export type SofQueueItem = {
  case_id: string;
  subject_principal_id: string;
  country: string;
  questionnaire_version: string;
  state: SofState;
  submitted_at: string | null;
  valid_until: string | null;
};
export type SofQuestion = {
  question_id: string;
  text: string;
  answer: "TEXT" | "CHOICE" | "AMOUNT" | "DATE" | "YES_NO";
  required: boolean;
  choices: string[];
  evidence_required_for: Record<string, string[]>;
};
export type SofQuestionnaire = { code: string; version: string; status: "SAMPLE" | "APPROVED"; content_hash: string; questions: SofQuestion[] };
export type SofDecisionRecord = {
  decision: SofDecision | "APPEAL_FILED";
  from_state: SofState;
  to_state: SofState;
  reason_codes: string[];
  decided_by: string;
  decided_at: string;
};
export type SofCase = {
  case_id: string;
  subject_principal_id: string;
  country: string;
  questionnaire: { code: string; version: string; content_hash: string };
  state: SofState;
  answers: Record<string, unknown>;
  evidence: { question_id: string; type: string; upload_id: string }[];
  problems: string[];
  submitted_at: string | null;
  valid_until: string | null;
  erased: boolean;
  decisions: SofDecisionRecord[];
  notice: string;
};
export type SofDecideIn = { decision: SofDecision; reason_codes: string[] };

/* ------------------------------------------------------------- verification workflows (DTV-30, CR-099) */

export type WfStatus = "DRAFT" | "APPROVED" | "RETIRED";
export type WfProblem = { code: string; where: string };
export type WfVersionSummary = { workflow_id: string; version: string; status: WfStatus; content_hash: string; author: string; approved_by: string | null; created_at: string };
export type WfVersion = {
  workflow_id: string;
  version: string;
  status: WfStatus;
  content_hash: string;
  definition: Record<string, unknown>;
  author: string;
  approved_by: string | null;
  approved_at: string | null;
  problems: WfProblem[] | null;
  notice: string;
};
export type WfCheckResult = { valid: boolean; parse_error: string | null; detail: string; problems: WfProblem[]; content_hash: string | null; publishable?: boolean };
export type WfSimulation = { steps: string[]; outcome: string; needs_person: string[]; side_effects: string };
export type WfRunStatus = "WAITING_FOR_CHECK" | "WAITING_FOR_REVIEW" | "ENDED";
export type WfRunSummary = {
  run_id: string;
  workflow_id: string;
  version: string;
  subject_principal_id: string;
  status: WfRunStatus;
  outcome: string | null;
  awaiting_check: string | null;
  awaiting_role: string | null;
  created_at: string;
  updated_at: string;
};

export type WfRunEvent = { step: string; kind: string; detail: string };
export type WfRun = WfRunSummary & {
  content_hash: string;
  step: string;
  results: Record<string, string>;
  events: WfRunEvent[];
  notifications: unknown[];
  erased: boolean;
  notice: string;
};

/* ------------------------------------------------------------- ownership chains (DTV-24, CR-102) */

export type OwnEntity = { id: string; kind: "PERSON" | "COMPANY" | "TRUST" | "LISTED_COMPANY" | "GOVERNMENT" };
export type OwnLink = { owner: string; owned: string; percent?: string; control?: boolean; evidenced?: boolean };
export type OwnGraphIn = { target: string; entities: OwnEntity[]; links: OwnLink[]; threshold?: string };
export type OwnCandidate = { entity_id: string; effective_percent: string; reasons: string[]; paths: number; status: "NEEDS_CONFIRMATION" | "NEEDS_EVIDENCE" };
export type OwnGap = { code: string; entity_id: string; effective_percent: string };
export type OwnFindings = {
  target: string;
  threshold: string;
  candidates: OwnCandidate[];
  gaps: OwnGap[];
  recognised_ends: string[];
  complete: boolean;
  recommendation: string;
  fallback: string | null;
  notes: string[];
};
export type OwnGraphSummary = { graph_id: string; target: string; version: number; content_hash: string; author: string; created_at: string };
export type OwnDecisionKind = "CONFIRMED" | "NOT_CONFIRMED" | "ESCALATED";
export type OwnDecisionIn = { entity_id: string; decision: OwnDecisionKind; reason: string; evidence_ref: string };
export type OwnDecision = { confirmation_id: string; entity_id: string; decision: OwnDecisionKind; reason: string; evidence_ref: string; reviewer: string; created_at: string };
export type OwnGraph = OwnGraphSummary & {
  threshold: string;
  entities: OwnEntity[];
  links: OwnLink[];
  findings: OwnFindings | null;
  findings_error?: string;
  confirmations: OwnDecision[];
};

/* ------------------------------------------------------------- WF01 result facts for one verification (screen A5, CR-107) */

export type ProofingCheckType = "document_validity" | "liveness_pad" | "face_match" | "chip_crypto";
export type ProofingCheckResult = "pass" | "fail" | "indeterminate" | "not_requested";
export type ProofingDecision = "verified_for_scope" | "review_required" | "more_evidence" | "indeterminate" | "rejected" | "pending";
export type ProofingFacts = {
  verification_id: string;
  scope: string;
  region: string;
  policy: { profile_id: string; version: number; level: string };
  expires_at: string;
  state: string;
  decision: ProofingDecision;
  assertions: string[];
  checks: { type: ProofingCheckType; result: ProofingCheckResult; method: string | null }[];
  not_derived_here: string[];
  indeterminate: boolean;
};

/* ------------------------------------------------------------- governed CSCA trust store (WF01 screen O2, CR-107) */

export type TrustPackageState = "STAGED" | "VALIDATED" | "REJECTED" | "APPROVED" | "ACTIVE" | "SUPERSEDED" | "ROLLED_BACK" | "RETIRED";
export type TrustPackage = {
  package_id: string;
  kind: "CSCA";
  seq: number;
  version: number;
  state: TrustPackageState;
  source_uri: string;
  source_note: string | null;
  package_sha256: string;
  anchor_sha256: string[];
  list_status: "VERIFIED" | "NOT_VERIFIED" | "INDETERMINATE" | null;
  list_reasons: string[];
  list_signer: string | null;
  certificate_count: number;
  manifest_digest: string | null;
  staged_by: string;
  staged_at: string;
  validated_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  activated_by: string | null;
  activated_at: string | null;
  ended_at: string | null;
  ended_by: string | null;
  ended_reason: string | null;
};
export type TrustStageIn = { master_list_base64: string; anchors_base64: string[]; confirmed_anchor_sha256: string[]; source_uri: string; source_note?: string };
export type TrustActive = { source: "TRUST_STORE" | "CONFIG_FOLDER"; package_id: string | null; package_seq: number | null; manifest_digest: string | null; governed: boolean; certificates: number };

/* ------------------------------------------------------------- reviewer rulings on company cases (WF-13B, ZID-ED-03, ZID-ED-04) */

export type ScreeningAlert = { list_id: string; score?: number; reasons?: string[]; list_version?: string; [key: string]: unknown };
export type ScreeningCaseDetail = {
  case_id: string;
  status: string;
  reasons: string[];
  versions: { version: number; status: string; decided_by: string | null }[];
  alerts: ScreeningAlert[];
  appeals: unknown[];
  register: unknown;
};
export type ScreeningReplay = { replayable: boolean; version: number; reason?: string; matches?: boolean; [key: string]: unknown };
export type ScreeningRulingIn = { outcome: "VERIFIED" | "REJECTED" | "REVIEW_REQUIRED"; rationale: string; confirm_mandate?: boolean };
export type KybRulingIn = { outcome: "VERIFIED" | "REJECTED" | "NEEDS_REVIEW"; rationale: string };
export type CaseRuling = { case_id: string; status: string; version: number; position?: string };

/* ------------------------------------------------------------- supplier spend budget and usage (WF01-014) */

export type VerificationBudget = {
  budget: { monthly_limit: string; currency: string; version: number } | null;
  month_start: string;
  spent?: string;
  remaining?: string;
  exceeded?: boolean;
  other_currencies?: string[];
};
export type VerificationUsage = {
  since: string;
  until: string;
  sessions: { adapter_id: string; status: string; outcome: string | null; sessions: number }[];
  total_sessions: number;
  review_cases: Record<string, number>;
  repeat_attempts: number;
  failover_sessions: number;
  failover_verified: number;
};

/* ------------------------------------------------------------- face engine readiness (WF01-011/012): what keeps an engine from being approved */

export type EngineRates = { false_accept: number | null; false_reject: number | null; attack_accept: number | null; bona_fide_reject: number | null };
export type EngineComponent = {
  id: string;
  role: string | null;
  code_licence: string | null;
  commercial_use: string;
  model_licence_confirmed: boolean;
  licence_verified_on: string | null;
  status: string | null;
  in_engine: boolean;
};
export type EngineReadiness = {
  mode: "DISABLED" | "LAB_ONLY" | "APPROVED_FOR_SCOPE";
  enabled: boolean;
  approvable: boolean;
  problems_for_approval: string[];
  countries: string[];
  engine: {
    id: string | null;
    version: string | null;
    evaluation_id: string | null;
    evaluated_on: string | null;
    pad_standard: string | null;
    licence_checked: boolean;
    dpia_ref: string | null;
    components: string[];
    measured: EngineRates;
  };
  thresholds: { face_match_min: number | null; liveness_min: number | null };
  ceilings: EngineRates & { [key: string]: number | null };
  register: { version: string | null; status: string | null; components: EngineComponent[] };
};

/* ------------------------------------------------------------- company verification grade, mandates, appeals, export (WF02, CR-109) */

export type KybGrade = "K0" | "K1" | "K2" | "K3" | "K4";
export type KybAssertion = {
  version?: number;
  grade: KybGrade;
  label: string;
  claims: string[];
  reasons: string[];
  assessed_at?: string;
  recheck_due: string | null;
  policy_hash?: string | null;
  /** The grade worked out again now from the stored facts: time alone can lower it. */
  now?: { grade: KybGrade; label: string; reasons: string[]; lowered_since_assessed: boolean };
  permitted_actions: string[];
};
export type KybHistoryItem = { version: number; grade: KybGrade; label: string; claims: string[]; reasons: string[]; trigger: string; assessed_at: string };
export type KybMandate = {
  mandate_id: string;
  person: string;
  role: string;
  source: string;
  powers: string[];
  second_signatory_required: boolean;
  second_signatory_present: boolean;
  valid_from: string;
  valid_to: string | null;
  revoked: boolean;
  recorded_at: string;
};
export type KybMandateIn = {
  person: string;
  role: string;
  source: string;
  powers: string[];
  evidence_ref: string;
  valid_from: string;
  valid_to?: string | null;
  second_signatory_required?: boolean;
};
export type KybAppeal = { appeal_id: string; decision_version: number; state: "OPEN" | "UPHELD" | "ALLOWED" | "WITHDRAWN"; message: string; opened_at: string; decided_at: string | null };
export type KybEntitlement = { action: string; allowed: boolean; grade: KybGrade; label: string; reasons: string[] };
export type KybExport = { summary: Record<string, unknown>; jws: string };

/* ------------------------------------------------------------- company-verification market policy packs, operator plane (WF02 K09, CR-109) */

export type KybPolicyVersion = { policy_hash: string; market_key: string; version: string; author: string; created_at: string; approved_roles: string[]; active: boolean };
export type KybPolicyDetail = {
  policy_hash: string;
  market_key: string;
  version: string;
  author: string;
  pack: Record<string, unknown>;
  approved: [string, string][];
  high_impact_changes: string[];
  publishable: boolean;
  problems: string[];
  in_force: boolean;
};
export type KybPolicyProposed = { policy_hash: string; market_key: string; version: string; complete: boolean; problems: string[] };

/** An identity proofing case (WF03, CR-110): codes and states only, no document data, image or score. */
export type IdvDecision = "APPROVED" | "REVIEW_REQUIRED" | "REJECTED" | "INDETERMINATE" | "ABANDONED";
export type IdvCase = {
  case_id: string;
  market: string;
  route: string;
  state: "OPEN" | "CLOSED";
  decision: IdvDecision;
  reasons: string[];
  assertions: string[];
  signals: { optical: string; nfc_read: string; passive_auth: string; chip_auth: string; liveness: string; face_match: string };
  policy_version: string;
  policy_hash: string;
  notice_version: string | null;
  reviewers: number;
  attempts: Record<string, number>;
  version: number;
  created_at: string;
  updated_at: string;
};
export type IdvCaseDetail = IdvCase & { history: { version: number; decision: IdvDecision; reasons: string[]; trigger: string; decided_at: string }[] };

/* ---------------------------------------------------------------- screening sources (WF06 S10, CR-113) */

export type ScreeningSourceId = "OFAC_SDN" | "OFAC_NON_SDN" | "UK_SANCTIONS" | "EU_CONSOLIDATED" | "UN_SC";
export type ScreeningFreshness = "FRESH" | "STALE" | "NEVER_RELEASED";
export type ScreeningSource = {
  source_id: ScreeningSourceId;
  address_on_record: boolean;
  freshness: ScreeningFreshness;
  last_validated_at: Iso | null;
  snapshot: string | null;
  awaiting_approval: number;
  quarantined: number;
  max_age_hours: number;
};
export type ScreeningReleaseState = "VALIDATED" | "QUARANTINED" | "ACTIVE" | "SUPERSEDED";
export type ScreeningRelease = {
  release_id: string;
  source_id: ScreeningSourceId;
  state: ScreeningReleaseState;
  staged_by: string;
  receipt: { url: string; sha256: string; fetched_at: Iso; published_at: Iso | null; item_count: number; schema_valid: boolean };
  blocked: string[];
  flags: string[];
  approvals: { approver: string; at: Iso; acknowledged: string[]; kind: "PUBLISH" | "ROLLBACK" }[];
  activated_at: Iso | null;
  superseded_at: Iso | null;
  publishable: string[];
};
export type ScreeningComparison = { release_id: string; against_active: boolean; added: number; removed: number; changed: number; removed_refs: string[]; changed_refs: string[] };

/* ---------------------------------------------------------------- screening cases (WF06 S04, S05, S09, CR-113) */

export type ScreeningCaseState = "NO_CANDIDATE" | "REVIEW_REQUIRED" | "SOURCE_UNAVAILABLE" | "CLOSED_CLEAR_FOR_SCOPE" | "CLOSED_RESTRICTED" | "CLOSED_UNRESOLVED";
export type ScreeningDisposition = "CLEARED_FOR_PURPOSE" | "EDD_REQUIRED" | "HOLD_PENDING_REVIEW" | "RESTRICTED" | "PROHIBITED" | "UNRESOLVED";
export type ScreeningAction = "RELEASE" | "EDD" | "HOLD" | "REJECT" | "FREEZE" | "BLOCK" | "REPORT";
export type ScreeningQueueItem = {
  request_id: string;
  tenant_id: string;
  market: string;
  purpose: string;
  subject_type: string;
  state: ScreeningCaseState;
  created_at: Iso;
  revision: number;
  sources_with_candidates: number;
};
export type ScreeningCandidate = {
  candidate_id: string;
  list_id: string;
  list_version: string;
  source_ref: string;
  matched_name: string;
  score: number;
  strength: "STRONG" | "POSSIBLE";
  reasons: string[];
  programs: string[];
};
export type ScreeningDimensionView = { coverage: string; result: string | null };
export type ScreeningModel = {
  case_id: string;
  coverage_status: string;
  match_status: string;
  review: string;
  sanctions: ScreeningDimensionView;
  pep_rca: ScreeningDimensionView;
  adverse_media: ScreeningDimensionView;
  ownership_control: ScreeningDimensionView;
  source_results: { source_id: string; snapshot: string | null; match_status: string | null; screened_at: Iso | null }[];
  decision: { status: ScreeningDisposition; rule_version: string; reason_codes: string[] };
};
export type ScreeningProposal = {
  proposal_id: string;
  maker: string;
  maker_role: string;
  disposition: ScreeningDisposition;
  action: ScreeningAction;
  rationale: string;
  reason_codes: string[];
  evidence_refs: string[];
  expected_revision: number;
  created_at: Iso;
};
export type ScreeningReviewCaseDetail = {
  request_id: string;
  tenant_id: string;
  state: ScreeningCaseState;
  revision: number;
  market: string;
  purpose: string;
  subject: { kind: string; name: string; birth: string | null; countries: string[]; identifiers: string[]; expired_identifiers: string[]; identity_source: string; identity_assured_at: Iso } | null;
  candidates: ScreeningCandidate[];
  model: ScreeningModel;
  decisions: { revision: number; disposition: ScreeningDisposition; action: ScreeningAction; maker: string; checker: string | null; rationale: string; reason_codes: string[]; kind: string; decided_at: Iso; case_hash: string }[];
  chain_intact: boolean;
  proposals: ScreeningProposal[];
};
export type ScreeningProposeIn = {
  disposition: ScreeningDisposition;
  action: ScreeningAction;
  expected_revision: number;
  rationale: string;
  reason_codes: string[];
  evidence_refs: string[];
  legal_citation?: string | null;
  review_by?: string | null;
};
export type ScreeningProposeOut = { proposal_id: string | null; state: "DECIDED" | "AWAITING_CHECKER"; request_id: string; disposition: ScreeningDisposition; revision?: number };

/* ---------------------------------------------------------------- screening for a tenant (WF06 S01 to S03, CR-113) */

export type ScreeningClass = "SANCTIONS" | "PEP_RCA" | "ADVERSE_MEDIA" | "OWNERSHIP_CONTROL";
export type ScreeningSubjectType = "INDIVIDUAL" | "BUSINESS" | "UBO" | "REPRESENTATIVE";
export type ScreeningReason = { code: string; text: string };
export type ScreeningEligibility = {
  eligible: boolean;
  blocked: ScreeningReason[];
  pending_identity: boolean;
  policy_version: string | null;
  region: string | null;
  classes: { class: ScreeningClass; available: boolean; tier: "OFFICIAL" | "LICENSED" | null; reasons: ScreeningReason[] }[];
  note: string;
};
export type ScreeningCaseIn = {
  market: string;
  purpose: string;
  subject_type: ScreeningSubjectType;
  subject_ref: string;
  name: string;
  birth?: string | null;
  countries?: string[];
  classes?: ScreeningClass[];
};
export type ScreeningCaseView = {
  request_id: string;
  market: string;
  purpose: string;
  subject_type: ScreeningSubjectType;
  created_at: Iso;
  checked_at: Iso | null;
  coverage: string;
  official_sanctions: string;
  pep_rca: string;
  adverse_media: string;
  finding: string;
  note: string;
};

/* ---------------------------------------------------------------- screening providers, exports, watches (WF06 S11, S12, S13, CR-113) */

export type ScreeningProvider = {
  provider_id: string;
  adapter: string;
  adapter_registered: boolean;
  state: "DISABLED" | "SANDBOX" | "APPROVED";
  classes: string[];
  markets: string[];
  regions: string[];
  contract_version: string | null;
  price_version: string | null;
  finance_authorised_by: string | null;
  unit_cost: string;
  monthly_budget: string | null;
  monthly_quota: number | null;
  blocked_by: string[];
  circuit: { open: boolean; recent_failures: number; opened_at: string | null };
};
export type ScreeningExportRequested = { export_id: string; state: "PENDING" | "READY"; manifest_hash: string | null };
export type ScreeningExportFetched = {
  export_id: string;
  manifest_hash: string;
  verified: boolean;
  problems: string[];
  bundle: { case_id: string; purpose: string; requester: string; approver: string | null; watermark: string; expires_at: string; included: { kind: string; ref: string }[]; excluded: { kind: string; ref: string; why: string }[] };
};
export type ScreeningWatchStatus = {
  watch_id: string;
  active: boolean;
  approvals: number;
  cadence_seconds: number;
  runs: number;
  continuous: boolean;
  sources: Record<string, { last_attempt_at: string | null; last_success_at: string | null; snapshot: string | null }>;
};

/* ---------------------------------------------------------------- screening release gates (WF06 section 22, ENG-20, CR-113) */

export type ScreeningGateModule = "SANCTIONS" | "PEP_RCA" | "ADVERSE_MEDIA" | "MONITORING";
export type ScreeningGateStatus = {
  market: string;
  module: ScreeningGateModule;
  go: boolean;
  missing: Record<string, string[]>;
  blockers: string[];
  open_defects: { record_id: string; ref: string; opened_at: string }[];
  holds: { record_id: string; reason: string; placed_at: string }[];
};
export type ScreeningGateAct = ScreeningGateStatus & { record_id?: string };

/* ---------------------------------------------------------------- method certification board (WF03 P10, CR-119) */

export type MethodLevel = "NOT_BUILT" | "BUILT_UNPROVEN" | "EVIDENCE_INCOMPLETE" | "APPROVED";
export type MethodCertificationBoard = {
  methods: { method: string; level: MethodLevel; summary: string; needs: string[] }[];
  counts: Record<MethodLevel, number>;
  note: string;
};

/* ---------------------------------------------------------------- screening information requests and the owners' roll-up (WF06, CR-122) */

export type ScreeningInformationState = "REQUESTED" | "RECEIVED" | "ACCEPTED" | "INSUFFICIENT" | "EXPIRED";
export type ScreeningInformationRequest = { edd_id: string; state: ScreeningInformationState; still_needed: string[]; due_at: string; message: string };
export type ScreeningInformation = { request_id: string; open: boolean; request: ScreeningInformationRequest | null };
export type ScreeningReviewInformation = ScreeningInformationRequest & { requested_by: string; reviewed_by: string | null; review_code: string | null; reminders: number };
export type ScreeningOwnerIn = { person_ref: string; status: "NEEDS_EVIDENCE" | "NEEDS_CONFIRMATION" | "CONFIRMED" };
export type ScreeningOwnershipRollup = {
  status: "COMPLETE_CLEAR" | "IN_REVIEW" | "INCOMPLETE";
  owners: { person_ref: string; status: string; reason: string }[];
  reasons: string[];
  needs_case: string[];
};

/* ---------------------------------------------------------------- the reviewer's side of a reviewed identity route, and the age check (CR-124) */

export type IdvPhoto = { has_photo: boolean; expired?: boolean; content_type?: string; byte_length?: number; checks?: Record<string, string>; captured_at?: string; expires_at?: string };
export type IdvClaim = { grade: "CHIP_VERIFIED" | "REVIEWED_NO_CHIP"; market: string; performed_at: string; valid_until: string; status: "VALID" | "REVOKED" };
export type IdvReview = IdvCase & { photo: IdvPhoto; reject_codes: string[]; recapture_codes: string[]; document_date_known: boolean; claim: IdvClaim | null };
export type IdvQueueItem = { case_id: string; market: string; route: string; created_at: string; waiting_hours: number; due_at: string | null; overdue: boolean; signatures: number; has_photo: boolean };
export type IdvQueue = { items: IdvQueueItem[]; overdue: number };
export type IdvMediaGrant = { grant_id: string; expires_at: string; reads_left: number };
export type IdvMedia = { content_type: string; image_base64: string; checks: Record<string, string>; reads_left: number };
export type AgeCheckIn = { threshold: number; birth_date?: string };
export type AgeCheckResult = { threshold: number; result: "AT_LEAST" | "UNDER" | "UNDETERMINED"; method: "VERIFIED_BIRTH_DATE" | "ESTIMATION" | "SELF_DECLARED"; valid_until: string | null; reason_codes: string[]; fallback_offered: boolean; limitations: string[] };
