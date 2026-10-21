/** Every cell endpoint the web app uses, one typed function each.
 *
 * `createCellApi` runs over the BFF (`/api/bff`, bearer token attached server-side).
 * `createFlowApi` runs same-origin against the passthrough (`/authn/*`, `/oauth2/consent/*`), where the
 * cell authenticates with its own session cookie and checks the Origin header. */
import type { components as AuthN } from "./generated/authentication";
import type { components as Authority } from "./generated/authority-mandate";
import type { components as AuthZen } from "./generated/authzen-evaluation";
import type { components as Cred } from "./generated/credentials";
import type { components as Fed } from "./generated/federation";
import type { components as OAuth } from "./generated/oauth";
import type { components as Scim } from "./generated/scim";
import type { components as Sig } from "./generated/signals";
import type { components as Ver } from "./generated/verification";
import type { components as Ev } from "./generated/evidence";
import type { components as Op } from "./generated/operator-access";
import type { components as Priv } from "./generated/privacy";
import type { components as Res } from "./generated/residency";
import type { components as Wl } from "./generated/workload-identity";
import type * as M from "./models";
import { seg, type Transport } from "./runtime/transport";

type S<C extends { schemas: Record<string, unknown> }, K extends keyof C["schemas"]> = C["schemas"][K];

/* Request bodies, named for the UI. */
export type GrantCreate = S<Authority, "GrantCreate">;
export type ApprovalRequestCreate = S<Authority, "RequestCreate">;
export type ApprovalDecisionCreate = S<Authority, "DecisionCreate">;
export type MandateCreate = S<Authority, "MandateCreate">;
export type ReserveBody = S<Authority, "ReserveBody">;
export type CommitBody = S<Authority, "CommitBody">;
export type LockboxIn = S<AuthN, "LockboxIn">;
export type IntegrationIn = S<Fed, "IntegrationIn">;
export type RotateIn = S<Fed, "RotateIn">;
export type IdpInitiatedIn = S<Fed, "IdpInitiatedIn">;
export type AnchorIn = S<Fed, "AnchorIn">;
export type ConfigurationIn = S<Cred, "ConfigurationIn">;
export type TrustIn = S<Cred, "TrustIn">;
export type TrustStatusIn = S<Cred, "TrustStatusIn">;
export type RegistrationIn = S<Cred, "RegistrationIn">;
export type PresentationIn = S<Cred, "PresentationIn">;
export type StreamIn = S<Sig, "StreamIn">;
export type SubjectIn = S<Sig, "SubjectIn">;
export type SignalIn = S<Sig, "SignalIn">;
export type OverrideIn = S<Sig, "OverrideIn">;
export type SourceIn = S<Scim, "SourceIn">;
export type ProfileIn = S<Ver, "ProfileIn">;
export type AdapterIn = S<Ver, "AdapterIn">;
export type RelianceIn = S<Ver, "RelianceIn">;
export type ReviewIn = S<Ver, "ReviewIn">;
export type VerificationStartIn = S<Ver, "zid_platform__modules__verification__api__verification__StartIn">;
export type EvaluationRequest = S<AuthZen, "EvaluationRequest">;
export type ConsentDecision = S<OAuth, "ConsentDecision">;
export type RegistrationOptionsIn = S<AuthN, "RegistrationOptionsIn">;
/* Phase F */
export type AnnotateIn = S<Ev, "AnnotateBody">;
export type PackageIn = S<Ev, "PackageBody">;
export type HoldIn = S<Ev, "HoldBody">;
export type HoldReleaseIn = S<Ev, "ReleaseBody">;
export type ResolveIn = S<Ev, "ResolveBody">;
export type LockboxDecisionIn = S<Op, "DecisionBody">;
export type OperatorPolicyIn = S<Op, "PolicyBody">;
export type AccessRequestIn = S<Op, "AccessBody">;
export type BreakGlassIn = S<Op, "BreakGlassBody">;
export type OperatorReasonIn = S<Op, "ReasonBody">;
export type BreakGlassReviewIn = S<Op, "ReviewBody">;
export type ConsentIn = S<Priv, "ConsentBody">;
export type RightsIn = S<Priv, "RightsBody">;
export type OnBehalfIn = S<Priv, "OnBehalfBody">;
export type RightsVerifyIn = S<Priv, "VerifyBody">;
export type RightsCloseIn = S<Priv, "CloseBody">;
export type PurposeIn = S<Priv, "PurposeBody">;
export type ActivityIn = S<Priv, "ActivityBody">;
export type RoleAssignmentIn = S<Priv, "RoleBody">;
export type RetentionPolicyIn = S<Priv, "RetentionBody">;
export type ResidencyIn = S<Res, "ResidencyBody">;
export type TransferRuleIn = S<Res, "RuleBody">;
export type TransferCheckIn = S<Res, "TransferBody">;
export type WorkloadBindingIn = S<Wl, "BindingBody">;
export type WorkloadRevokeIn = S<Wl, "RevokeBody">;
export type QuarantineIn = S<Wl, "QuarantineBody">;

export function createCellApi(t: Transport) {
  const get = <T>(path: string, query?: Record<string, string | number | undefined>) => t.request<T>("GET", path, { query });
  const post = <T>(path: string, body?: unknown, extra: { idempotencyKey?: string; query?: Record<string, string | number | undefined> } = {}) =>
    t.request<T>("POST", path, { body: body ?? {}, ...extra });

  return {
    me: {
      userinfo: () => get<M.UserInfo>("/oauth2/userinfo"),
      sessions: () => get<M.Items<M.SessionView>>("/sessions"),
      revokeSession: (id: string) => post<{ session_id: string; status: M.SessionStatus }>(`/sessions/${seg(id)}:revoke`),
      revokeAllSessions: () => post<{ revoked: number }>("/sessions:revoke-all"),
      authenticators: () => get<M.Items<M.AuthenticatorView>>("/authn/authenticators"),
      revokeAuthenticator: (id: string) => post<{ authenticator_id: string; status: M.AuthenticatorStatus }>(`/authn/authenticators/${seg(id)}:revoke`),
      issueRecoveryCodes: () => post<M.RecoveryCodes>("/authn/recovery-codes"),
      cancelRecovery: (id: string) => post<{ recovery_id: string; status: "DENIED" }>(`/authn/recovery/${seg(id)}:cancel`),
      consents: () => get<M.Items<M.ConsentGrant>>("/oauth2/consents"),
      revokeConsent: (clientId: string) => post<{ client_id: string; status: "REVOKED" }>(`/oauth2/consents/${seg(clientId)}:revoke`),
    },

    principals: {
      list: (q: { principal_type?: M.PrincipalType; status?: M.PrincipalStatus; limit?: number } = {}) =>
        get<M.Items<M.PrincipalItem>>("/admin/principals", q),
    },

    sessionsAdmin: {
      revokeAllForPrincipal: (principalId: string) =>
        post<{ principal_id: string; revoked: number }>(`/admin/principals/${seg(principalId)}/sessions:revoke`),
    },

    recoveryAdmin: {
      assist: (principalId: string) => post<M.RecoveryProgress>("/admin/recovery-cases", { principal_id: principalId }),
      deny: (recoveryId: string, reason: string) =>
        post<{ recovery_id: string; status: "DENIED" }>(`/admin/recovery-cases/${seg(recoveryId)}:deny`, { reason }),
      policy: (principalId: string) => get<M.RecoveryPolicy>(`/admin/recovery-policies/${seg(principalId)}`),
      putPolicy: (principalId: string, body: LockboxIn) =>
        t.request<M.RecoveryPolicy>("PUT", `/admin/recovery-policies/${seg(principalId)}`, { body }),
      liftPolicy: (principalId: string, approvalRequestId: string | null) =>
        post<M.RecoveryPolicy>(`/admin/recovery-policies/${seg(principalId)}:lift`, { approval_request_id: approvalRequestId }),
    },

    federation: {
      integrations: () => get<M.Items<M.FederationIntegration>>("/federation/integrations"),
      createIntegration: (body: IntegrationIn) => post<M.FederationIntegration>("/federation/integrations", body),
      rotate: (id: string, body: RotateIn) => post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:rotate`, body),
      setIdpInitiated: (id: string, body: IdpInitiatedIn) =>
        post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:idp-initiated`, body),
      disable: (id: string) => post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:disable`),
      trustAnchors: () => get<M.Items<M.TrustAnchorItem>>("/federation/trust-anchors"),
      createTrustAnchor: (body: AnchorIn) => post<M.TrustAnchor>("/federation/trust-anchors", body),
      disableTrustAnchor: (id: string) => post<M.TrustAnchor>(`/federation/trust-anchors/${seg(id)}:disable`),
    },

    verification: {
      profiles: () => get<M.Items<M.VerificationProfile>>("/verification-profiles"),
      createProfile: (body: ProfileIn) => post<M.VerificationProfile>("/verification-profiles", body),
      activateProfile: (id: string) => post<M.VerificationProfile>(`/verification-profiles/${seg(id)}:activate`),
      adapters: () => get<M.Items<M.VerificationAdapterItem>>("/verification-adapters"),
      createAdapter: (body: AdapterIn) => post<M.VerificationAdapter>("/verification-adapters", body),
      quarantineAdapter: (id: string) => post<M.VerificationAdapter>(`/verification-adapters/${seg(id)}:quarantine`),
      start: (body: VerificationStartIn) => post<M.VerificationSession>("/verifications", body),
      get: (id: string) => get<M.VerificationSession>(`/verifications/${seg(id)}`),
      cancel: (id: string) => post<M.VerificationSession>(`/verifications/${seg(id)}:cancel`),
      reverify: (id: string) => post<M.VerificationSession>(`/verifications/${seg(id)}:reverify`),
      assertions: (principalId: string) => get<M.Items<M.VerificationAssertion>>(`/principals/${seg(principalId)}/verification-assertions`),
      assurance: (principalId: string) => get<M.Assurance>(`/principals/${seg(principalId)}/assurance`),
      revokeAssertion: (id: string) => post<{ assertion_id: string; status: "REVOKED" }>(`/verification-assertions/${seg(id)}:revoke`),
      rely: (body: RelianceIn) => post<M.RelianceDecision>("/verification-reliance-decisions", body),
      decideReview: (caseId: string, body: ReviewIn) => post<M.ReviewOutcome>(`/verification-review-cases/${seg(caseId)}/decisions`, body),
    },

    credentials: {
      list: (q: { status?: M.IssuedCredentialStatus; limit?: number } = {}) => get<M.Items<M.IssuedCredential>>("/credentials", q),
      configurations: () => get<M.Items<M.CredentialConfigurationItem>>("/credential-configurations"),
      issuerTrust: () => get<M.Items<M.IssuerTrustItem>>("/credential-issuer-trust"),
      relyingParties: () => get<M.Items<M.RpRegistrationItem>>("/relying-party-registrations"),
      createConfiguration: (body: ConfigurationIn) => post<M.CredentialConfiguration>("/credential-configurations", body),
      activateConfiguration: (id: string) => post<M.CredentialConfiguration>(`/credential-configurations/${seg(id)}:activate`),
      createIssuerTrust: (body: TrustIn) => post<M.IssuerTrust>("/credential-issuer-trust", body),
      setIssuerTrustStatus: (body: TrustStatusIn) => post<M.IssuerTrust>("/credential-issuer-trust:status", body),
      registerRelyingParty: (body: RegistrationIn) => post<M.RpRegistration>("/relying-party-registrations", body),
      revoke: (id: string) => post<M.CredentialStatusChange>(`/credentials/${seg(id)}:revoke`),
      suspend: (id: string) => post<M.CredentialStatusChange>(`/credentials/${seg(id)}:suspend`),
      reinstate: (id: string) => post<M.CredentialStatusChange>(`/credentials/${seg(id)}:reinstate`),
      requestPresentation: (body: PresentationIn) => post<M.PresentationCreated>("/presentations", body),
      presentation: (id: string) => get<M.Presentation>(`/presentations/${seg(id)}`),
    },

    authority: {
      grants: (q: { status?: M.GrantStatus; grantee_principal_id?: string; limit?: number } = {}) =>
        get<M.Items<M.AuthorityGrant>>("/v1/authority-grants", q),
      approvals: (q: { state?: M.ApprovalState; limit?: number } = {}) => get<M.Items<M.ApprovalListItem>>("/v1/approval-requests", q),
      createGrant: (body: GrantCreate, idempotencyKey: string) => post<M.AuthorityGrant>("/v1/authority-grants", body, { idempotencyKey }),
      grant: (id: string) => get<M.AuthorityGrant>(`/v1/authority-grants/${seg(id)}`),
      chain: (id: string, riskClass?: number) => get<M.GrantChain>(`/v1/authority-grants/${seg(id)}/chain`, { risk_class: riskClass }),
      revokeGrant: (id: string) => post<{ authority_grant_id: string; status: M.GrantStatus }>(`/v1/authority-grants/${seg(id)}:revoke`),
      createApproval: (body: ApprovalRequestCreate) => post<M.ApprovalCreated>("/v1/approval-requests", body),
      approval: (id: string, transactionContext: string) =>
        get<M.ApprovalView>(`/v1/approval-requests/${seg(id)}`, { transaction_context: transactionContext }),
      decideApproval: (id: string, body: ApprovalDecisionCreate) =>
        post<M.ApprovalDecisionResult>(`/v1/approval-requests/${seg(id)}/decisions`, body),
    },

    mandates: {
      list: (q: { status?: M.MandateStatus; executor_principal_id?: string; limit?: number } = {}) =>
        get<M.Items<M.Mandate>>("/v1/mandates", q),
      create: (body: MandateCreate, idempotencyKey: string) => post<M.Mandate>("/v1/mandates", body, { idempotencyKey }),
      balance: (id: string) => get<M.MandateBalance>(`/v1/mandates/${seg(id)}/balance`),
      activate: (id: string, riskClass?: number) =>
        post<{ mandate_id: string; status: M.MandateStatus }>(`/v1/mandates/${seg(id)}:activate`, undefined, { query: { risk_class: riskClass } }),
      pause: (id: string) => post<{ mandate_id: string; status: M.MandateStatus }>(`/v1/mandates/${seg(id)}:pause`),
      revoke: (id: string) => post<{ mandate_id: string; status: M.MandateStatus }>(`/v1/mandates/${seg(id)}:revoke`),
      reserve: (id: string, body: ReserveBody, idempotencyKey: string) =>
        post<M.Reservation>(`/v1/mandates/${seg(id)}/reservations`, body, { idempotencyKey }),
      commit: (reservationId: string, body: CommitBody) => post<M.Reservation>(`/v1/mandate-reservations/${seg(reservationId)}:commit`, body),
      release: (reservationId: string) => post<M.Reservation>(`/v1/mandate-reservations/${seg(reservationId)}:release`),
    },

    decisions: {
      evaluate: (body: EvaluationRequest) => post<M.EvaluationResult>("/access/v1/evaluation", body),
    },

    signals: {
      securityState: (type: M.SubjectType, id: string) => get<M.SecurityState>(`/security-state/${seg(type)}/${seg(id)}`),
      override: (type: M.SubjectType, id: string, body: OverrideIn) =>
        post<{ subject_id: string; state: M.SecurityStateValue; revision: number }>(`/security-state/${seg(type)}/${seg(id)}:reevaluate`, body),
      publish: (body: SignalIn) => post<M.SignalAccepted>("/signals", body),
      streams: () => get<M.Items<M.SsfStream>>("/ssf/streams"),
      createStream: (body: StreamIn) => post<M.SsfStream>("/ssf/streams", body),
      stream: (id: string) => get<M.SsfStream>(`/ssf/streams/${seg(id)}`),
      enableStream: (id: string) => post<{ stream_id: string; status: string }>(`/ssf/streams/${seg(id)}:enable`),
      disableStream: (id: string) => post<{ stream_id: string; status: string }>(`/ssf/streams/${seg(id)}:disable`),
      propagation: (id: string) => get<M.StreamPropagation>(`/ssf/streams/${seg(id)}/propagation`),
      mapSubject: (id: string, body: SubjectIn) => post<Record<string, unknown>>(`/ssf/streams/${seg(id)}/subjects`, body),
    },

    provisioning: {
      sources: () => get<M.Items<M.ProvisioningSourceItem>>("/provisioning/sources"),
      createSource: (body: SourceIn) => post<M.ProvisioningSource>("/provisioning/sources", body),
      suspendSource: (id: string) => post<M.ProvisioningSource>(`/provisioning/sources/${seg(id)}:suspend`),
    },

    evidence: {
      keys: () => get<{ keys: Record<string, unknown>[] }>("/v1/evidence/keys"),
      receipts: (q: { action?: string; outcome?: string; evidence_class?: string; before?: string; limit?: number } = {}) =>
        get<M.Items<M.ReceiptSummary>>("/v1/evidence/receipts", q),
      receipt: (id: string) => get<M.ReceiptDetail>(`/v1/evidence/receipts/${seg(id)}`),
      verify: (id: string) => post<M.ReceiptVerification>(`/v1/evidence/receipts/${seg(id)}:verify`),
      proof: (id: string) => get<Record<string, unknown>>(`/v1/evidence/receipts/${seg(id)}/proof`),
      replay: (id: string) => post<M.ReplayOutcome>(`/v1/evidence/receipts/${seg(id)}:replay`),
      annotate: (id: string, body: AnnotateIn) =>
        post<{ annotation_id: string; receipt_id: string; kind: string }>(`/v1/evidence/receipts/${seg(id)}:annotate`, body),
      exportPackage: (body: PackageIn) => post<M.EvidencePackage>("/v1/evidence/packages", body),
      auditEvents: (q: { event_type?: string; before?: string; limit?: number } = {}) => get<M.Items<M.AuditEvent>>("/v1/evidence/audit-events", q),
      verifyPartition: () => post<M.PartitionReport>("/v1/evidence/partition:verify"),
      holds: (status?: string) => get<M.Items<M.LegalHold>>("/v1/evidence/legal-holds", { status }),
      createHold: (body: HoldIn) => post<M.LegalHold>("/v1/evidence/legal-holds", body),
      releaseHold: (id: string, body: HoldReleaseIn) => post<M.LegalHold>(`/v1/evidence/legal-holds/${seg(id)}:release`, body),
      resolveHandle: (body: ResolveIn) => post<{ handle: string; principal_id: string }>("/v1/evidence/handles:resolve", body),
    },

    privacy: {
      packs: () => get<M.Items<M.JurisdictionPackView>>("/v1/privacy/jurisdiction-packs"),
      purposes: () => get<M.Items<M.PurposeView>>("/v1/privacy/purposes"),
      myConsents: () => get<M.Items<M.ConsentRecord>>("/v1/privacy/consents"),
      giveConsent: (body: ConsentIn) => post<{ consent_id: string; status: string }>("/v1/privacy/consents", body),
      withdrawConsent: (id: string, reason?: string) =>
        post<{ consent_id: string; status: string }>(`/v1/privacy/consents/${seg(id)}:withdraw`, reason ? { reason } : {}),
      myPreferences: () => get<M.Items<M.PreferenceRecord>>("/v1/privacy/preferences"),
      setPreference: (type: string, value: boolean) =>
        t.request<{ preference_type: string; value: boolean; revision: number }>("PUT", `/v1/privacy/preferences/${seg(type)}`, { body: { value } }),
      myRequests: () => get<M.Items<M.RightsRequest>>("/v1/privacy/rights-requests/mine"),
      openRequest: (body: RightsIn) => post<M.RightsRequest>("/v1/privacy/rights-requests", body),
      request: (id: string) => get<M.RightsRequest>(`/v1/privacy/rights-requests/${seg(id)}`),
      downloadPackage: (id: string) => get<Record<string, unknown>>(`/v1/privacy/rights-requests/${seg(id)}/package`),
      admin: {
        requests: (status?: string) => get<M.Items<M.RightsRequest>>("/v1/privacy/admin/rights-requests", { status }),
        openOnBehalf: (body: OnBehalfIn) => post<M.RightsRequest>("/v1/privacy/admin/rights-requests", body),
        verify: (id: string, body: RightsVerifyIn) => post<M.RightsRequest>(`/v1/privacy/admin/rights-requests/${seg(id)}:verify`, body),
        fulfill: (id: string) => post<M.RightsRequest>(`/v1/privacy/admin/rights-requests/${seg(id)}:fulfill`),
        close: (id: string, body: RightsCloseIn) => post<M.RightsRequest>(`/v1/privacy/admin/rights-requests/${seg(id)}:close`, body),
        acknowledge: (id: string, item: string, ackRef: string) =>
          post<M.RightsRequest>(`/v1/privacy/admin/rights-requests/${seg(id)}/items/${seg(item)}:acknowledge`, { ack_ref: ackRef }),
        dataMap: (subject: string) => get<M.Items<M.PrivacyRow>>(`/v1/privacy/admin/data-map/${seg(subject)}`),
        purposes: () => get<M.Items<M.PrivacyRow>>("/v1/privacy/admin/purposes"),
        createPurpose: (body: PurposeIn) => post<{ purpose_id: string; status: string }>("/v1/privacy/admin/purposes", body),
        activatePurpose: (id: string) => post<{ purpose_id: string; status: string }>(`/v1/privacy/admin/purposes/${seg(id)}:activate`),
        activities: () => get<M.Items<M.PrivacyRow>>("/v1/privacy/admin/processing-activities"),
        createActivity: (body: ActivityIn) =>
          post<{ processing_activity_id: string; status: string }>("/v1/privacy/admin/processing-activities", body),
        activateActivity: (id: string) =>
          post<{ processing_activity_id: string; status: string }>(`/v1/privacy/admin/processing-activities/${seg(id)}:activate`),
        assignRole: (id: string, body: RoleAssignmentIn) =>
          post<M.PrivacyRow>(`/v1/privacy/admin/processing-activities/${seg(id)}/role-assignments`, body),
        roleMap: () => get<M.Items<M.PrivacyRow>>("/v1/privacy/admin/role-map"),
        releaseCheck: () => get<M.ReleaseCheck>("/v1/privacy/admin/release-check"),
        retention: () => get<M.Items<M.PrivacyRow>>("/v1/privacy/admin/retention-policies"),
        createRetention: (body: RetentionPolicyIn) => post<M.PrivacyRow>("/v1/privacy/admin/retention-policies", body),
        liftRestriction: (id: string) => post<{ restriction_id: string; active: boolean }>(`/v1/privacy/admin/restrictions/${seg(id)}:lift`),
      },
    },

    lockbox: {
      requests: (state?: string) => get<M.Items<M.LockboxRequest>>("/v1/lockbox-requests", { state }),
      approve: (id: string, body: LockboxDecisionIn) => post<M.LockboxRequest>(`/v1/lockbox-requests/${seg(id)}:approve`, body),
      deny: (id: string, body: LockboxDecisionIn) => post<M.LockboxRequest>(`/v1/lockbox-requests/${seg(id)}:deny`, body),
      policy: () => get<M.OperatorAccessPolicy>("/v1/operator-access-policy"),
      setPolicy: (body: OperatorPolicyIn) => t.request<M.OperatorAccessPolicy>("PUT", "/v1/operator-access-policy", { body }),
    },

    residency: {
      cells: () => get<M.Items<M.CellView>>("/v1/cells"),
      current: () => get<{ residency: M.TenantResidency | null }>("/v1/tenant/residency"),
      set: (body: ResidencyIn) => t.request<{ residency: M.TenantResidency }>("PUT", "/v1/tenant/residency", { body }),
      matrix: () => get<{ residency_profile_id: string; items: M.ResidencyMatrixRow[] }>("/v1/tenant/residency-matrix"),
      rules: () => get<M.Items<M.TransferRule>>("/v1/tenant/transfer-rules"),
      addRule: (body: TransferRuleIn) => post<M.TransferRule>("/v1/tenant/transfer-rules", body),
      suspendRule: (id: string) => post<M.TransferRule>(`/v1/tenant/transfer-rules/${seg(id)}:suspend`),
      checkTransfer: (body: TransferCheckIn) => post<M.TransferDecision>("/v1/tenant/cross-cell-transfers:authorize", body),
    },

    workloads: {
      trustDomain: () => get<{ trust_domain: string | null; federated: string[] }>("/v1/machine/trust-domain"),
      bindings: (status?: string) => get<M.Items<M.WorkloadBindingView>>("/v1/machine/workload-bindings", { status }),
      register: (body: WorkloadBindingIn) =>
        post<{ binding_id: string; spiffe_id: string; status: string }>("/v1/machine/workload-bindings", body),
      revoke: (body: WorkloadRevokeIn) => post<{ spiffe_id: string; revoked: number }>("/v1/machine/workload-bindings:revoke", body),
      quarantine: (env: string, body: QuarantineIn) =>
        post<{ environment_id: string; quarantined: boolean; security_revision: number }>(`/v1/machine/environments/${seg(env)}:quarantine`, body),
      release: (env: string) => post<{ environment_id: string; quarantined: boolean }>(`/v1/machine/environments/${seg(env)}:release`),
    },
  };
}

/** The Operator Console's API (separate origin and OIDC client, DEC-F4 §5 / DEC-H1). */
export function createOperatorApi(t: Transport) {
  const get = <T>(path: string, query?: Record<string, string | number | undefined>) => t.request<T>("GET", path, { query });
  const post = <T>(path: string, body?: unknown) => t.request<T>("POST", path, { body: body ?? {} });
  return {
    capabilities: () => get<M.OperatorCapabilities>("/operator/capabilities"),
    requests: (view: "mine" | "pending" | "all" = "mine", state?: string) =>
      get<M.Items<M.JitGrant>>("/operator/access-requests", { view, state }),
    request: (id: string) => get<M.JitGrant>(`/operator/access-requests/${seg(id)}`),
    create: (body: AccessRequestIn) => post<M.JitGrant>("/operator/access-requests", body),
    approve: (id: string) => post<M.JitGrant>(`/operator/access-requests/${seg(id)}:approve`),
    deny: (id: string, body: OperatorReasonIn) => post<M.JitGrant>(`/operator/access-requests/${seg(id)}:deny`, body),
    cancel: (id: string) => post<M.JitGrant>(`/operator/access-requests/${seg(id)}:cancel`),
    activate: (id: string) => post<M.JitGrant>(`/operator/access-requests/${seg(id)}:activate`),
    revoke: (id: string, body: OperatorReasonIn) => post<M.JitGrant>(`/operator/access-requests/${seg(id)}:revoke`, body),
    breakGlass: (body: BreakGlassIn) => post<M.JitGrant & { review_id: string }>("/operator/break-glass", body),
    reviews: (state?: string) => get<M.Items<M.BreakGlassReview>>("/operator/reviews", { state }),
    completeReview: (id: string, body: BreakGlassReviewIn) => post<M.BreakGlassReview>(`/operator/reviews/${seg(id)}:complete`, body),
  };
}

export type OperatorApi = ReturnType<typeof createOperatorApi>;

/** Browser-facing flows (cell session cookie + Origin), same-origin through the passthrough. */
export function createFlowApi(t: Transport, clientId: string) {
  const post = <T>(path: string, body: unknown) => t.request<T>("POST", path, { body });
  return {
    passkeyOptions: () => post<PublicKeyCredentialRequestOptionsJSONLike>("/authn/webauthn/authentication/options", { client_id: clientId }),
    passkeyVerify: (credential: unknown) =>
      post<M.SignInResult>("/authn/webauthn/authentication/verify", { client_id: clientId, credential }),
    stepUpOptions: () => post<PublicKeyCredentialRequestOptionsJSONLike>("/authn/step-up", { client_id: clientId }),
    registrationOptions: (body: Omit<RegistrationOptionsIn, "client_id">) =>
      post<PublicKeyCredentialCreationOptionsJSONLike>("/authn/webauthn/registration/options", { client_id: clientId, ...body }),
    registrationVerify: (credential: unknown, label?: string) =>
      post<M.RegisteredAuthenticator>("/authn/webauthn/registration/verify", { client_id: clientId, credential, label: label ?? null }),
    startRecovery: (recoveryCode: string) => post<M.RecoveryProgress>("/authn/recovery", { client_id: clientId, recovery_code: recoveryCode }),
    continueRecovery: (id: string, recoveryHandle: string, approvalRequestId?: string) =>
      post<M.RecoveryProgress>(`/authn/recovery/${seg(id)}:continue`, {
        client_id: clientId,
        recovery_handle: recoveryHandle,
        approval_request_id: approvalRequestId ?? null,
      }),
    consentRequest: (ref: string) => t.request<M.ConsentRequest>("GET", `/oauth2/consent/${seg(ref)}`),
    decideConsent: (ref: string, decision: ConsentDecision["decision"]) => post<{ redirect_to: string }>(`/oauth2/consent/${seg(ref)}`, { decision }),
    linkFederation: (connectionId: string) =>
      post<{ authorization_url: string }>(`/federation/connections/${seg(connectionId)}/link`, { client_id: clientId }),
  };
}

/** WebAuthn options as JSON (base64url fields), as returned by the cell. */
export type PublicKeyCredentialRequestOptionsJSONLike = { challenge: string; [k: string]: unknown };
export type PublicKeyCredentialCreationOptionsJSONLike = { challenge: string; user: { id: string; name: string; displayName: string }; [k: string]: unknown };

export type CellApi = ReturnType<typeof createCellApi>;
export type FlowApi = ReturnType<typeof createFlowApi>;
