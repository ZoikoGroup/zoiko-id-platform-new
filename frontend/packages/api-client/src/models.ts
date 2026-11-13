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
export type SignalSource = "VELOCITY" | "ADVERSE_MEDIA";
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
  detail: VelocityDetail & AdverseMediaDetail;
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
