/** Response models for the cell API.
 *
 * The published OpenAPI contracts type every 2xx body as a free-form object, so these shapes are
 * transcribed from the route handlers in backend/app/src/zid_platform/modules/*\/api/*.py. Keep
 * them in step with those handlers; fields the UI does not rely on are left out. */

/* ---------------------------------------------------------------- shared */

export type Items<T> = { items: T[] };
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
  /** Returned for SAML connections after create / IdP-initiated changes. */
  acs_url?: string;
};
export type TrustAnchor = { anchor_id: string; entity_id: string; status: string };

/* ---------------------------------------------------------------- verification */

export type VerificationStatus =
  | "CREATED"
  | "IN_PROGRESS"
  | "PENDING_EXTERNAL"
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
  /** Propagation SLO per risk class (ms). */
  slo_ms_by_risk: Record<string, number>;
  [k: string]: unknown;
};

/* ---------------------------------------------------------------- provisioning */

export type ProvisioningSource = { source_id: string; client_id: string; status: string };

/* ---------------------------------------------------------------- admin lists (CR-010) */

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
  created_at: Iso;
  expires_at: Iso;
};

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
export type TenantRoleName = "OWNER" | "ADMIN" | "MEMBER";
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
/** Where an invitee is: link sent -> passkey created -> signed in -> accepted (ACTIVE). */
export type MemberProgress = "INVITED" | "PASSKEY_CREATED" | "SIGNED_IN" | "ACTIVE" | "PENDING_VERIFICATION" | "REVOKED";
export type TenantMember = TenantRole & {
  passkeys: number;
  passkey_registered_at: Iso | null;
  last_sign_in_at: Iso | null;
  progress: MemberProgress;
};
export type Invitation = TenantRole & { enrollment_ticket: string; enrollment_ticket_expires_at: Iso };
export type TenantView = {
  tenant_id: string;
  display_name: string;
  tenant_slug: string;
  status: TenantStatus;
  deployment_mode: string;
  home_cell_id: string;
  configuration_revision: number;
  security_revision: number;
  my_roles: TenantRole[];
};
