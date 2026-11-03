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
export type TrustKeysIn = S<Cred, "TrustKeysIn">;
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
export type HoldReleaseIn = S<Ev, "zid_platform__modules__evidence__api__evidence__ReleaseBody">;
export type ResolveIn = S<Ev, "zid_platform__modules__evidence__api__evidence__ResolveBody">;
export type LockboxDecisionIn = S<Op, "DecisionBody">;
export type OperatorPolicyIn = S<Op, "PolicyBody">;
export type AccessRequestIn = S<Op, "AccessBody">;
export type BreakGlassIn = S<Op, "BreakGlassBody">;
export type OperatorReasonIn = S<Op, "zid_platform__modules__operator_access__api__operator__ReasonBody">;
export type BreakGlassReviewIn = S<Op, "zid_platform__modules__operator_access__api__operator__ReviewBody">;
export type ConsentIn = S<Priv, "ConsentBody">;
export type RightsIn = S<Priv, "RightsBody">;
export type OnBehalfIn = S<Priv, "OnBehalfBody">;
export type RightsVerifyIn = S<Priv, "VerifyBody">;
export type RightsCloseIn = S<Priv, "zid_platform__modules__privacy__api__privacy__CloseBody">;
export type PurposeIn = S<Priv, "PurposeBody">;
export type ActivityIn = S<Priv, "ActivityBody">;
export type RoleAssignmentIn = S<Priv, "zid_platform__modules__privacy__api__privacy__RoleBody">;
export type RetentionPolicyIn = S<Priv, "RetentionBody">;
export type ResidencyIn = S<Res, "ResidencyBody">;
export type TransferRuleIn = S<Res, "RuleBody">;
export type TransferCheckIn = S<Res, "TransferBody">;
export type WorkloadBindingIn = S<Wl, "BindingBody">;
export type WorkloadRevokeIn = S<Wl, "RevokeBody">;
export type QuarantineIn = S<Wl, "zid_platform__modules__machine__api__workloads__QuarantineBody">;

export function createCellApi(t: Transport) {
  const get = <T>(path: string, query?: Record<string, string | number | undefined>) => t.request<T>("GET", path, { query });
  const post = <T>(
    path: string,
    body?: unknown,
    extra: { idempotencyKey?: string; query?: Record<string, string | number | undefined>; headers?: Record<string, string> } = {},
  ) => t.request<T>("POST", path, { body: body ?? {}, ...extra });
  /** A proof that a person confirmed this exact command with their passkey (ZID-ENG-DEC-009), sent beside the command. */
  const withProof = (proof?: string) => (proof ? { headers: { "x-step-up-proof": proof } } : {});

  return {
    stepUp: {
      tiers: () => get<M.StepUpTiers>("/v1/step-up/tiers"),
      challenge: (draft: M.StepUpDraft) =>
        post<M.StepUpChallenge>("/v1/step-up/challenges", { action: draft.action, resource: draft.resource, change: draft.change ?? {}, critical: draft.critical ?? false }),
      verify: (challengeId: string, credential: unknown) => post<M.StepUpProof>(`/v1/step-up/challenges/${seg(challengeId)}/verify`, { credential }),
      cancel: (challengeId: string) => post<{ challenge_id: string; status: string; grants_nothing: boolean }>(`/v1/step-up/challenges/${seg(challengeId)}/cancel`),
    },
    /** The organization-scoped approvals and onboarding routes of ZID-UX-WF-013C. The organization is always the caller's own. */
    org: {
      approvals: (orgId: string, q: M.OrgApprovalQuery = {}) => get<M.OrgApprovalInbox>(`/v1/organizations/${seg(orgId)}/approvals`, q),
      approval: (orgId: string, requestId: string) => get<M.OrgApprovalCase>(`/v1/organizations/${seg(orgId)}/approvals/${seg(requestId)}`),
      /** `version` is the request_hash of the version the person read (If-Match); a stale one is 409 VERSION_CONFLICT with no side effect. */
      decide: (orgId: string, requestId: string, body: { decision: M.OrgDecision; reason?: string }, version: string, opts: { proof?: string; idempotencyKey?: string } = {}) =>
        post<M.OrgApprovalCase>(`/v1/organizations/${seg(orgId)}/approvals/${seg(requestId)}/decisions`, body, {
          idempotencyKey: opts.idempotencyKey,
          headers: { "if-match": `"${version}"`, ...(opts.proof ? { "x-step-up-proof": opts.proof } : {}) },
        }),
      /** The forms behind "New request". */
      requestForms: (orgId: string) => get<M.ApprovalForms>(`/v1/organizations/${seg(orgId)}/approvals/forms`),
      /** Ask for a governed change. The server checks it; a refusal says why. */
      createRequest: (orgId: string, body: M.OrgApprovalCreate) => post<M.OrgApprovalCase>(`/v1/organizations/${seg(orgId)}/approvals`, body),
      attachments: (orgId: string, requestId: string) => get<M.ApprovalAttachments>(`/v1/organizations/${seg(orgId)}/approvals/${seg(requestId)}/attachments`),
      /** One document, sent as the file itself, while the request is open and unvoted. A repeat with the same key is the same document. */
      attach: (orgId: string, requestId: string, file: File, kind: M.ApprovalAttachmentKind, idempotencyKey: string) =>
        t.request<M.ApprovalAttachment>("POST", `/v1/organizations/${seg(orgId)}/approvals/${seg(requestId)}/attachments`, {
          raw: { data: file, contentType: file.type || "application/octet-stream" },
          idempotencyKey,
          headers: { "x-attachment-kind": kind, "x-attachment-name": file.name.slice(0, 200) },
        }),
      onboarding: (orgId: string) => get<M.OrgOnboarding>(`/v1/organizations/${seg(orgId)}/onboarding`),
      acknowledge: (orgId: string, body: M.OrgAcknowledgementIn, idempotencyKey?: string) =>
        post<M.OrgOnboarding & { recorded: boolean }>(`/v1/organizations/${seg(orgId)}/onboarding/acknowledgments`, body, { idempotencyKey }),
      activate: (orgId: string) => post<M.OrgOnboarding & { activated_now: boolean }>(`/v1/organizations/${seg(orgId)}/activation`),
    },
    registration: {
      capabilities: (country?: string) => get<M.RegistrationCapabilities>("/v1/registration/capabilities", { country }),
      start: (b: M.RegistrationIntentIn, idempotencyKey?: string) => post<M.RegistrationStarted>("/v1/registration/intents", b, { idempotencyKey }),
      resend: (requestId: string) => post<{ status: string; message: string }>("/v1/registration/resend", { request_id: requestId }),
      confirm: (token: string, requestId?: string) => post<M.RegistrationConfirmed>("/v1/registration/confirm", requestId ? { token, request_id: requestId } : { token }),
      status: (requestId: string) => get<M.RegistrationStatus>("/v1/registration/status", { request_id: requestId }),
    },
    approvals: {
      policy: () => get<M.ApprovalPolicy>("/v1/approval-policy"),
      soloStatus: () => get<M.SoloStatus>("/v1/governance/solo-status"),
      list: (q: { state?: M.ApprovalCaseState; limit?: number } = {}) => get<M.Items<M.ApprovalCase>>("/v1/approval-cases", q),
      read: (id: string) => get<M.ApprovalCase>(`/v1/approval-cases/${seg(id)}`),
      create: (draft: M.ApprovalCaseDraft) => post<M.ApprovalCase>("/v1/approval-cases", draft),
      /** The vote carries a step-up proof made for this case, this hash and this vote (see `stepUp.challenge`). */
      vote: (id: string, vote: "APPROVE" | "REJECT", requestHash: string, proof: string) =>
        post<M.ApprovalCase>(`/v1/approval-cases/${seg(id)}/votes`, { vote, request_hash: requestHash }, withProof(proof)),
      revise: (id: string, payload: Record<string, unknown>, reason: string) => post<M.ApprovalCase>(`/v1/approval-cases/${seg(id)}:revise`, { payload, reason }),
      cancel: (id: string) => post<M.ApprovalCase>(`/v1/approval-cases/${seg(id)}:cancel`),
      execute: (id: string, idempotencyKey: string) => post<M.ApprovalExecution>(`/v1/approval-cases/${seg(id)}/execute`, undefined, { idempotencyKey }),
      evidence: (id: string) => get<M.ApprovalEvidence>(`/v1/approval-cases/${seg(id)}/evidence`),
      openRecovery: (b: { principal_id: string; identity_reproof_ref: string; reason: string }) => post<M.ApprovalCase>("/v1/recovery-cases", b),
      contain: (b: { action_id: string; payload?: Record<string, unknown>; reason: string }) =>
        post<{ action_id: string; executed_at: string; region: string; post_incident_review: "REQUIRED" }>("/v1/containment-actions", b),
      reviewers: () => get<M.Items<M.GovernanceReviewer>>("/v1/governance/reviewers"),
      appointReviewer: (b: M.ReviewerAppointIn) => post<M.GovernanceReviewer & { activation_case: M.ApprovalCase }>("/v1/governance/reviewers", b),
      revokeReviewer: (id: string) => post<M.GovernanceReviewer>(`/v1/governance/reviewers/${seg(id)}:revoke`),
    },
    me: {
      userinfo: () => get<M.UserInfo>("/oauth2/userinfo"),
      sessions: () => get<M.Items<M.SessionView>>("/sessions"),
      revokeSession: (id: string) => post<{ session_id: string; status: M.SessionStatus }>(`/sessions/${seg(id)}:revoke`),
      revokeAllSessions: () => post<{ revoked: number }>("/sessions:revoke-all"),
      authenticators: () => get<M.Items<M.AuthenticatorView>>("/authn/authenticators"),
      revokeAuthenticator: (id: string, proof?: string) =>
        post<{ authenticator_id: string; status: M.AuthenticatorStatus }>(`/authn/authenticators/${seg(id)}:revoke`, undefined, withProof(proof)),
      issueRecoveryCodes: () => post<M.RecoveryCodes>("/authn/recovery-codes"),
      recoveryCodeStatus: () => get<M.RecoveryCodeStatus>("/authn/recovery-codes/status"),
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
      /** Read a provider's published settings from its address: the OpenID issuer, or the SAML metadata address. */
      discoverProvider: (protocol: "OIDC" | "SAML", url: string) => post<M.ProviderSettings>("/federation/integrations:discover", { protocol, url }),
      createIntegration: (body: IntegrationIn, proof?: string) => post<M.FederationIntegration>("/federation/integrations", body, withProof(proof)),
      rotate: (id: string, body: RotateIn) => post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:rotate`, body),
      setIdpInitiated: (id: string, body: IdpInitiatedIn) =>
        post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:idp-initiated`, body),
      disable: (id: string) => post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:disable`),
      trustAnchors: () => get<M.Items<M.TrustAnchorItem>>("/federation/trust-anchors"),
      createTrustAnchor: (body: AnchorIn) => post<M.TrustAnchor>("/federation/trust-anchors", body),
      disableTrustAnchor: (id: string) => post<M.TrustAnchor>(`/federation/trust-anchors/${seg(id)}:disable`),
    },

    policies: {
      releases: () => get<M.Items<M.PolicyReleaseSummary>>("/v1/policy-releases"),
      release: (id: string) => get<M.PolicyReleaseView>(`/v1/policy-releases/${seg(id)}`),
      createDraft: (body: { name: string; policies: string; cases: M.PolicyTestCase[] }) => post<M.PolicyReleaseView>("/v1/policy-releases", body),
      editDraft: (id: string, body: { policies: string; cases: M.PolicyTestCase[] }) =>
        t.request<M.PolicyReleaseView>("PUT", `/v1/policy-releases/${seg(id)}`, { body }),
      validate: (id: string) => post<M.PolicyValidation>(`/v1/policy-releases/${seg(id)}:validate`),
      simulate: (id: string, body: { principal: string; action: string; resource: string; context: Record<string, unknown> }) =>
        post<M.PolicySimulation>(`/v1/policy-releases/${seg(id)}:simulate`, body),
      review: (id: string) => post<M.PolicyReleaseView>(`/v1/policy-releases/${seg(id)}:review`),
      publish: (id: string) => post<M.PolicyReleaseView>(`/v1/policy-releases/${seg(id)}:publish`),
    },

    /** Development provider simulator (local/test cells only; 404 elsewhere). */
    dev: {
      simulatorStatus: () => get<{ enabled: boolean; adapter_id: string; jurisdictions: string[] }>("/dev/verification-simulator/status"),
      simulatorSetup: () => post<{ profile_created: boolean; adapter_id: string }>("/dev/verification-simulator/setup"),
      simulatorComplete: (id: string, body: { legal_name: string; birthdate: string | null; outcome: "VERIFIED" | "FAILED" | "FRAUD" }) =>
        post<{ verification_id: string; outcome: string }>(`/dev/verification-simulator/sessions/${seg(id)}:complete`, body),
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
      /** Replace an issuer's pinned public keys (key rollover); the old keys stop verifying at once. */
      rotateIssuerKeys: (body: TrustKeysIn) => post<M.IssuerKeysRotated>("/credential-issuer-trust:rotate-keys", body),
      registerRelyingParty: (body: RegistrationIn) => post<M.RpRegistration>("/relying-party-registrations", body),
      revoke: (id: string) => post<M.CredentialStatusChange>(`/credentials/${seg(id)}:revoke`),
      suspend: (id: string) => post<M.CredentialStatusChange>(`/credentials/${seg(id)}:suspend`),
      reinstate: (id: string) => post<M.CredentialStatusChange>(`/credentials/${seg(id)}:reinstate`),
      requestPresentation: (body: PresentationIn) => post<M.PresentationCreated>("/presentations", body),
      presentation: (id: string) => get<M.Presentation>(`/presentations/${seg(id)}`),
    },

    authority: {
      /** Newest first. `next_cursor` continues the list after the last row; pass it back as `cursor`. */
      grants: (q: { status?: M.GrantStatus; grantee_principal_id?: string; limit?: number; cursor?: string } = {}) =>
        get<M.Page<M.AuthorityGrant>>("/v1/authority-grants", q),
      approvals: (q: { state?: M.ApprovalState; limit?: number } = {}) => get<M.Items<M.ApprovalListItem>>("/v1/approval-requests", q),
      createGrant: (body: GrantCreate, idempotencyKey: string) => post<M.AuthorityGrant>("/v1/authority-grants", body, { idempotencyKey }),
      grant: (id: string) => get<M.AuthorityGrant>(`/v1/authority-grants/${seg(id)}`),
      chain: (id: string, riskClass?: number) => get<M.GrantChain>(`/v1/authority-grants/${seg(id)}/chain`, { risk_class: riskClass }),
      revokeGrant: (id: string) => post<{ authority_grant_id: string; status: M.GrantStatus }>(`/v1/authority-grants/${seg(id)}:revoke`),
      /** Pending requests the signed-in person did not open and holds authority to decide. */
      approvalsAwaitingMe: () => get<M.Items<M.ApprovalAwaiting>>("/v1/approval-requests/awaiting-me"),
      /** The caller's own latest unexpired request for exactly this change, or `request: null`. */
      approvalForChange: (q: { action: string; resource: string; change: string }) =>
        get<{ request: M.ApprovalListItem | null }>("/v1/approval-requests/for-change", q),
      createApproval: (body: ApprovalRequestCreate) => post<M.ApprovalCreated>("/v1/approval-requests", body),
      approval: (id: string, transactionContext: string) =>
        get<M.ApprovalView>(`/v1/approval-requests/${seg(id)}`, { transaction_context: transactionContext }),
      decideApproval: (id: string, body: ApprovalDecisionCreate) =>
        post<M.ApprovalDecisionResult>(`/v1/approval-requests/${seg(id)}/decisions`, body),
    },

    mandates: {
      list: (q: { status?: M.MandateStatus; executor_principal_id?: string; limit?: number } = {}) =>
        get<M.Items<M.Mandate>>("/v1/mandates", q),
      mine: (q: { limit?: number } = {}) => get<M.Items<M.MyMandate>>("/v1/mandates/mine", q),
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

    search: {
      query: (q: string, signal?: AbortSignal) =>
        t.request<{ query: string; items: M.SearchHit[] }>("GET", "/v1/search", { query: { q }, signal }),
    },

    alerts: {
      list: (query: { state?: M.AlertState; severity?: M.AlertSeverity; tasksOnly?: boolean } = {}) =>
        get<M.Items<M.PlatformAlert>>("/v1/alerts", {
          state: query.state,
          severity: query.severity,
          tasks_only: query.tasksOnly ? "true" : undefined,
        }),
      summary: () => get<M.AlertSummary>("/v1/alerts/summary"),
      acknowledge: (id: string) => post<M.PlatformAlert>(`/v1/alerts/${seg(id)}:acknowledge`),
      resolve: (id: string) => post<M.PlatformAlert>(`/v1/alerts/${seg(id)}:resolve`),
    },

    webhooks: {
      eventTypes: () => get<M.Items<M.WebhookEventType>>("/v1/webhooks/event-types"),
      signingKeys: () => get<{ keys: Record<string, unknown>[] }>("/v1/webhooks/signing-keys"),
      endpoints: () => get<M.Items<M.WebhookEndpoint>>("/v1/webhooks/endpoints"),
      createEndpoint: (body: M.WebhookEndpointCreate) => post<M.WebhookEndpoint>("/v1/webhooks/endpoints", body),
      verifyEndpoint: (id: string) => post<M.WebhookEndpoint>(`/v1/webhooks/endpoints/${seg(id)}:verify`),
      pauseEndpoint: (id: string) => post<M.WebhookEndpoint>(`/v1/webhooks/endpoints/${seg(id)}:pause`),
      resumeEndpoint: (id: string) => post<M.WebhookEndpoint>(`/v1/webhooks/endpoints/${seg(id)}:resume`),
      retireEndpoint: (id: string) => post<M.WebhookEndpoint>(`/v1/webhooks/endpoints/${seg(id)}:retire`),
      deliveries: (query: { endpoint_id?: string; status?: string } = {}) =>
        get<M.Items<M.WebhookDelivery>>("/v1/webhooks/deliveries", query),
      replay: (id: string) => post<M.WebhookDelivery>(`/v1/webhooks/deliveries/${seg(id)}:replay`),
    },

    tenant: {
      me: () => get<M.TenantView>("/v1/tenant"),
      members: () => get<M.Items<M.TenantMember>>("/v1/tenant/members"),
      invite: (role: M.InvitableRole) => post<M.Invitation>("/v1/tenant/members", { role }),
      reissue: (assignmentId: string) => post<M.Invitation>(`/v1/tenant/members/${seg(assignmentId)}:reissue`),
      revoke: (assignmentId: string) => post<M.TenantRole>(`/v1/tenant/members/${seg(assignmentId)}:revoke`),
      accept: () => post<M.TenantRole>("/v1/tenant/members/me:accept"),
      domains: () => get<M.Items<M.TenantDomain>>("/v1/tenant/domains"),
      addDomain: (domain: string, method: M.DomainMethod = "DNS_TXT") => post<M.TenantDomain>("/v1/tenant/domains", { domain, method }),
      verifyDomain: (id: string) => post<M.TenantDomain>(`/v1/tenant/domains/${seg(id)}:verify`),
      revokeDomain: (id: string) => post<M.TenantDomain>(`/v1/tenant/domains/${seg(id)}:revoke`),
      transitions: () => get<M.Items<M.TenantTransition>>("/v1/tenant/transitions"),
      configurationRevisions: () => get<M.Items<M.ConfigurationRevision>>("/v1/tenant/configuration-revisions"),
      addOwner: (principalId: string, approvalRequestId: string, proof?: string) =>
        post<M.TenantRole>("/v1/tenant/owners", { principal_id: principalId, approval_request_id: approvalRequestId }, withProof(proof)),
      removeOwner: (principalId: string, approvalRequestId: string, proof?: string) =>
        post<M.TenantRole>(`/v1/tenant/owners/${seg(principalId)}:remove`, { approval_request_id: approvalRequestId }, withProof(proof)),
      suspend: (reasonCode: string) => post<M.TenantView>("/v1/tenant:suspend", { reason_code: reasonCode }),
      resume: (approvalRequestId: string) => post<M.TenantView>("/v1/tenant:resume", { approval_request_id: approvalRequestId }),
      offboard: (approvalRequestId: string, reasonCode: string) =>
        post<M.TenantView>("/v1/tenant:offboard", { approval_request_id: approvalRequestId, reason_code: reasonCode }),
    },

    integration: {
      applications: () => get<M.Items<M.IntegrationApplication>>("/v1/integration/applications"),
      createApplication: (body: M.ApplicationCreate) => post<M.IntegrationApplication>("/v1/integration/applications", body),
      moveApplication: (id: string, status: M.AppStatus) =>
        post<M.IntegrationApplication>(`/v1/integration/applications/${seg(id)}:transition`, { status }),
      oauthClients: () => get<M.Items<M.OAuthClientRecord>>("/v1/integration/oauth-clients"),
      createOauthClient: (body: M.OAuthClientCreate) => post<M.OAuthClientRecord>("/v1/integration/oauth-clients", body),
      retireOauthClient: (id: string) =>
        post<{ client_id: string; status: string }>(`/v1/integration/oauth-clients/${seg(id)}:retire`, {}),
      connectors: () => get<M.Items<M.Connector>>("/v1/integration/connectors"),
      createConnector: (body: M.ConnectorCreate) => post<M.Connector>("/v1/integration/connectors", body),
      setCredential: (id: string, secret: string, notAfter?: string | null) =>
        t.request<{ connector_id: string; version: number }>("PUT", `/v1/integration/connectors/${seg(id)}/credential`, {
          body: { secret, not_after: notAfter ?? null },
        }),
      quarantine: (id: string, reasonCode: string) =>
        post<M.Connector>(`/v1/integration/connectors/${seg(id)}:quarantine`, { reason_code: reasonCode }),
      release: (id: string, approvalRequestId: string) =>
        post<M.Connector>(`/v1/integration/connectors/${seg(id)}:release`, { approval_request_id: approvalRequestId }),
      releases: (connectorId: string) => get<M.Items<M.MappingRelease>>(`/v1/integration/connectors/${seg(connectorId)}/mapping-releases`),
      createRelease: (connectorId: string, rules: M.MappingRule[]) =>
        post<M.MappingRelease>(`/v1/integration/connectors/${seg(connectorId)}/mapping-releases`, { rules }),
      simulate: (releaseId: string, records: Record<string, unknown>[]) =>
        post<M.MappingRelease>(`/v1/integration/mapping-releases/${seg(releaseId)}:simulate`, { records }),
      activateRelease: (releaseId: string, approvalRequestId: string) =>
        post<M.MappingRelease>(`/v1/integration/mapping-releases/${seg(releaseId)}:activate`, { approval_request_id: approvalRequestId }),
      routes: () => get<M.Items<M.LoginRoute>>("/v1/integration/routes"),
      addRoute: (body: { kind: M.RouteKind; match: string; connection_ref: string }) => post<M.LoginRoute>("/v1/integration/routes", body),
      resolveRoute: (body: { application_id?: string; login_domain?: string }) =>
        post<{ connection_ref: string | null; rule_id: string | null }>("/v1/integration/routes:resolve", body),
    },

    orgClaims: {
      search: (country: string, query: string) => post<M.OrgClaimSearch>("/v1/org-claims/search", { country, query }),
      create: (body: M.OrgClaimCreate, idempotencyKey: string) =>
        t.request<M.OrgClaimStatus>("POST", "/v1/org-claims", { body, idempotencyKey }),
      status: (id: string) => get<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/status`),
      refresh: (id: string) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/refresh`, {}),
      screening: (id: string, body: M.OrgClaimScreening) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/screening`, body),
      /** Say the claim is complete and ready for review. Accepted, not decided (202); once, and a repeat changes nothing. */
      submit: (id: string) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/submit`),
      /** Withdraw a claim that is not verified. Nothing is deleted; the applicant may start a new claim. */
      withdraw: (id: string) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/withdraw`),
      /** Declare again while the case is waiting for details; the earlier declaration stays. */
      amendScreening: (id: string, body: M.OrgClaimScreening) => t.request<M.OrgClaimStatus>("PUT", `/v1/org-claims/${seg(id)}/screening`, { body }),
      /** Appeal a rejection or ask for a correction; the case goes back to a person. */
      appeal: (id: string, body: M.OrgClaimAppeal) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/appeal`, body),
      proofs: (id: string) => get<M.OrgClaimProofs>(`/v1/org-claims/${seg(id)}/proofs`),
      /** One supporting document, sent as the file itself. A repeat with the same key is the same document. */
      uploadProof: (id: string, file: File, kind: M.OrgClaimProofKind, idempotencyKey: string) =>
        t.request<M.OrgClaimProof>("POST", `/v1/org-claims/${seg(id)}/proofs`, {
          raw: { data: file, contentType: file.type || "application/octet-stream" },
          idempotencyKey,
          headers: { "x-proof-kind": kind, "x-proof-name": file.name.slice(0, 200) },
        }),
    },

    onboarding: {
      gates: () => get<M.GateReport>("/v1/onboarding/gates"),
      declaredOrganization: () => get<{ declared: M.DeclaredOrganization | null }>("/v1/onboarding/organization"),
      submitOrganization: (body: M.OrganizationSubmission) => post<M.OrganizationResult>("/v1/onboarding/organization", body),
      /** Whether the owner approves onboarding steps alone (no second administrator). */
      mode: () => get<M.OnboardingApprovalMode>("/v1/onboarding/mode"),
      /** Where to send decisions and how far shadow has got. */
      connection: () => get<M.OnboardingConnection>("/v1/onboarding/connection"),
      plans: () => get<M.Items<M.OnboardingPlan>>("/v1/onboarding/plans"),
      createPlan: (body: M.PlanCreate) => post<M.OnboardingPlan>("/v1/onboarding/plans", body),
      approvePlan: (id: string, approvalRequestId: string | null) =>
        post<M.OnboardingPlan>(`/v1/onboarding/plans/${seg(id)}:approve`, { approval_request_id: approvalRequestId || null }),
      validatePlan: (id: string) => post<M.GateReport>(`/v1/onboarding/plans/${seg(id)}:validate`),
      startIntegration: (id: string) => post<M.GateReport>(`/v1/onboarding/plans/${seg(id)}:start-integration`),
      startShadow: (id: string) => post<M.GateReport>(`/v1/onboarding/plans/${seg(id)}:start-shadow`),
      startPilot: (id: string, approvalRequestId: string | null) =>
        post<M.GateReport>(`/v1/onboarding/plans/${seg(id)}:start-pilot`, { approval_request_id: approvalRequestId || null }),
      activate: (id: string, approvalRequestId: string | null) =>
        post<M.GateReport>(`/v1/onboarding/plans/${seg(id)}:activate`, { approval_request_id: approvalRequestId || null }),
      rollback: (id: string, reasonCode: string) =>
        post<{ tenant_status: M.TenantStatus }>(`/v1/onboarding/plans/${seg(id)}:rollback`, { reason_code: reasonCode }),
      stages: (planId: string) => get<M.Items<M.PilotStage>>(`/v1/onboarding/plans/${seg(planId)}/stages`),
      addStage: (planId: string, name: string, scope: Partial<M.StageScope>) =>
        post<M.PilotStage>(`/v1/onboarding/plans/${seg(planId)}/stages`, { name, scope }),
      activateStage: (stageId: string, approvalRequestId: string | null) =>
        post<M.PilotStage>(`/v1/onboarding/stages/${seg(stageId)}:activate`, { approval_request_id: approvalRequestId || null }),
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
      cases: (status?: string) => get<M.Items<M.EvidenceCase>>("/v1/evidence/cases", { status }),
      openCase: (body: { kind: M.EvidenceCaseKind; purpose: string; reference: string; legal_basis?: string | null; expires_at: string }) =>
        post<{ case_id: string; status: string }>("/v1/evidence/cases", body),
      case: (id: string) => get<M.EvidenceCaseDetail>(`/v1/evidence/cases/${seg(id)}`),
      addCaseItem: (id: string, body: { evidence_item_id: string; purpose: string }) =>
        post<{ custody_id: string; case_id: string }>(`/v1/evidence/cases/${seg(id)}/items`, body),
      closeCase: (id: string) => post<{ case_id: string; status: string }>(`/v1/evidence/cases/${seg(id)}:close`),
      provenance: (entityType: string, entityId: string, depth = 3) =>
        get<M.ProvenanceGraph>(`/v1/evidence/provenance/${seg(entityType)}/${seg(entityId)}`, { depth }),
      artifact: (artifactType: string, ref: string) => get<M.Items<M.EvidenceArtifact>>(`/v1/evidence/artifacts/${seg(artifactType)}/${seg(ref)}`),
    },

    privacy: {
      processors: () => get<M.Items<M.PrivacyProcessor>>("/v1/privacy/admin/processors"),
      registerProcessor: (body: M.ProcessorCreate) => post<M.PrivacyProcessor>("/v1/privacy/admin/processors", body),
      approveProcessor: (id: string) => post<M.PrivacyProcessor>(`/v1/privacy/admin/processors/${seg(id)}:approve`),
      suspendProcessor: (id: string) => post<M.PrivacyProcessor>(`/v1/privacy/admin/processors/${seg(id)}:suspend`),
      notices: () => get<M.Items<M.PrivacyNotice>>("/v1/privacy/admin/notices"),
      publishNotice: (body: { code: string; audience: string; content: string; jurisdictions?: string[]; content_uri?: string | null; approval_request_id: string }) =>
        post<M.PrivacyNotice>("/v1/privacy/admin/notices", body),
      admProfiles: () => get<M.Items<M.AdmProfile>>("/v1/privacy/admin/automated-decision-profiles"),
      createAdmProfile: (body: M.AdmCreate) => post<M.AdmProfile>("/v1/privacy/admin/automated-decision-profiles", body),
      packs: () => get<M.Items<M.JurisdictionPackView>>("/v1/privacy/jurisdiction-packs"),
      myConsents: () => get<M.Items<M.ConsentRecord>>("/v1/privacy/consents"),
      /** What the person can consent to, each with the exact notice to cite. */
      consentOptions: () => get<M.Items<M.ConsentOption>>("/v1/privacy/consent-options"),
      giveConsent: (body: ConsentIn) => post<{ consent_id: string; status: string }>("/v1/privacy/consents", body),
      withdrawConsent: (id: string, reason?: string) =>
        post<{ consent_id: string; status: string }>(`/v1/privacy/consents/${seg(id)}:withdraw`, reason ? { reason } : {}),
      myPreferences: () => get<M.Items<M.PreferenceRecord>>("/v1/privacy/preferences"),
      /** Global Privacy Control: the cell reads the browser's `Sec-GPC: 1` (the BFF forwards it) and records the opt-out. */
      preferenceSignal: () => post<{ applied: boolean; preference_type?: string }>("/v1/privacy/preferences:signal"),
      setPreference: (type: string, value: boolean) =>
        t.request<{ preference_type: string; value: boolean; revision: number }>("PUT", `/v1/privacy/preferences/${seg(type)}`, { body: { value } }),
      myRequests: () => get<M.Items<M.RightsRequest>>("/v1/privacy/rights-requests/mine"),
      openRequest: (body: RightsIn) => post<M.RightsRequest>("/v1/privacy/rights-requests", body),
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

    usage: {
      summary: (days = 7) => get<M.UsageSummary>("/v1/usage", { days }),
      limits: () => get<M.RateLimitStatus>("/v1/usage/limits"),
    },

    contacts: {
      list: () => get<M.Items<M.EmailContact>>("/v1/tenant/contacts"),
      add: (body: { purpose: M.EmailContactPurpose; address: string }) =>
        t.request<M.EmailContact>("PUT", "/v1/tenant/contacts", { body }),
      remove: (id: string) => t.request<void>("DELETE", `/v1/tenant/contacts/${seg(id)}`),
      /** Where the signed-in person's own security notices go. */
      setMine: (address: string) => t.request<{ contact_id: string; address: string }>("PUT", "/v1/account/contact", { body: { address } }),
    },

    billing: {
      /** The permissions this person holds in billing, and the catalogue of all of them. */
      access: () => get<M.BillingAccess>("/v1/tenant/billing-access/me"),
      grants: () => get<M.Items<M.BillingGrant>>("/v1/tenant/billing-grants"),
      giveAccess: (body: { grantee_principal_id: string; bundle: M.BillingBundle; reason: string; expires_at?: string | null }) =>
        post<{ grant_id: string }>("/v1/tenant/billing-grants", body),
      revokeAccess: (grantId: string) => post<{ grant_id: string; revoked: boolean }>(`/v1/tenant/billing-grants/${seg(grantId)}:revoke`),
      catalog: () => get<M.Catalog>("/v1/catalog"),
      entitlements: (tenantId: string) => get<M.EntitlementView>(`/v1/tenants/${seg(tenantId)}/entitlements`),
      /** The five-band view of what this plan includes (ZID-ENG-DEC-008). */
      effective: () => get<M.EffectiveEntitlements>("/v1/entitlements/effective"),
      /** What moving to a lower band would switch off. It deletes nothing. */
      downgradeImpact: (to: string) => get<M.DowngradeImpact>("/v1/entitlements/downgrade-impact", { to }),
      usageSummary: () => get<M.ReservationUsage>("/v1/usage/summary"),
      state: () => get<M.BillingState>("/v1/billing/state"),
      orders: () => get<M.Items<M.BillingOrder>>("/v1/billing/orders"),
      preview: () => get<M.BillPreview>("/v1/billing/preview"),
      ledger: (month?: string) => get<M.BillableSummary>("/v1/usage/billable", month ? { month } : undefined),
      /** The events behind a meter, so a bill can be reproduced. */
      billableEvents: (month: string, meter?: string, limit?: number) =>
        get<M.BillableEvents>("/v1/usage/billable/events", { month, meter, limit }),
      /** The rated lines of a month, each with its formula; 404 RATING_NOT_FOUND until the month is rated. */
      rated: (month: string) => get<M.RatedUsage>("/v1/usage/rated", { month }),
      invoices: () => get<M.Items<M.Invoice>>("/v1/invoices"),
      budgets: () => get<M.Items<M.UsageBudget>>("/v1/budgets"),
      setBudget: (body: M.BudgetIn) => t.request<M.UsageBudget>("PUT", "/v1/budgets", { body }),
      evaluateBudgets: () => post<{ raised: { key: string; threshold: number }[] }>("/v1/budgets:evaluate"),
      /** The company's own requests for a different plan, newest first, with the team's answer. */
      planRequests: () => get<M.Items<M.PlanRequest>>("/v1/billing/plan-requests"),
      /** Ask for a different plan. Changes no entitlement: the move is a signed order. */
      requestPlan: (plan: string, note?: string) =>
        post<{ request_id: string; status: M.PlanRequestStatus; plan: string; current_plan: string }>("/v1/billing/plan-requests", { plan, note: note?.trim() || null }),
      /** What is due now and the saved card (brand and last four only). */
      payment: () => get<M.BillingPayment>("/v1/billing/payment"),
      /** Pay what is outstanding on one issued invoice with the saved card, now. One idempotency key per click. */
      payInvoice: (invoiceId: string, idempotencyKey: string) =>
        post<{ status: "CAPTURED" | "FAILED"; failure_code: string | null }>(`/v1/invoices/${seg(invoiceId)}/pay`, { idempotency_key: idempotencyKey }),
      /** Start saving or replacing the card: the address of Stripe's hosted page. The card never reaches this app. */
      startPaymentSetup: () => post<{ url: string }>("/v1/billing/payment/setup"),
      /** After Stripe sends the buyer back: the cell verifies the session and makes the card the default. */
      /** Pay one invoice on Stripe's page (the way through when the bank asks for confirmation): the address to send the buyer to. */
      payOnline: (invoiceId: string) => post<{ url: string }>(`/v1/invoices/${seg(invoiceId)}/pay-online`),
      /** After Stripe sends the buyer back from paying: the cell verifies the session and records the payment once. */
      completeOnlinePayment: (sessionId: string) =>
        post<{ recorded: boolean; already_recorded: boolean }>("/v1/billing/payment/online-complete", { session_id: sessionId }),
      completePaymentSetup: (sessionId: string) => post<{ method: M.PaymentMethod }>("/v1/billing/payment/complete", { session_id: sessionId }),
      withdrawPlanRequest: (id: string) => post<{ request_id: string; status: M.PlanRequestStatus }>(`/v1/billing/plan-requests/${seg(id)}:withdraw`),
    },

    settings: {
      all: () => get<M.SettingsAll>("/v1/settings"),
      /** Replace an area's value. `expectedRevision` is the revision the edit was based on; a stale one is a 409. */
      put: <T>(area: M.SettingsArea, expectedRevision: number, value: T) =>
        t.request<M.SettingSnapshot<T>>("PUT", `/v1/settings/${seg(area)}`, { body: { expected_revision: expectedRevision, value } }),
      history: (area: M.SettingsArea) => get<{ area: M.SettingsArea; items: M.SettingsHistoryItem[] }>(`/v1/settings/${seg(area)}/history`),
      cryptography: () => get<M.CryptoOverview>("/v1/settings/cryptography"),
      notificationKinds: () => get<M.Items<M.NotificationKind>>("/v1/settings/notification-kinds"),
    },

    agents: {
      list: (status?: M.AgentStatus) => get<M.Items<M.AgentItem>>("/v1/machine/agents", { status }),
      get: (id: string) => get<M.AgentDetail>(`/v1/machine/agents/${seg(id)}`),
      register: (body: M.AgentCreate) => post<M.AgentItem>("/v1/machine/agents", body),
      registerChild: (id: string, body: Omit<M.AgentCreate, "blueprint_id" | "blueprint_version" | "owning_organization_id" | "model_provider">) =>
        post<M.AgentItem>(`/v1/machine/agents/${seg(id)}/children`, body),
      submit: (id: string) => post<M.AgentItem>(`/v1/machine/agents/${seg(id)}:submit`),
      attest: (id: string, gate: M.AgentGate, note: string) =>
        post<{ gate: M.AgentGate; attested_by: string; attested_at: M.Iso }>(`/v1/machine/agents/${seg(id)}:attest`, { gate, note }),
      activate: (id: string) => post<M.AgentItem>(`/v1/machine/agents/${seg(id)}:activate`),
      suspend: (id: string, reason_code: string) =>
        post<{ agent_principal_id: string; sub_agents: number; bindings_suspended: number }>(`/v1/machine/agents/${seg(id)}:suspend`, { reason_code }),
      revoke: (id: string, reason_code: string) =>
        post<{ agent_principal_id: string; sub_agents_suspended: number; bindings_revoked: number }>(`/v1/machine/agents/${seg(id)}:revoke`, { reason_code }),
      retire: (id: string) => post<M.AgentItem>(`/v1/machine/agents/${seg(id)}:retire`),
      blueprints: () => get<M.Items<M.BlueprintRelease>>("/v1/machine/blueprints"),
      createBlueprint: (body: M.BlueprintCreate) => post<{ blueprint_id: string; version: string; review_status: M.BlueprintReview }>("/v1/machine/blueprints", body),
      approveBlueprint: (id: string, version: string) =>
        post<{ blueprint_id: string; version: string; review_status: M.BlueprintReview }>(`/v1/machine/blueprints/${seg(id)}/versions/${seg(version)}:approve`),
      revokeBlueprint: (id: string, version: string, reason_code: string) =>
        post<{ bindings_suspended: number; agents_suspended: number }>(`/v1/machine/blueprints/${seg(id)}/versions/${seg(version)}:revoke`, { reason_code }),
      tools: () => get<M.Items<M.ToolItem>>("/v1/machine/tools"),
      registerTool: (body: M.ToolCreate) => post<M.ToolItem>("/v1/machine/tools", body),
      activateTool: (id: string) => post<M.ToolItem>(`/v1/machine/tools/${seg(id)}:activate`),
      suspendTool: (id: string, reason_code: string) => post<M.ToolItem>(`/v1/machine/tools/${seg(id)}:suspend`, { reason_code }),
      retireTool: (id: string) => post<M.ToolItem>(`/v1/machine/tools/${seg(id)}:retire`),
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

    /** Commercial operator surface (ZID-COM-001). Reads are typed; every write goes through `call`, driven by the
     * action catalogue in the operator console so that each route has exactly one description of its body. */
    commercial: {
      books: () => get<M.Items<M.PriceBookSummary>>("/v1/commercial/price-books"),
      book: (id: string) => get<M.PriceBookDetail>(`/v1/commercial/price-books/${seg(id)}`),
      /** Every company's open requests for a different plan, oldest first. */
      planRequests: () => get<M.Items<M.PlanRequest & { tenant_id: string; company: string | null }>>("/v1/commercial/plan-requests"),
      /** ACCEPTED: the team will raise the order. DECLINED: the company is told why. The entitlement is untouched. */
      answerPlanRequest: (id: string, decision: "ACCEPTED" | "DECLINED", note?: string) =>
        post<{ request_id: string; status: M.PlanRequestStatus; tenant_id: string }>(`/v1/commercial/plan-requests/${seg(id)}:answer`, { decision, note: note?.trim() || null }),
      exceptions: () => get<M.Items<M.AssuranceException>>("/v1/commercial/exceptions"),
      /** What the payment provider reported that needs a person, oldest first. `resolved` also lists the closed ones. */
      paymentEvents: (resolved = false) => get<M.Items<M.PaymentEvent>>("/v1/commercial/payment-events", resolved ? { resolved: "true" } : undefined),
      kpis: () => get<Record<string, unknown>>("/v1/commercial/kpis"),
      vendorCosts: (start: string, end: string) => get<M.Items<M.VendorCostRow>>("/v1/commercial/vendor-costs", { start, end }),
      auditPack: (tenantId: string, month: string) => get<Record<string, unknown>>(`/v1/commercial/tenants/${seg(tenantId)}/audit-pack`, { month }),
      journal: (tenantId: string, start: string, end: string) => get<Record<string, unknown>>(`/v1/commercial/tenants/${seg(tenantId)}/journal`, { start, end }),
      /** Check a signed audit pack: signature, unaltered body and bundle digest, against the trusted evidence keys. */
      verifyAuditPack: (pack: Record<string, unknown>) =>
        t.request<M.AuditPackVerdict>("POST", "/v1/commercial/audit-pack:verify", { body: { pack } }),
      quote: (id: string) => get<Record<string, unknown>>(`/v1/commercial/quotes/${seg(id)}`),
      verifyChangeLog: () => get<Record<string, unknown>>("/v1/commercial/change-log:verify"),
      verifyInvoiceChain: () => get<Record<string, unknown>>("/v1/commercial/invoice-chain:verify"),
      call: (method: "POST" | "PUT", path: string, body: unknown) => t.request<Record<string, unknown>>(method, path, { body: body ?? {} }),
    },

    /** Cells and governed recovery operations (RESILIENCE-001 sections 15-20, 25-30). */
    platform: {
      readiness: (cells: string[]) => get<{ cells: M.CellReadiness[] }>("/platform/recovery-readiness", { cells: cells.join(",") }),
      cell: (id: string) => get<M.CellReadiness>(`/platform/cells/${seg(id)}`),
      quarantineCell: (id: string, reason: string) =>
        post<{ cell_id: string; previous_status: string; status: string }>(`/platform/cells/${seg(id)}:quarantine`, { reason }),
      restoreCell: (id: string, reason: string) =>
        post<{ cell_id: string; previous_status: string; status: string }>(`/platform/cells/${seg(id)}:restore`, { reason }),
      reportReadiness: (id: string, body: M.ReadinessReport) => t.request<M.CellReadiness>("PUT", `/platform/cells/${seg(id)}/readiness`, { body }),
      operations: () => get<{ operations: M.RecoveryOperation[] }>("/platform/operations"),
      operation: (id: string) => get<M.RecoveryOperation>(`/platform/operations/${seg(id)}`),
      declare: (kind: "FAILOVER" | "MIGRATION" | "FAILBACK", tenantId: string, targetCellId: string) =>
        post<M.RecoveryOperation>(
          `/platform/tenants/${seg(tenantId)}:${kind === "FAILOVER" ? "failover" : kind === "MIGRATION" ? "migrate" : "failback"}`,
          { target_cell_id: targetCellId },
        ),
      restore: (targetCellId: string, tenantId?: string | null) =>
        post<M.RecoveryOperation>("/platform/restore-jobs", { target_cell_id: targetCellId, target_tenant_id: tenantId || null }),
      advance: (id: string, body: { step: string; evidence_ref?: string | null; probes?: Record<string, boolean> | null }) =>
        post<M.RecoveryOperation>(`/platform/operations/${seg(id)}:advance`, body),
      abort: (id: string, reason: string) => post<M.RecoveryOperation>(`/platform/operations/${seg(id)}:abort`, { reason }),
      exercises: () => get<{ exercises: M.DrExercise[] }>("/platform/dr-exercises"),
      recordExercise: (body: M.DrExerciseCreate) =>
        post<{ exercise_id: string; passed: boolean; target_rto_s: number | null; target_rpo_s: number | null }>("/platform/dr-exercises", body),
    },

    /** Operational control (SRE-001 section 29): catalogue, SLOs, change policy, incidents, PRR. */
    ops: {
      services: () => get<{ version: string; services: M.CatalogueService[] }>("/ops/services"),
      health: (id: string) => get<M.ServiceHealth>(`/ops/services/${seg(id)}/health`),
      changePolicy: (id: string, changeClass: M.ChangeClass) =>
        get<M.ChangePolicy>(`/ops/services/${seg(id)}/change-policy`, { change_class: changeClass }),
      readiness: (id: string) => get<M.ReadinessVerdict>(`/ops/services/${seg(id)}/readiness`),
      freeze: (id: string, reasonCode: string, incidentId?: string | null) =>
        post<{ service_id: string; freeze_id: string; frozen: boolean }>(`/ops/services/${seg(id)}:freeze-changes`, {
          reason_code: reasonCode,
          incident_id: incidentId || null,
        }),
      unfreeze: (id: string) => post<{ service_id: string; freeze_id: string; frozen: boolean }>(`/ops/services/${seg(id)}:unfreeze-changes`),
      cellHealth: (id: string) => get<M.CellReadiness & { state: string; recovery_in_progress: boolean }>(`/ops/cells/${seg(id)}/health`),
      incidents: (openOnly = false) => get<{ incidents: M.Incident[] }>("/ops/incidents", { open: openOnly ? "true" : undefined }),
      incident: (id: string) => get<M.Incident>(`/ops/incidents/${seg(id)}`),
      declareIncident: (body: M.IncidentCreate) => post<M.Incident>("/ops/incidents", body),
      updateIncident: (id: string, body: M.IncidentUpdate) => t.request<M.Incident>("PATCH", `/ops/incidents/${seg(id)}`, { body }),
      addEntry: (id: string, body: { kind: "ACTION" | "DECISION" | "COMMUNICATION"; note: string }) =>
        post<{ incident_id: string; seq: number }>(`/ops/incidents/${seg(id)}/actions`, body),
      resolve: (id: string, validation: string) => post<M.Incident>(`/ops/incidents/${seg(id)}:resolve`, { validation }),
      addActionItem: (id: string, body: { title: string; owner?: string | null; due?: string | null }) =>
        post<{ incident_id: string; item_id: string }>(`/ops/incidents/${seg(id)}/action-items`, body),
      verifyActionItem: (id: string, itemId: string) =>
        post<{ verified: boolean }>(`/ops/incidents/${seg(id)}/action-items/${seg(itemId)}:verify`),
      close: (id: string, pirRef: string | null) => post<M.Incident>(`/ops/incidents/${seg(id)}:close`, { pir_ref: pirRef }),
      recordReview: (body: { service_id: string; release_ref: string; gates: M.PrrGate[] }) =>
        post<{ review_id: string; service_id: string; production_ready: boolean; blocking: M.PrrBlocker[] }>("/ops/readiness-reviews", body),
    },
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
