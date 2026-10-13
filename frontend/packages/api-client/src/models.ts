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
  actions: string[];
  resources: string[];
  value_limit: Money | null;
  valid_from: Iso;
  valid_to: Iso;
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
  mandate_id: string;
  reservation_id: string;
  status: ReservationStatus;
  expires_at: Iso | null;
  amounts: LedgerAmounts;
};

/* ---------------------------------------------------------------- decisions */

export type DecisionOutcome = "ALLOW" | "DENY" | "STEP_UP" | "APPROVAL_REQUIRED" | "STATE_UNAVAILABLE";
export type EvaluationResult = {
  decision: boolean;
  context: {
    decision_id: string;
    outcome: DecisionOutcome;
    reason_codes: string[];
    obligations: unknown[];
    risk_class: string;
    policy_release_id: string | null;
    relation_revision: number | null;
    security_revision: number | null;
    valid_until: Iso | null;
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
  acked?: number;
  pending?: number;
  p50?: number | null;
  p99?: number | null;
  max_ms?: number | null;
  oldest_pending_ms?: number | null;
  [k: string]: unknown;
};

/* ---------------------------------------------------------------- provisioning */

export type ProvisioningSource = { source_id: string; client_id: string; status: string };

/* ---------------------------------------------------------------- health */

export type Health = { success: boolean; data?: { status?: string; checks?: Record<string, unknown> } & Record<string, unknown> };
