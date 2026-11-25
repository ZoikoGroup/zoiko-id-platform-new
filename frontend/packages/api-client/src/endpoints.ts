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
import type * as C from "./checks";
import type * as VA from "./verify-analytics";
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
export type IntegrationUpdateIn = S<Fed, "UpdateIn">;
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
export type DirectorySyncIn = S<Scim, "DirectorySyncIn">;
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
export type LockboxDecisionIn = S<Op, "zid_platform__modules__operator_access__api__lockbox__DecisionBody">;
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
      updateIntegration: (id: string, body: IntegrationUpdateIn) =>
        t.request<M.FederationIntegration>("PATCH", `/federation/integrations/${seg(id)}`, { body }),
      disable: (id: string) => post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:disable`),
      enable: (id: string, approvalRequestId?: string) =>
        post<M.FederationIntegration>(`/federation/integrations/${seg(id)}:enable`, { approval_request_id: approvalRequestId }),
      trustAnchors: () => get<M.Items<M.TrustAnchorItem>>("/federation/trust-anchors"),
      createTrustAnchor: (body: AnchorIn) => post<M.TrustAnchor>("/federation/trust-anchors", body),
      disableTrustAnchor: (id: string) => post<M.TrustAnchor>(`/federation/trust-anchors/${seg(id)}:disable`),
      enableTrustAnchor: (id: string, approvalRequestId?: string) =>
        post<M.TrustAnchor>(`/federation/trust-anchors/${seg(id)}:enable`, { approval_request_id: approvalRequestId }),
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
      startBulk: (body: M.BulkVerificationIn) => post<M.BulkVerificationResult>("/verifications:bulk", body),
      get: (id: string) => get<M.VerificationSession>(`/verifications/${seg(id)}`),
      /** WF01 screen A5: what was checked and how each check came out, in the platform's words. Facts only: no evidence, no claim value, no score. */
      proofing: (id: string) => get<M.ProofingFacts>(`/verifications/${seg(id)}/proofing`),
      /** WF01-014: the company's monthly supplier spend budget and this month's spend. Money is a decimal string, never a float. */
      budget: () => get<M.VerificationBudget>("/verification-budget"),
      setBudget: (body: { monthly_limit: string; currency: string }) => t.request<M.VerificationBudget>("PUT", "/verification-budget", { body }),
      clearBudget: () => t.request<M.VerificationBudget>("DELETE", "/verification-budget"),
      usage: (query?: { since?: string; until?: string }) => get<M.VerificationUsage>("/verification-usage", query),
      /** WF01-011/012: whether a face engine could be approved today and what is missing. Read only. */
      engineReadiness: () => get<M.EngineReadiness>("/verification-engine-readiness"),
      methodCertification: () => get<M.MethodCertificationBoard>("/verification-method-certification"),
      ageCheck: (body: M.AgeCheckIn) => post<M.AgeCheckResult>("/v2/age-checks", body),
      cancel: (id: string) => post<M.VerificationSession>(`/verifications/${seg(id)}:cancel`),
      reverify: (id: string) => post<M.VerificationSession>(`/verifications/${seg(id)}:reverify`),
      /** Where the caller is on the risk tiers R0 to R3 and what the next one needs. `cannot` (NO_CAMERA, NO_PHONE, NO_STANDARD_ID) shows the approved alternatives. */
      tier: (cannot: string[] = []) => get<M.VerificationTier>(`/v1/verification/tier${cannot.length ? `?${cannot.map((c) => `cannot=${encodeURIComponent(c)}`).join("&")}` : ""}`),
      assertions: (principalId: string) => get<M.Items<M.VerificationAssertion>>(`/principals/${seg(principalId)}/verification-assertions`),
      assurance: (principalId: string) => get<M.Assurance>(`/principals/${seg(principalId)}/assurance`),
      revokeAssertion: (id: string) => post<{ assertion_id: string; status: "REVOKED" }>(`/verification-assertions/${seg(id)}:revoke`),
      rely: (body: RelianceIn) => post<M.RelianceDecision>("/verification-reliance-decisions", body),
      /** Which biometric methods this tenant's live sessions may use for a country and purpose, and every gate that refuses one (CR-107, CR-108). */
      biometricEligibility: (country: string, purpose = "IDENTITY_VERIFICATION") =>
        get<M.BiometricEligibility>("/v1/biometric-methods/eligibility", { country, purpose }),
      decideReview: (caseId: string, body: ReviewIn) => post<M.ReviewOutcome>(`/verification-review-cases/${seg(caseId)}/decisions`, body),
    },

    /** Uploaded evidence (ZID-INT-002): bytes are kept only after a clean scan; everything here returns metadata. */
    uploads: {
      list: () => get<M.UploadList>("/v2/evidence/uploads"),
      announce: (body: M.UploadAnnounce, idempotencyKey: string) => post<M.UploadAnnounced>("/v2/evidence/uploads", body, { idempotencyKey }),
      send: (uploadUrl: string, token: string, file: Blob) =>
        t.request<M.UploadManifest>("PUT", uploadUrl, { raw: { data: file, contentType: "application/octet-stream" }, headers: { "x-upload-token": token } }),
      remove: (ref: string) => t.request<M.UploadManifest>("DELETE", `/v2/evidence/${seg(ref)}`),
      validate: (body: { evidence_ref: string; declared_type: string; checks: string[]; expected_sha256?: string }) => post<M.DocumentValidation>("/v2/documents/validate", body),
    },

    /** Independent assurance (ZID-UAT-005): read views over signed evidence. A missing run or manifest reads NOT_RUN, never green. */
    assurance: {
      requirements: (suite?: string) => get<M.UatRequirements>(`/v2/assurance/requirements${suite ? `?suite=${seg(suite)}` : ""}`),
      preview: (releaseId: string) => get<M.UatPreview>(`/v2/assurance/release-candidates/${seg(releaseId)}`),
      providerLab: () => get<M.ProviderLabReport>("/v2/assurance/provider-lab"),
      fixtures: () => get<M.UatFixtureList>("/v2/assurance/fixtures"),
      createFixture: (body: { name: string; content_sha256: string }) => post<{ fixture_id: string; revision: number; state: string }>("/v2/assurance/fixtures", body),
      reviewFixture: (id: string, body: { revision: number; approve: boolean; rationale: string }) => post<{ state: string }>(`/v2/assurance/fixtures/${seg(id)}/review`, body),
      runs: () => get<M.UatRunList>("/v2/assurance/test-runs"),
      evidence: (runId: string, audience: string) => get<M.UatEvidence>(`/v2/assurance/test-runs/${seg(runId)}/evidence?audience=${seg(audience)}`),
      verify: (body: M.UatVerifyIn) => post<M.UatVerifyResult>("/v2/assurance/evidence:verify", body),
      decide: (releaseId: string, body: M.UatDecisionIn) => post<M.UatSigned>(`/v2/assurance/releases/${seg(releaseId)}/decisions`, body),
      rollback: (releaseId: string, body: M.UatRollbackIn) => post<M.UatRolledBack>(`/v2/assurance/releases/${seg(releaseId)}/rollback`, body),
      confirmRisks: (risks: Record<string, "P0" | "P1" | "P2">) => post<M.UatRiskConfirmed>("/v2/assurance/baselines/risk-confirmations", { risks }),
    },

    credentials: {
      list: (q: { status?: M.IssuedCredentialStatus; limit?: number } = {}) => get<M.Items<M.IssuedCredential>>("/credentials", q),
      configurations: () => get<M.Items<M.CredentialConfigurationItem>>("/credential-configurations"),
      issuerTrust: () => get<M.Items<M.IssuerTrustItem>>("/credential-issuer-trust"),
      /** What the platform trusts, read-only (WF-04). */
      trustRegistry: () => get<M.TrustRegistry>("/v1/credentials/registry"),
      /** One person's credential checks, decisions and appeals in one read (WF-02). */
      dossier: (subjectId: string) => get<M.CredentialDossier>(`/v1/credentials/dossiers/${seg(subjectId)}`),
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

    serviceRequests: {
      list: () => get<M.Items<M.ServiceRequest>>("/v1/service-requests"),
      create: (body: M.ServiceRequestIn) => post<M.ServiceRequest>("/v1/service-requests", body),
      withdraw: (id: string) => post<{ request_id: string; status: string }>(`/v1/service-requests/${seg(id)}:withdraw`, {}),
    },
    insights: {
      risk: (days: number, unit?: string) => get<M.RiskInsights>(`/v1/insights/risk?days=${days}${unit ? `&unit_id=${seg(unit)}` : ""}`),
      assurance: (days: number, unit?: string) => get<M.AssuranceReport>(`/v1/insights/assurance?days=${days}${unit ? `&unit_id=${seg(unit)}` : ""}`),
    },
    delegationTemplates: {
      list: () => get<M.Items<M.DelegationTemplate>>("/v1/delegation-templates"),
      create: (body: M.DelegationTemplateIn) => post<M.DelegationTemplate>("/v1/delegation-templates", body),
      retire: (id: string) => post<{ template_id: string; status: string }>(`/v1/delegation-templates/${seg(id)}:retire`, {}),
      apply: (id: string, body: M.DelegationTemplateApply) =>
        post<{ authority_grant_id: string; valid_to: string }>(`/v1/delegation-templates/${seg(id)}:apply`, body),
    },
    authority: {
      /** Newest first. `next_cursor` continues the list after the last row; pass it back as `cursor`. */
      grants: (q: { status?: M.GrantStatus; grantee_principal_id?: string; limit?: number; cursor?: string } = {}) =>
        get<M.Page<M.AuthorityGrant>>("/v1/authority-grants", q),
      /** The permissions given to the signed-in person. No company-wide right is needed: it returns only their own. */
      myGrants: () => get<M.Items<M.MyGrant>>("/v1/authority-grants/mine"),
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
      orgUnits: () => get<M.Items<M.OrgUnit> & { max_depth: number }>("/v1/tenant/org-units"),
      addOrgUnit: (body: M.OrgUnitIn) => post<{ unit_id: string; depth: number }>("/v1/tenant/org-units", body),
      retireOrgUnit: (id: string) => post<{ unit_id: string }>(`/v1/tenant/org-units/${seg(id)}:retire`, {}),
      addOrgUnitMember: (id: string, principal_id: string, title: string) => post<{ unit_id: string }>(`/v1/tenant/org-units/${seg(id)}/members`, { principal_id, title }),
      customRoles: () => get<M.Items<M.CustomRole>>("/v1/tenant/custom-roles"),
      customRoleActions: (base: string, kind: "NARROW" | "EXTEND" = "NARROW") =>
        get<{ base_role: string; kind: string; actions: M.CustomRoleAction[] }>(`/v1/tenant/custom-roles/actions?base_role=${seg(base)}&kind=${kind}`),
      createCustomRole: (body: M.CustomRoleIn) => post<M.CustomRole>("/v1/tenant/custom-roles", body),
      retireCustomRole: (id: string) => post<{ role_id: string; status: string }>(`/v1/tenant/custom-roles/${seg(id)}:retire`, {}),
      customRoleMembers: (id: string) => get<M.Items<M.CustomRoleMember>>(`/v1/tenant/custom-roles/${seg(id)}/members`),
      giveCustomRole: (id: string, principal_id: string) => post<{ role_id: string }>(`/v1/tenant/custom-roles/${seg(id)}/members`, { principal_id }),
      takeCustomRole: (id: string, principal_id: string) =>
        post<{ revoked: boolean }>(`/v1/tenant/custom-roles/${seg(id)}/members/${seg(principal_id)}:revoke`, {}),
      /** The published permission matrices: a catalogue for navigation, never an enforcement control. The server decides every request. */
      roleMatrix: () => get<M.RoleMatrix>("/v1/roles/matrix"),
      /** What the caller's live roles could do if every other condition held. */
      myPermissions: () => get<M.MyPermissions>("/v1/tenant/me/permissions"),
      invite: (role: M.InvitableRole, display_name?: string, email?: string, sendEmail?: boolean) =>
        post<M.Invitation>("/v1/tenant/members", { role, ...(display_name ? { display_name } : {}), ...(email ? { email, send_email: sendEmail === true } : {}) }),
      /** Email the fixed invitation to this address. The invitation gets a fresh single-use link (the one shown before stops working), returned here. */
      emailInvitation: (assignmentId: string, email: string) => post<M.Invitation>(`/v1/tenant/members/${seg(assignmentId)}:email-invitation`, { email }),
      /** An owner or admin sets or clears a colleague's name (the member number never changes). */
      rename: (principal_id: string, name: string | null) => post<M.Person>(`/v1/tenant/members/${seg(principal_id)}:rename`, { name }),
      /** A person sets or clears their own name. */
      nameMe: (name: string | null) => post<M.Person>("/v1/tenant/members/me:name", { name }),
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
      sandboxIdentities: () => get<M.Items<M.SandboxIdentity> & { max_live: number }>("/v1/integration/sandbox-identities"),
      createSandboxIdentities: (body: M.SandboxIdentitiesIn) => post<M.Items<M.SandboxIdentity>>("/v1/integration/sandbox-identities", body),
      retireSandboxIdentity: (id: string) =>
        post<{ principal_id: string; status: string }>(`/v1/integration/sandbox-identities/${seg(id)}:retire`, {}),
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
      /** Only a VERIFIED claim (all four checks complete) creates the organization; the server refuses anything else. */
      establish: (id: string) => post<M.OrgClaimEstablished>(`/v1/org-claims/${seg(id)}/establish`),
      /** Withdraw a claim that is not verified. Nothing is deleted; the applicant may start a new claim. */
      withdraw: (id: string) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/withdraw`),
      /** Declare again while the case is waiting for details; the earlier declaration stays. */
      amendScreening: (id: string, body: M.OrgClaimScreening) => t.request<M.OrgClaimStatus>("PUT", `/v1/org-claims/${seg(id)}/screening`, { body }),
      /** Appeal a rejection or ask for a correction; the case goes back to a person. */
      appeal: (id: string, body: M.OrgClaimAppeal) => post<M.OrgClaimStatus>(`/v1/org-claims/${seg(id)}/appeal`, body),
      proofs: (id: string) => get<M.OrgClaimProofs>(`/v1/org-claims/${seg(id)}/proofs`),
      /** Read a certificate picture and compare it with the claim without saving it: nothing is stored. */
      checkCertificate: (id: string, file: File) =>
        t.request<{ stored: false; read: M.OrgClaimProofRead }>("POST", `/v1/org-claims/${seg(id)}/certificate:check`, {
          raw: { data: file, contentType: file.type || "application/octet-stream" },
        }),
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
      createSource: (body: SourceIn) => post<M.ProvisioningSourceCreated>("/provisioning/sources", body),
      suspendSource: (id: string) => post<M.ProvisioningSource>(`/provisioning/sources/${seg(id)}:suspend`),
      resumeSource: (id: string, approvalRequestId?: string | null) =>
        post<M.ProvisioningSource>(`/provisioning/sources/${seg(id)}:resume`, approvalRequestId ? { approval_request_id: approvalRequestId } : undefined),
      /** A company's own directory (Auth0 first) read by the worker into one of its sources. The secret is write-only: it is never returned. */
      directorySyncs: () => get<M.Items<M.DirectorySync>>("/provisioning/directory-syncs"),
      createDirectorySync: (body: DirectorySyncIn) => post<M.DirectorySync>("/provisioning/directory-syncs", body),
      runDirectorySync: (id: string) => post<{ sync_id: string; status: string }>(`/provisioning/directory-syncs/${seg(id)}:run`, {}),
      pauseDirectorySync: (id: string) => post<M.DirectorySync>(`/provisioning/directory-syncs/${seg(id)}:pause`, {}),
      resumeDirectorySync: (id: string) => post<M.DirectorySync>(`/provisioning/directory-syncs/${seg(id)}:resume`, {}),
      setDirectorySyncSecret: (id: string, secret: string) =>
        t.request<{ sync_id: string; version: number }>("PUT", `/provisioning/directory-syncs/${seg(id)}/credential`, { body: { secret } }),
    },

    evidence: {
      /** Several receipts in one signed package that expires; creating one needs a fresh strong sign-in. */
      bulkExports: (limit?: number) => get<M.Items<M.BulkExport>>("/v1/evidence/bulk-exports", { limit }),
      createBulkExport: (body: M.BulkExportIn) => post<Record<string, unknown>>("/v1/evidence/bulk-exports", body),
      bulkExport: (id: string) => get<Record<string, unknown>>(`/v1/evidence/bulk-exports/${seg(id)}`),
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
      /** The /v2 control plane (ZID-PRI-004): DPIA packages with their release gates, and breach cases with clocks and biometric containment (CR-108). */
      v2: {
        dpiaPackages: () => get<M.Items<M.DpiaPackageSummary>>("/v2/privacy/dpia-packages"),
        dpiaPackage: (id: string) => get<M.DpiaPackage>(`/v2/privacy/dpia-packages/${seg(id)}`),
        createDpiaPackage: (body: M.DpiaPackageIn) => post<M.DpiaPackage>("/v2/privacy/dpia-packages", body),
        setDpiaRisks: (id: string, risks: M.DpiaRisk[], consultationRef: string | null) =>
          t.request<M.DpiaPackage>("PUT", `/v2/privacy/dpia-packages/${seg(id)}/risks`, { body: { risks, consultation_ref: consultationRef } }),
        signDpiaGate: (id: string, body: { gate: string; role: string; evidence_uri: string }) =>
          post<M.DpiaPackage>(`/v2/privacy/dpia-packages/${seg(id)}/gate-signoffs`, body),
        decideDpiaPackage: (id: string, body: { approve: boolean; rationale: string; valid_days?: number }) =>
          post<M.DpiaPackage>(`/v2/privacy/dpia-packages/${seg(id)}/decisions`, body),
        releaseGate: (market: string, product: string, version: string) =>
          get<M.ReleaseGateView>(`/v2/privacy/release-gates/${seg(market)}/${seg(product)}/${seg(version)}`),
        breaches: (openOnly = false) => get<M.Items<M.BreachCase>>("/v2/privacy/breaches", { open_only: openOnly ? "true" : undefined }),
        breach: (id: string) => get<M.BreachCase>(`/v2/privacy/breaches/${seg(id)}`),
        /** B09 audit dossier (CR-110): the case, clocks, capture timeline and audit trail; codes and hashes only. */
        breachDossier: (id: string) => get<Record<string, unknown>>(`/v2/privacy/breaches/${seg(id)}/dossier`),
        openBreach: (body: M.BreachIn) => post<M.BreachCase>("/v2/privacy/breaches", body),
        notifyBreach: (id: string, body: { jurisdiction: string; notified_at: string; reference: string }) =>
          post<M.BreachCase>(`/v2/privacy/breaches/${seg(id)}/notifications`, body),
        closeBreach: (id: string) => post<M.BreachCase>(`/v2/privacy/breaches/${seg(id)}/close`),
        pauseCapture: (id: string) => post<M.BreachCase>(`/v2/privacy/breaches/${seg(id)}/biometric-capture:pause`),
        releaseCapture: (id: string, reason: string) => post<M.BreachCase>(`/v2/privacy/breaches/${seg(id)}/biometric-capture:release`, { reason }),
        addBreachDataClasses: (id: string, dataClasses: string[]) =>
          post<M.BreachCase>(`/v2/privacy/breaches/${seg(id)}/data-classes`, { data_classes: dataClasses }),
        /** The person's own verification session (WF-04 B01, B02): what is checked and why, the notice, consent only where it is the basis, and other ways. */
        session: (ref: string) => get<M.SubjectSessionStatus>(`/v2/verification-sessions/${seg(ref)}/subject-status`),
        acknowledgeNotice: (ref: string, noticeVersion: string, language: string) =>
          post<{ ack_id: string; notice: string; acknowledged_at: string }>(`/v2/verification-sessions/${seg(ref)}/notices/acknowledgments`, {
            notice_version: noticeVersion,
            language,
          }),
        sessionConsent: (ref: string, noticeVersion: string) =>
          post<Record<string, unknown>>(`/v2/verification-sessions/${seg(ref)}/consent-records`, {
            notice_version: noticeVersion,
            capture_method: "WEB",
            affirmative_action: "CHECKBOX_AND_CONTINUE",
          }),
        chooseRoute: (ref: string, mode: string) => post<M.SubjectSessionStatus>(`/v2/verification-sessions/${seg(ref)}/route`, { mode }),
        cancelSession: (ref: string) => post<M.SubjectSessionStatus>(`/v2/verification-sessions/${seg(ref)}/cancel`),
        assistedQueue: (state?: M.AssistedItem["assisted_state"]) => get<M.Items<M.AssistedItem>>("/v2/privacy/assisted-routes", { state }),
        claimAssisted: (ref: string) => post<{ session_ref: string; assisted_state: string }>(`/v2/verification-sessions/${seg(ref)}/assisted:claim`),
        completeAssisted: (ref: string, body: { done: boolean; outcome_ref?: string | null; reason_code?: string | null }) =>
          post<{ session_ref: string; assisted_state: string }>(`/v2/verification-sessions/${seg(ref)}/assisted:complete`, body),
        reviews: (state?: M.ReviewState) => get<M.Items<M.DecisionReview>>("/v2/privacy/reviews", { state }),
        myReviews: () => get<M.Items<M.MyReview>>("/v2/privacy/reviews/mine"),
        models: () => get<M.Items<M.ModelVersion>>("/v2/privacy/models"),
        oversight: () => get<M.BiometricOversight>("/v2/privacy/biometric-oversight"),
        transfers: () => get<M.Items<M.TransferLedgerEntry>>("/v2/privacy/transfer-decisions"),
        checkTransfer: (body: M.TransferIn) => post<Record<string, unknown>>("/v2/privacy/transfer-decisions", body),
        changes: (pendingOnly = false) => get<M.Items<M.SubprocessorChange>>("/v2/privacy/subprocessor-changes", { pending_only: pendingOnly ? "true" : undefined }),
        notifyChange: (body: { processor_id: string; kind: M.SubprocessorChangeKind; detail: string; notified_at: string }) =>
          post<Record<string, unknown>>("/v2/privacy/subprocessor-changes", body),
        decideChange: (id: string, approve: boolean) => post<Record<string, unknown>>(`/v2/privacy/subprocessor-changes/${seg(id)}:decide`, { approve }),
        registerModel: (body: { model_ref: string; version: string; kind: M.ModelKind; provider_service_ref?: string | null; live_features?: string[] }) =>
          post<Record<string, unknown>>("/v2/privacy/models", body),
        recordEvaluation: (body: M.ModelEvaluationIn) => post<Record<string, unknown>>("/v2/privacy/model-evaluations", body),
        promoteModel: (ref: string, version: string) => post<Record<string, unknown>>(`/v2/privacy/models/${seg(ref)}/versions/${seg(version)}:promote`),
        decideReview: (id: string, body: { outcome: "UPHELD" | "OVERTURNED" | "CORRECTED"; rationale: string; correction_ref?: string | null }) =>
          post<M.DecisionReview>(`/v2/privacy/reviews/${seg(id)}/decisions`, body),
        /** CR-109: a third person confirms a proposed reversal, or sends it back. */
        checkReview: (id: string, body: { confirm: boolean; note: string }) => post<M.DecisionReview>(`/v2/privacy/reviews/${seg(id)}/checks`, body),
        erasureJobs: () => get<M.Items<M.ErasureJob>>("/v2/privacy/erasure-jobs"),
        erasureJob: (id: string) => get<M.ErasureJob>(`/v2/privacy/erasure-jobs/${seg(id)}`),
        /** B05 dry run: what an erasure would dispose, hold or send to providers, in deletion order. Changes nothing. */
        previewErasure: (subjectRef: string, scope: M.ErasureScope) => post<M.ErasurePreview>("/v2/privacy/erasure-jobs:preview", { subject_ref: subjectRef, scope }),
        openErasureJob: (body: { subject_ref: string; scope: M.ErasureScope; reason: string; legal_basis: string }) => post<M.ErasureJob>("/v2/privacy/erasure-jobs", body),
        attestBackup: (jobId: string, taskId: string, statement: string) =>
          post<M.ErasureJob>(`/v2/privacy/erasure-jobs/${seg(jobId)}/tasks/${seg(taskId)}:attest`, { statement }),
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

    /** ZID-DTV-WF-05 verification checks (CR-112 to CR-116). Nothing is available until the owners approve a method. */
    checks: {
      eligibility: (country: string, purpose: string) => get<C.CheckEligibility>("/v1/check-methods/eligibility", { country, purpose }),
      cases: () => get<{ items: C.CheckCase[] }>("/v1/verification-cases"),
      open: (body: C.CheckCaseIn, idempotencyKey: string) => post<C.CheckCase>("/v1/verification-cases", body, { idempotencyKey }),
      requirements: (id: string) => get<C.CheckRequirements>(`/v1/verification-cases/${seg(id)}/requirements`),
      evidence: (id: string) => get<C.CheckEvidence>(`/v1/verification-cases/${seg(id)}/evidence`),
      close: (id: string) => post<C.CheckCase>(`/v1/verification-cases/${seg(id)}:close`),
      addContact: (id: string, body: { type: "EMAIL" | "PHONE"; value: string; region?: string }) =>
        post<C.CheckContact>(`/v1/verification-cases/${seg(id)}/contacts`, body),
      challenge: (contactId: string, body: { channel: "EMAIL" | "SMS" | "VOICE"; session_ref: string }, idempotencyKey: string) =>
        post<C.CheckChallenge>(`/v1/contacts/${seg(contactId)}/challenges`, body, { idempotencyKey }),
      /** Send the same challenge again, within the sends the platform allows (sends_left says how many remain). */
      resendChallenge: (contactId: string, challengeId: string, sessionRef: string) =>
        post<C.CheckChallenge>(`/v1/contacts/${seg(contactId)}/challenges/${seg(challengeId)}:resend`, { session_ref: sessionRef }),
      confirm: (contactId: string, body: { challenge_id: string; code: string; session_ref: string }) =>
        post<C.CheckChallenge>(`/v1/contacts/${seg(contactId)}/confirm`, body),
      setAddress: (id: string, body: C.CheckAddressIn) =>
        t.request<C.CheckAddressResult>("PUT", `/v1/verification-cases/${seg(id)}/address`, { body }),
      documents: (id: string) => get<{ items: C.CheckDocument[] }>(`/v1/verification-cases/${seg(id)}/address-documents`),
      addDocument: (id: string, body: C.CheckDocumentIn) => post<C.CheckDocument>(`/v1/verification-cases/${seg(id)}/address-documents`, body),
      review: (id: string, doc: string, body: { finding: "CONFIRMED" | "REQUIRES_MORE_EVIDENCE" | "NOT_SUPPORTED"; reason_codes: string[] }) =>
        post<{ status: string; recorded: Record<string, string> }>(`/v1/verification-cases/${seg(id)}/address-documents/${seg(doc)}:review`, body),
      check: (id: string, doc: string, body: { agrees: boolean; reason_codes: string[] }) =>
        post<{ status: string; recorded: Record<string, string> }>(`/v1/verification-cases/${seg(id)}/address-documents/${seg(doc)}:check`, body),
      policies: () => get<{ items: C.CheckPolicy[] }>("/v1/check-policies"),
      draftPolicy: (body: C.CheckPolicyIn) => post<C.CheckPolicy>("/v1/check-policies", body),
      submitPolicy: (id: string) => post<C.CheckPolicy>(`/v1/check-policies/${seg(id)}:submit`),
      decidePolicy: (id: string, approve: boolean, reason: string) =>
        post<C.CheckPolicy>(`/v1/check-policies/${seg(id)}:${approve ? "approve" : "reject"}`, { reason }),
      postal: (id: string) => post<C.CheckPostal>(`/v1/verification-cases/${seg(id)}/postal-challenges`),
      confirmPostal: (id: string, challengeId: string, code: string) =>
        post<C.CheckPostal>(`/v1/verification-cases/${seg(id)}/postal-challenges/${seg(challengeId)}:confirm`, { code }),
    },

    contacts: {
      list: () => get<M.Items<M.EmailContact>>("/v1/tenant/contacts"),
      add: (body: { purpose: M.EmailContactPurpose; address: string }) =>
        t.request<M.EmailContact>("PUT", "/v1/tenant/contacts", { body }),
      remove: (id: string) => t.request<void>("DELETE", `/v1/tenant/contacts/${seg(id)}`),
      /** Ask to change where the signed-in person's own security notices go; the new address must confirm it (CR-114). */
      setMine: (address: string) =>
        t.request<{ change_id: string; status: "PENDING_CONFIRMATION"; address: string; expires_at: string }>("PUT", "/v1/account/contact", { body: { address } }),
      /** The single-use link from the confirmation email. */
      confirmMine: (token: string) => t.request<{ status: "APPLIED"; address: string }>("POST", "/v1/account/contact:confirm", { body: { token } }),
    },

    /** The Command Center (ZID-CPS-WF-02): aggregates under the analytics bundles; cost and price under billing access. */
    verifyAnalytics: {
      access: () => get<VA.Access>("/v1/verify-analytics/access/me"),
      overview: (w: VA.Window) => get<VA.Overview>("/v1/verify-analytics/overview", w),
      query: (body: VA.QueryIn) => post<VA.QueryResult>("/v1/verify-analytics/query", body),
      snapshot: (id: string) => get<VA.QueryResult>(`/v1/verify-analytics/snapshots/${seg(id)}`),
      definitions: () => get<{ metrics: VA.Definition[] }>("/v1/verify-analytics/definitions"),
      quality: () => get<{ quality: VA.Quality; watermark_utc: string | null; sources: VA.Freshness[]; stale_after_minutes: number }>("/v1/verify-analytics/quality"),
      operations: (days = 7) => get<VA.Operations>("/v1/verify-analytics/operations", { days }),
      economics: (w: VA.Window) => get<VA.Economics>("/v1/verify-analytics/economics", w),
      coverage: () => get<VA.Coverage>("/v1/verify-analytics/coverage"),
      timeline: (ref: string) => get<VA.Timeline>(`/v1/verify-analytics/sessions/${seg(ref)}/timeline`),
      corrections: () => get<M.Items<VA.Correction>>("/v1/verify-analytics/corrections"),
      requestCorrection: (body: { event_id: string; reason: string }) => post<{ correction_id: string }>("/v1/verify-analytics/corrections", body),
      approveCorrection: (id: string) => post<{ approved: boolean }>(`/v1/verify-analytics/corrections/${seg(id)}:approve`),
      exports: () => get<M.Items<VA.ExportJob>>("/v1/verify-analytics/exports"),
      createExport: (body: { snapshot_id: string; format: "csv" | "json"; purpose: string }) => post<VA.ExportCreated>("/v1/verify-analytics/exports", body),
      revokeExport: (id: string) => post<{ status: string }>(`/v1/verify-analytics/exports/${seg(id)}:revoke`),
      grants: () => get<M.Items<VA.Grant>>("/v1/verify-analytics/grants"),
      give: (body: { grantee_principal_id: string; bundle: string; expires_at?: string }) => post<{ grant_id: string }>("/v1/verify-analytics/grants", body),
      takeAway: (id: string) => t.request<void>("DELETE", `/v1/verify-analytics/grants/${seg(id)}`),
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
      /** The offer catalogue for a market, evaluated on the server from the active market matrix. A card never carries a price. */
      offers: (q: M.OffersQuery = {}) => get<M.OffersView>("/v1/commercial/offers", { market: q.market, payer_country: q.payer_country, operating_region: q.operating_region }),
      /** Which services need which (a signature needs a verified identity). Public. */
      capabilityDependencies: () => get<M.CapabilityDependencies>("/v1/commercial/capability-dependencies"),
      /** The configuration as the deal desk would receive it: canonical scope and hash, no price, nothing saved. */
      previewQuote: (body: M.SolutionIn) => post<M.QuotePreview>("/v1/commercial/quotes/preview", body),
      /** Save a configuration as a draft. A draft is not an entitlement and expires after 30 days. */
      saveConfiguration: (body: M.SolutionIn) => post<M.SolutionDraft>("/v1/commercial/configurations", body),
      configurations: () => get<M.Items<M.SolutionDraft>>("/v1/commercial/configurations"),
      /** Send a saved draft to the deal desk. Commits the company to nothing. */
      requestQuote: (draftId: string) => post<M.SolutionDraft>(`/v1/commercial/configurations/${seg(draftId)}:request-quote`),
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
      /** Orders that wait for the first month's platform fee before the plan switches (CR-062). */
      activations: () => get<M.Items<M.ActivationWait>>("/v1/billing/activations"),
      /** What buying this plan costs today (CR-147). Reads only. */
      planOffer: (plan: string) => get<M.PlanOffer>(`/v1/billing/plan-offers/${seg(plan)}`),
      /** Raise the order for this plan and get Stripe's page for its exact amount. The plan changes only after Stripe confirms the payment. */
      purchasePlan: (plan: string, body: M.PlanPurchaseIn) => post<M.PlanPurchased>(`/v1/billing/plans/${seg(plan)}:purchase`, body),
      /** Stripe's page for that amount: the address to send the buyer to. */
      payActivation: (orderId: string) => post<{ url: string }>(`/v1/billing/activations/${seg(orderId)}/pay`),
      /** Read the payment back from Stripe (the returned session, or the one this order last opened) and, only if it is paid in full, switch the plan. */
      confirmActivation: (orderId: string, sessionId?: string) => post<M.ActivationConfirm>(`/v1/billing/activations/${seg(orderId)}:confirm`, sessionId ? { session_id: sessionId } : {}),
      /** Pay one invoice on Stripe's page (the way through when the bank asks for confirmation): the address to send the buyer to. */
      payOnline: (invoiceId: string) => post<{ url: string }>(`/v1/invoices/${seg(invoiceId)}/pay-online`),
      /** After Stripe sends the buyer back from paying: the cell verifies the session and records the payment once. */
      completeOnlinePayment: (sessionId: string) =>
        post<{ recorded: boolean; already_recorded: boolean }>("/v1/billing/payment/online-complete", { session_id: sessionId }),
      completePaymentSetup: (sessionId: string) => post<{ method: M.PaymentMethod }>("/v1/billing/payment/complete", { session_id: sessionId }),
      withdrawPlanRequest: (id: string) => post<{ request_id: string; status: M.PlanRequestStatus }>(`/v1/billing/plan-requests/${seg(id)}:withdraw`),
      /** B01: the state, the one next action, the payer and recent activity. The server decides all of it. */
      overview: () => get<M.BillingOverview>("/v1/billing/overview"),
      /** B04: the payer's address, tax identifiers and billing contacts. The legal payer and tax country are never edited here. */
      profile: () => get<M.BillingProfile>("/v1/billing/profile"),
      saveProfile: (body: M.BillingProfileIn) => t.request<M.BillingProfile>("PUT", "/v1/billing/profile", { body }),
      profileHistory: () => get<M.Items<M.BillingProfileVersion>>("/v1/billing/profile/history"),
      /** B05: customer approval of a new or increased commitment (ZID-COM-CL-004 section 04). The server derives the amount from the order. */
      commitments: () => get<M.Items<M.CommitmentRequest>>("/v1/billing/commitment-requests"),
      commitment: (id: string) => get<M.CommitmentRequest>(`/v1/billing/commitment-requests/${seg(id)}`),
      requestCommitment: (orderId: string) => post<M.CommitmentRequest>("/v1/billing/commitment-requests", { order_id: orderId }),
      /** The decision carries the hash the person read and, when the server asks for one, a step-up proof made for exactly this decision. */
      decideCommitment: (id: string, decision: "approve" | "reject", snapshotHash: string, reason?: string, proof?: string) =>
        post<M.CommitmentRequest>(`/v1/billing/commitment-requests/${seg(id)}:${decision}`, { snapshot_hash: snapshotHash, reason: reason?.trim() || null }, withProof(proof)),
      cancelCommitment: (id: string) => post<M.CommitmentRequest>(`/v1/billing/commitment-requests/${seg(id)}:cancel`),
      delegates: () => get<M.Items<M.CommitmentDelegate>>("/v1/billing/commitment-delegates"),
      giveDelegate: (body: { principal_id: string; kind: M.CommitmentDelegateKind; cap_usd: string; effective_to?: string | null }, proof?: string) =>
        post<{ delegate_id: string }>("/v1/billing/commitment-delegates", body, withProof(proof)),
      revokeDelegate: (id: string, proof?: string) =>
        post<{ delegate_id: string; revoked: boolean }>(`/v1/billing/commitment-delegates/${seg(id)}:revoke`, undefined, withProof(proof)),
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

    /** Verification workflows (DTV-30, CR-099): a workflow is data; a different person approves it; the runs only follow it. */
    workflows: {
      list: (status?: string) => get<{ items: M.WfVersionSummary[]; notice: string }>("/v2/workflows", { status }),
      version: (id: string, version: string) => get<M.WfVersion>(`/v2/workflows/${seg(id)}/versions/${seg(version)}`),
      check: (definition: Record<string, unknown>) => post<M.WfCheckResult>("/v2/workflows:check", { definition }),
      create: (definition: Record<string, unknown>) => post<M.WfVersion>("/v2/workflows", { definition }),
      simulate: (id: string, version: string, results: Record<string, string>) =>
        post<M.WfSimulation>(`/v2/workflows/${seg(id)}/versions/${seg(version)}:simulate`, { results }),
      approve: (id: string, version: string) => post<M.WfVersion>(`/v2/workflows/${seg(id)}/versions/${seg(version)}:approve`),
      retire: (id: string, version: string) => post<M.WfVersion>(`/v2/workflows/${seg(id)}/versions/${seg(version)}:retire`),
      runs: (status?: string) => get<{ items: M.WfRunSummary[]; notice: string }>("/v2/workflow-runs", { status }),
      run: (id: string) => get<M.WfRun>(`/v2/workflow-runs/${seg(id)}`),
      reportCheck: (id: string, check: string, outcome: string) => post<M.WfRun>(`/v2/workflow-runs/${seg(id)}:report-check`, { check, outcome }),
      completeReview: (id: string) => post<M.WfRun>(`/v2/workflow-runs/${seg(id)}:complete-review`),
    },

    /** Ownership chains (DTV-24, CR-102): candidates and gaps for a person to confirm; never a named owner. */
    ownership: {
      list: () => get<{ items: M.OwnGraphSummary[] }>("/v2/ownership-graphs"),
      graph: (id: string) => get<M.OwnGraph>(`/v2/ownership-graphs/${seg(id)}`),
      analyze: (body: M.OwnGraphIn) => post<M.OwnFindings>("/v2/ownership-graphs:analyze", body),
      record: (body: M.OwnGraphIn) => post<M.OwnGraph>("/v2/ownership-graphs", body),
      decide: (id: string, body: M.OwnDecisionIn) => post<M.OwnDecision>(`/v2/ownership-graphs/${seg(id)}/confirmations`, body),
    },

    /** Identity proofing cases: the decision, its reasons and the six signal states (WF03, CR-110). Read, and a reviewer's signature. */
    idv: {
      cases: () => get<{ items: M.IdvCase[] }>("/v1/idv/cases"),
      case: (id: string) => get<M.IdvCaseDetail>(`/v1/idv/cases/${seg(id)}`),
      review: (id: string) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/reviews`),
      open: (market: string, route = "CHIP") => post<M.IdvCase>("/v1/idv/cases", { market, route }),
      nonce: (id: string) => post<{ case_id: string; nonce: string; issued_at: string }>(`/v1/idv/cases/${seg(id)}/nonce`),
      /** The platform measures the photograph, reads its printed lines and checks the envelope itself; nothing here states a result. */
      documentImage: (id: string, body: { nonce: string; image_base64: string; captured_at: string; declared?: { surname: string; given_names: string; document_number: string; birth_date: string }; selfie_base64?: string; consent?: boolean; details?: { name?: string; number?: string; birth_date?: string } }) =>
        post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/document-image`, { ...body, virtual_camera: null, channel: "WEB" }),
      consent: (id: string, noticeVersion: string, consented: boolean) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/consent`, { notice_version: noticeVersion, consented }),
      withdraw: (id: string) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/consent:withdraw`),
      abandon: (id: string) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}:abandon`),
      /** A reviewer's side of a case that people decide (CR-124): the photograph is opened only with a reason, for minutes, and never appears in a list. */
      reviewView: (id: string) => get<M.IdvReview>(`/v1/idv/cases/${seg(id)}/review`),
      reviewQueue: () => get<M.IdvQueue>("/v1/idv/review-queue"),
      attestDate: (id: string, expired: boolean) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/attest-date`, { expired }),
      mediaAccess: (id: string, reason: string) => post<M.IdvMediaGrant>(`/v1/idv/cases/${seg(id)}/media:access`, { reason }),
      mediaRead: (id: string, grantId: string) => get<M.IdvMedia>(`/v1/idv/cases/${seg(id)}/media/${seg(grantId)}`),
      reject: (id: string, code: string) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/reject`, { code }),
      recapture: (id: string, code: string) => post<M.IdvCase>(`/v1/idv/cases/${seg(id)}/recapture`, { code }),
    },

    /** Screening for a tenant (WF06 S01 to S03, CR-113): eligibility, a case, its minimal status and a request for another look. The identity a screening rests on is the platform's own, never the caller's. */
    screening: {
      eligibility: (q: { market: string; purpose: string; subject_type: string; subject_ref: string; classes?: string }) =>
        get<M.ScreeningEligibility>("/v1/screening/eligibility", q),
      openCase: (body: M.ScreeningCaseIn, idempotencyKey: string) => post<M.ScreeningCaseView>("/v1/screening/cases", body, { idempotencyKey }),
      getCase: (id: string) => get<M.ScreeningCaseView>(`/v1/screening/cases/${seg(id)}`),
      /** What more is wanted for a case, in words that never say why, and the customer's answer (WF06 EDD, CR-122). */
      information: (id: string) => get<M.ScreeningInformation>(`/v1/screening/cases/${seg(id)}/information`),
      replyWithInformation: (id: string, provided: string[]) => post<{ request_id: string; request: M.ScreeningInformationRequest }>(`/v1/screening/cases/${seg(id)}/information`, { provided }),
      ownershipRollup: (owners: M.ScreeningOwnerIn[]) => post<M.ScreeningOwnershipRollup>("/v1/screening/ownership/rollup", { owners }),
      requestReview: (id: string, note: string) => post<{ request_id: string; status: string }>(`/v1/screening/cases/${seg(id)}:request-review`, { note }),
    },

    /** A company's verification grade, its representatives' mandates, appeals and the signed export (WF02, CR-109). The grade is worked out again on each read. */
    kyb: {
      assertion: () => get<M.KybAssertion>("/v1/kyb/assertions"),
      history: () => get<{ items: M.KybHistoryItem[] }>("/v1/kyb/assertions/history"),
      assess: () => post<M.KybAssertion>("/v1/kyb/assertions:assess"),
      entitlement: (action: string) => get<M.KybEntitlement>("/v1/kyb/entitlements", { action }),
      mandates: () => get<{ items: M.KybMandate[] }>("/v1/kyb/mandates"),
      recordMandate: (body: M.KybMandateIn) => post<{ mandate_id: string; grade: M.KybGrade }>("/v1/kyb/mandates", body),
      cosign: (id: string) => post<{ mandate_id: string; grade: M.KybGrade }>(`/v1/kyb/mandates/${seg(id)}:cosign`),
      revoke: (id: string, reason: string) => post<{ mandate_id: string; grade: M.KybGrade }>(`/v1/kyb/mandates/${seg(id)}:revoke`, { reason }),
      appeals: () => get<{ items: M.KybAppeal[] }>("/v1/kyb/appeals"),
      openAppeal: (decisionVersion: number, reason: string) => post<{ appeal_id: string; state: string; message: string }>("/v1/kyb/appeals", { decision_version: decisionVersion, reason }),
      decideAppeal: (id: string, outcome: "UPHELD" | "ALLOWED", reason: string) => post<{ appeal_id: string; state: string; message: string; grade?: M.KybGrade }>(`/v1/kyb/appeals/${seg(id)}:decide`, { outcome, reason }),
      exportEvidence: (purpose: string) => post<M.KybExport>("/v1/kyb/evidence-exports", { purpose }),
    },

    /** The governed country-signing-CA trust store (WF01 screen O2, CR-107): importing is not trusting; a second person approves and activates. */
    trustStore: {
      packages: () => get<{ items: M.TrustPackage[] }>("/v2/trust-store/csca/packages"),
      active: () => get<M.TrustActive>("/v2/trust-store/csca/active"),
      stage: (body: M.TrustStageIn) => post<M.TrustPackage>("/v2/trust-store/csca/packages", body),
      validate: (id: string) => post<M.TrustPackage>(`/v2/trust-store/csca/packages/${seg(id)}:validate`),
      approve: (id: string) => post<M.TrustPackage>(`/v2/trust-store/csca/packages/${seg(id)}:approve`),
      reject: (id: string, reason: string) => post<M.TrustPackage>(`/v2/trust-store/csca/packages/${seg(id)}:reject`, { reason }),
      activate: (id: string) => post<M.TrustPackage>(`/v2/trust-store/csca/packages/${seg(id)}:activate`),
      rollback: (reason: string, toPackageId?: string) => post<M.TrustPackage>("/v2/trust-store/csca/rollback", toPackageId ? { reason, to_package_id: toPackageId } : { reason }),
      retire: (reason: string) => post<M.TrustPackage>("/v2/trust-store/csca/retire", { reason }),
    },

    /** Source of funds and wealth (DTV-28, CR-096). Every route that starts or changes a case answers "service unavailable" while the product line is not on. */
    sourceOfFunds: {
      queue: (state?: string) => get<{ items: M.SofQueueItem[]; notice: string }>("/v2/source-of-funds/cases", { state }),
      case: (id: string) => get<M.SofCase>(`/v2/source-of-funds/cases/${seg(id)}`),
      questionnaire: (country: string) => get<M.SofQuestionnaire>("/v2/source-of-funds/questionnaire", { country }),
      decide: (id: string, body: M.SofDecideIn) => post<M.SofCase>(`/v2/source-of-funds/cases/${seg(id)}:decide`, body),
      appeal: (id: string) => post<M.SofCase>(`/v2/source-of-funds/cases/${seg(id)}:appeal`),
    },

    /** Delegated Authority & Access Readiness agent (CR-091). Every route answers 404 while the feature is off; the tenant and region are never sent. */
    accessReadiness: {
      setups: () => get<M.Items<M.ReadinessSetup>>("/v1/access-readiness/agents"),
      setup: (agentId: string) => get<M.ReadinessSetup>(`/v1/access-readiness/agents/${seg(agentId)}`),
      createSetup: (body: M.ReadinessCreateIn) => post<M.ReadinessSetup>("/v1/access-readiness/agents", body),
      changeSetup: (agentId: string, body: M.ReadinessPatchIn) => t.request<M.ReadinessSetup>("PATCH", `/v1/access-readiness/agents/${seg(agentId)}`, { body }),
      dryRun: (agentId: string, body: { fixture?: string; policy_version?: string } = {}) =>
        post<M.ReadinessDryRun>(`/v1/access-readiness/agents/${seg(agentId)}/dry-runs`, body),
      requestPublish: (agentId: string) => post<M.ReadinessSetup>(`/v1/access-readiness/agents/${seg(agentId)}/publish-requests`),
      approvePublish: (agentId: string) => post<M.ReadinessSetup>(`/v1/access-readiness/agents/${seg(agentId)}/publish-requests:approve`),
      pause: (agentId: string, reason?: string) => post<M.ReadinessSetup>(`/v1/access-readiness/agents/${seg(agentId)}:pause`, reason ? { reason } : {}),
      resume: (agentId: string) => post<M.ReadinessSetup>(`/v1/access-readiness/agents/${seg(agentId)}:resume`),
      startRun: (agentId: string, idempotencyKey: string) =>
        post<M.ReadinessRun>(
          `/v1/access-readiness/agents/${seg(agentId)}/runs`,
          { purpose: "access_posture_review", resource_selector: { kind: "mandates" }, mode: "manual" },
          { idempotencyKey },
        ),
      runs: (agentId: string) => get<M.Items<M.ReadinessRun>>(`/v1/access-readiness/agents/${seg(agentId)}/runs`),
      run: (agentId: string, runId: string) => get<M.ReadinessRunDetail>(`/v1/access-readiness/agents/${seg(agentId)}/runs/${seg(runId)}`),
      decisions: (agentId: string, runId: string) => get<M.Items<M.ReadinessDecision>>(`/v1/access-readiness/agents/${seg(agentId)}/runs/${seg(runId)}/decisions`),
      evidence: (agentId: string, runId: string) => get<M.ReadinessEvidence>(`/v1/access-readiness/agents/${seg(agentId)}/runs/${seg(runId)}/evidence`),
      /** Permitted evidence of one run, references and digests only. A stated purpose is required; the export is recorded. */
      exportEvidence: (agentId: string, runId: string, purpose: string) =>
        post<M.ReadinessEvidenceExport>(`/v1/access-readiness/agents/${seg(agentId)}/runs/${seg(runId)}/evidence:export`, { purpose }),
      retryRun: (agentId: string, runId: string) => post<M.ReadinessRun>(`/v1/access-readiness/agents/${seg(agentId)}/runs/${seg(runId)}:retry`),
      proposals: (status?: M.ReadinessProposalStatus) => get<M.Items<M.ReadinessProposal> & { notice: string }>("/v1/access-readiness/review-proposals", { status }),
      proposal: (id: string) => get<M.ReadinessProposal>(`/v1/access-readiness/review-proposals/${seg(id)}`),
      dismissProposal: (id: string) => post<M.ReadinessProposal>(`/v1/access-readiness/review-proposals/${seg(id)}:dismiss`),
      assignProposal: (id: string, assignee: string) => post<M.ReadinessProposal>(`/v1/access-readiness/review-proposals/${seg(id)}:assign`, { assignee }),
      /** Records a signed request for the governed access workflow. Executes nothing. */
      handoffProposal: (id: string) => post<M.ReadinessProposal>(`/v1/access-readiness/review-proposals/${seg(id)}:handoff`),
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
      builds: (status?: M.BuildReview) => get<M.BuildList>("/v1/machine/builds", { status }),
      resolveBuild: (image: string) => post<M.BuildLookup>("/v1/machine/builds:resolve", { image }),
      buildVersions: (image: string) => post<M.BuildVersions>("/v1/machine/builds:versions", { image }),
      registerBuild: (body: M.BuildCreate) => post<M.BuildItem>("/v1/machine/builds", body),
      registerApiClient: (body: M.ApiClientCreate) => post<M.ApiClientRegistered>("/v1/machine/api-clients", body),
      apiBinding: (id: string) => get<M.ApiBinding>(`/v1/machine/agents/${seg(id)}/api-binding`),
      bindApiClient: (id: string) => post<M.ApiBinding>(`/v1/machine/agents/${seg(id)}/api-binding`),
      revokeApiBinding: (id: string, reason_code: string) => post<M.ApiBinding>(`/v1/machine/agents/${seg(id)}/api-binding:revoke`, { reason_code }),
      approveBuild: (id: string) => post<M.BuildItem>(`/v1/machine/builds/${seg(id)}:approve`),
      revokeBuild: (id: string, reason_code: string) => post<M.BuildRevoked>(`/v1/machine/builds/${seg(id)}:revoke`, { reason_code }),
      tools: () => get<M.Items<M.ToolItem>>("/v1/machine/tools"),
      registerTool: (body: M.ToolCreate) => post<M.ToolItem>("/v1/machine/tools", body),
      activateTool: (id: string) => post<M.ToolItem>(`/v1/machine/tools/${seg(id)}:activate`),
      suspendTool: (id: string, reason_code: string) => post<M.ToolItem>(`/v1/machine/tools/${seg(id)}:suspend`, { reason_code }),
      retireTool: (id: string) => post<M.ToolItem>(`/v1/machine/tools/${seg(id)}:retire`),
    },

    /** Agent governance (ZID-AG-WF-002, CR-105): permissions, spending, approvals, monitoring and the emergency stop. */
    agentGovernance: {
      overview: () => get<M.GovOverview>("/v1/machine/governance/overview"),
      baseline: () => get<{ configured: boolean; revision?: number } & Partial<M.GovBaseline>>("/v1/machine/governance/baseline"),
      /** Set the company baseline (needs agent.governance.manage). A company can lower the platform limits, never raise them. */
      setBaseline: (body: {
        allowed_tools: string[];
        allowed_regions: string[];
        ai_providers: string[];
        quotas: Record<string, number | boolean> | null;
        schedules_allowed: boolean;
      }) => t.request<{ revision: number } & M.GovBaseline>("PUT", "/v1/machine/governance/baseline", { body }),
      requests: (query: { agent_id?: string; state?: M.GovRequestState } = {}) => get<M.Items<M.GovRequest>>("/v1/machine/governance/requests", query),
      decide: (id: string, decision: "approve" | "reject" | "clarify", body: { policy_hash: string; reason?: string }) =>
        post<{ state: string; open_slots?: string[] }>(`/v1/machine/governance/requests/${seg(id)}:${decision}`, body),
      apply: (id: string) => post<{ policy_hash: string; version: number }>(`/v1/machine/governance/requests/${seg(id)}:apply`),
      effectiveAccess: (agentId: string) => get<M.GovEffectiveAccess>(`/v1/machine/agents/${seg(agentId)}/effective-access`),
      policyVersions: (agentId: string) => get<M.Items<M.GovPolicy>>(`/v1/machine/agents/${seg(agentId)}/policy`),
      draftPolicy: (agentId: string, body: M.GovDraftInput) => post<M.GovPolicy>(`/v1/machine/agents/${seg(agentId)}/policy/drafts`, body),
      simulate: (
        agentId: string,
        body: { tool_id: string; resource_kind: string; resource_id: string; purpose?: string; region?: string; action?: string },
      ) => post<M.GovSimulation>(`/v1/machine/agents/${seg(agentId)}/policy/simulate`, body),
      requestChange: (agentId: string, body: { policy_id: string; reason_code: string }) =>
        post<M.GovRequest>(`/v1/machine/agents/${seg(agentId)}/change-requests`, body),
      budget: (agentId: string) => get<M.GovBudgetView>(`/v1/machine/agents/${seg(agentId)}/budget`),
      setBudget: (body: M.GovBudgetInput) =>
        post<{ state: "ACTIVE" | "PENDING"; budget_id: string; hard_cap: string; approval_request_id?: string; required_slots?: string[] }>(
          "/v1/machine/budgets",
          body,
        ),
      monitoring: (agentId: string) => get<M.GovMonitoring>(`/v1/machine/agents/${seg(agentId)}/monitoring`),
      events: (agentId: string | null, query: { cursor?: number; outcome?: string; limit?: number } = {}) =>
        get<M.GovEvents>(agentId ? `/v1/machine/agents/${seg(agentId)}/events` : "/v1/machine/governance/events", query),
      verifyEvidence: () => get<{ intact: boolean; broken_at: number | null; checked: number }>("/v1/machine/governance/evidence:verify"),
      pauseAgent: (agentId: string, body: { reason: string; severity: "P0" | "P1" | "P2" }) =>
        post<{ case_id: string; generation: number; agents_suspended?: number; already_frozen: boolean }>(`/v1/machine/agents/${seg(agentId)}/pause`, body),
      pauseCompany: (body: { reason: string; severity: "P0" | "P1" | "P2" }) =>
        post<{ case_id: string; generation: number; agents_suspended?: number; already_frozen: boolean }>("/v1/machine/governance/pause", body),
      freezes: () => get<M.Items<M.GovFreeze>>("/v1/machine/governance/freezes"),
      requestRestore: (caseId: string) => post<M.GovRequest>(`/v1/machine/governance/freezes/${seg(caseId)}:request-restore`),
      delegates: () => get<M.GovDelegates>("/v1/machine/governance/delegates"),
      grantSlot: (body: { principal_id: string; slot: M.GovSlot }) =>
        post<{ principal_id: string; slot: M.GovSlot; created: boolean }>("/v1/machine/governance/delegates", body),
      revokeSlot: (body: { principal_id: string; slot: M.GovSlot }) =>
        post<{ principal_id: string; slot: M.GovSlot; revoked: boolean }>("/v1/machine/governance/delegates:revoke", body),
    },

    /** The DAARA reference agent and its human review queue (ZID-AG-WF-001, CR-106). */
    daara: {
      template: () => get<M.DaaraTemplate>("/v1/machine/daara/template"),
      setup: (agentId: string) => get<M.DaaraSetup>(`/v1/machine/agents/${seg(agentId)}/daara`),
      saveSetup: (agentId: string, body: M.DaaraSetupInput) => t.request<M.DaaraSetup>("PUT", `/v1/machine/agents/${seg(agentId)}/daara`, { body }),
      validate: (agentId: string) => post<{ validated: boolean; config_hash: string }>(`/v1/machine/agents/${seg(agentId)}/daara:validate`),
      dryRun: (agentId: string) => post<M.DaaraDryRun>(`/v1/machine/agents/${seg(agentId)}/daara/dry-run`),
      requestPublish: (agentId: string) => post<M.GovRequest>(`/v1/machine/agents/${seg(agentId)}/daara:request-publish`),
      startRun: (agentId: string, idempotencyKey: string, mode: "inline" | "queued" = "inline") =>
        post<M.DaaraRun>(`/v1/machine/agents/${seg(agentId)}/daara/runs`, {}, { idempotencyKey, query: { mode } }),
      runs: (agentId: string) => get<M.Items<M.DaaraRun>>(`/v1/machine/agents/${seg(agentId)}/daara/runs`),
      run: (agentId: string, runId: string) => get<M.DaaraRunDetail>(`/v1/machine/agents/${seg(agentId)}/daara/runs/${seg(runId)}`),
      queue: (status?: M.ReviewProposal["status"]) => get<M.ReviewQueue>("/v1/machine/agent-review", { status }),
      proposal: (id: string) => get<M.ReviewProposal>(`/v1/machine/agent-review/${seg(id)}`),
      decide: (id: string, action: "dismiss" | "assign" | "handoff", body: { reason?: string; assignee?: string }) =>
        post<M.ReviewDecision>(`/v1/machine/agent-review/${seg(id)}:${action}`, body),
      acknowledgeAlert: (key: string, note?: string) => post<{ state: string }>(`/v1/machine/governance/alerts/${seg(key)}:acknowledge`, { note }),
      routeAlert: (key: string, body: { route: "SECURITY" | "AUTHORITY" | "PRIVACY" | "FINANCE" | "SRE"; owner?: string; note?: string }) =>
        post<{ state: string; route: string }>(`/v1/machine/governance/alerts/${seg(key)}:route`, body),
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
    /** Action-bound step-up proofs (ZID-ENG-DEC-009), the same calls the customer console makes: the console proves exactly what the cell asks for. */
    stepUp: {
      challenge: (draft: M.StepUpDraft) =>
        post<M.StepUpChallenge>("/v1/step-up/challenges", { action: draft.action, resource: draft.resource, change: draft.change ?? {}, critical: draft.critical ?? false }),
      verify: (challengeId: string, credential: unknown) => post<M.StepUpProof>(`/v1/step-up/challenges/${seg(challengeId)}/verify`, { credential }),
      cancel: (challengeId: string) => post<{ challenge_id: string; status: string; grants_nothing: boolean }>(`/v1/step-up/challenges/${seg(challengeId)}/cancel`),
    },
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
    /** Fraud and adverse-media signals waiting for a person. A signal is a flag to look at: it never blocks or approves anything. */
    signalReviews: () => get<M.Items<M.SignalReview>>("/v1/reviews/signals"),
    challengeSignal: (id: string, body: M.SignalChallengeIn) => post<M.SignalReview>(`/v1/reviews/signals/${seg(id)}/challenge`, body),
    resolveSignal: (id: string, body: M.SignalResolutionIn) => post<M.SignalReview>(`/v1/reviews/signals/${seg(id)}/resolution`, body),

    /** A reviewer's work on a company case: screening alerts with the listed names, a past decision decided again, and the ruling. Reviewer routes are protected actions: a role flag never grants them. */
    caseReviews: {
      screening: (id: string) => get<M.ScreeningCaseDetail>(`/v1/reviews/screening-cases/${seg(id)}`),
      replay: (id: string, version: number) => get<M.ScreeningReplay>(`/v1/reviews/screening-cases/${seg(id)}/replay`, { version }),
      screeningRuling: (id: string, body: M.ScreeningRulingIn) => post<M.CaseRuling>(`/v1/reviews/screening-cases/${seg(id)}/rulings`, body),
      kybRuling: (id: string, body: M.KybRulingIn) => post<M.CaseRuling>(`/v1/reviews/kyb-cases/${seg(id)}/rulings`, body),
    },

    /** Commercial operator surface (ZID-COM-001). Reads are typed; every write goes through `call`, driven by the
     * action catalogue in the operator console so that each route has exactly one description of its body. */
    commercial: {
      books: () => get<M.Items<M.PriceBookSummary>>("/v1/commercial/price-books"),
      book: (id: string) => get<M.PriceBookDetail>(`/v1/commercial/price-books/${seg(id)}`),
      /** What a price book still lacks before it is approved: unpriced SKU and plan pairs, and lines chosen as free. */
      bookReadiness: (id: string) =>
        get<{ price_book_id: string; lines: number; ready: boolean; missing: { sku: string; plan: string; what: string }[]; free: { sku: string; plan: string }[] }>(
          `/v1/commercial/price-books/${seg(id)}/readiness`,
        ),
      /** First-month plan payments an operator marked to be returned, with the processor reference to refund (CR-062). */
      /** The market matrices, newest first, and one with its rows, signatures and what is still missing. */
      marketMatrices: () => get<M.Items<M.MarketMatrixSummary>>("/v1/commercial/market-matrices"),
      marketMatrix: (id: string) => get<M.MarketMatrixDetail>(`/v1/commercial/market-matrices/${seg(id)}`),
      /** Wording revisions of the sixteen service families beside the reviewed file's wording. Wording only: structure never changes here. */
      familyTexts: (family?: string) => get<M.FamilyTexts>("/v1/commercial/service-family-texts", { family }),
      /** Inquiries from the public pricing page, newest first. A lead is a conversation, never an account or a commitment. */
      salesLeads: (status?: string) => get<M.Items<M.SalesLead>>("/v1/commercial/sales-leads", { status }),
      /** FX rate sets for reporting: a named source, USD per unit, activated by a different person. There are no default rates. */
      fxRateSets: () => get<M.Items<M.FxRateSet>>("/v1/commercial/fx-rate-sets"),
      /** Family rate cards, newest first, and one with its lines and signatures. There are no default rates. */
      rateCards: () => get<M.Items<M.RateCardSummary>>("/v1/commercial/family-rate-cards"),
      rateCard: (id: string) => get<M.RateCardDetail>(`/v1/commercial/family-rate-cards/${seg(id)}`),
      /** Whether the public pricing page's packaging is approved: the file's hash, who has signed it and who is still needed. */
      pricingPage: () => get<M.PricingPageState>("/v1/commercial/pricing-page"),
      /** The privacy notice texts for the inquiry form and the one visitors are shown now. */
      publicNotices: () => get<M.PublicNotices>("/v1/commercial/public-notices"),
      /** Where each market of the active matrix is in its staged rollout. */
      marketRollout: () => get<M.Items<M.MarketRolloutItem>>("/v1/commercial/market-rollout"),
      /** Every company's saved configuration sent for a quote, for the deal desk. */
      quoteRequests: () => get<M.Items<M.QuoteRequestItem>>("/v1/commercial/quote-requests"),
      activationRefunds: () => get<M.Items<M.ActivationRefund>>("/v1/commercial/activation-refunds"),
      /** What was deleted for a closed company, what is held and what is still owed. */
      dataDeletion: (tenantId: string) => get<Record<string, unknown>>(`/v1/commercial/tenants/${seg(tenantId)}/data-deletion`),
      /** Every company's open requests for a different plan, oldest first. */
      planRequests: () => get<M.Items<M.PlanRequest & { tenant_id: string; company: string | null }>>("/v1/commercial/plan-requests"),
      /** ACCEPTED: the team will raise the order. DECLINED: the company is told why. The entitlement is untouched. */
      answerPlanRequest: (id: string, decision: "ACCEPTED" | "DECLINED", note?: string) =>
        post<{ request_id: string; status: M.PlanRequestStatus; tenant_id: string }>(`/v1/commercial/plan-requests/${seg(id)}:answer`, { decision, note: note?.trim() || null }),
      /** Every company's open requests for a contracted service (dedicated isolation, sovereign service, key custody, named support), oldest first. */
      serviceRequests: () => get<M.Items<M.ServiceRequest & { tenant_id: string }>>("/v1/commercial/service-requests"),
      moveServiceRequest: (id: string, status: "IN_PROGRESS" | "ACTIVE" | "DECLINED", note?: string) =>
        post<{ request_id: string; status: string; tenant_id: string }>(`/v1/commercial/service-requests/${seg(id)}:move`, { status, note: note?.trim() || null }),
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

    /** Zoiko's finance authority (ZID-COM-CL-004, ZID-UX-COM-009): appointments, the signed policy, and money requests that need distinct approvers. */
    finance: {
      overview: () => get<M.FinanceOverview>("/v1/commercial/finance/overview"),
      policy: () => get<M.FinancePolicy>("/v1/commercial/finance/policy"),
      signPolicy: (contentHash: string) => post<M.FinancePolicy>("/v1/commercial/finance/policy:sign", { content_hash: contentHash }),
      appointments: () => get<{ items: M.FinanceAppointment[]; roles: string[] }>("/v1/commercial/finance/appointments"),
      appoint: (body: { principal_id: string; role: string; effective_to?: string | null }) => post<M.FinanceAppointment>("/v1/commercial/finance/appointments", body),
      endAppointment: (id: string) => post<{ appointment_id: string; ended: boolean }>(`/v1/commercial/finance/appointments/${seg(id)}:end`),
      requests: (state?: string) => get<M.Items<M.FinanceRequest>>("/v1/commercial/finance/requests", { state }),
      request: (id: string) => get<M.FinanceRequest>(`/v1/commercial/finance/requests/${seg(id)}`),
      open: (body: M.FinanceOpenIn) => post<M.FinanceRequest>("/v1/commercial/finance/requests", body),
      decide: (id: string, body: { role: string; decision: "APPROVE" | "REJECT"; snapshot_hash: string; reason?: string | null }) =>
        post<M.FinanceRequest>(`/v1/commercial/finance/requests/${seg(id)}:decide`, body),
      cancel: (id: string) => post<M.FinanceRequest>(`/v1/commercial/finance/requests/${seg(id)}:cancel`),
      execute: (id: string, idempotencyKey: string) => post<M.FinanceRequest>(`/v1/commercial/finance/requests/${seg(id)}:execute`, { idempotency_key: idempotencyKey }),
      reviewCommitment: (tenantId: string, requestId: string) =>
        post<M.CommitmentRequest>(`/v1/commercial/finance/commitments/${seg(tenantId)}/${seg(requestId)}:review`),
      eligibility: (tenantId: string) => get<M.Eligibility>(`/v1/commercial/finance/eligibility/${seg(tenantId)}`),
    },

    /** Refunds, disputes and reconciliation (ZID-UX-COM-009 F03, F04; CR-058). No request here sends a refund: the worker does. */
    payments: {
      refunds: (state?: string) => get<M.Items<M.RefundCase>>("/v1/commercial/payments/refunds", { state }),
      refund: (id: string) => get<M.RefundCase>(`/v1/commercial/payments/refunds/${seg(id)}`),
      openRefund: (body: M.RefundOpenIn) => post<M.RefundCase>("/v1/commercial/payments/refunds", body),
      decideRefund: (id: string, body: { role: string; decision: "APPROVE" | "REJECT"; snapshot_hash: string; reason?: string | null }) =>
        post<M.RefundCase>(`/v1/commercial/payments/refunds/${seg(id)}:decide`, body),
      executeRefund: (id: string) => post<M.RefundCase>(`/v1/commercial/payments/refunds/${seg(id)}:execute`),
      cancelRefund: (id: string) => post<M.RefundCase>(`/v1/commercial/payments/refunds/${seg(id)}:cancel`),
      disputes: (openOnly?: boolean) => get<M.Items<M.DisputeCase>>("/v1/commercial/payments/disputes", { open_only: openOnly ? "true" : undefined }),
      dispute: (id: string) => get<M.DisputeCase>(`/v1/commercial/payments/disputes/${seg(id)}`),
      assignDispute: (id: string, owner: string) => post<M.DisputeCase>(`/v1/commercial/payments/disputes/${seg(id)}:assign`, { owner }),
      addEvidence: (id: string, body: { item: M.DisputeEvidenceItem; ref: string }) => post<M.DisputeCase>(`/v1/commercial/payments/disputes/${seg(id)}:evidence`, body),
      decideDispute: (id: string, body: { strategy: "ACCEPT" | "CONTEST"; reason: string }) => post<M.DisputeCase>(`/v1/commercial/payments/disputes/${seg(id)}:decide`, body),
      recordSubmission: (id: string, reference: string) => post<M.DisputeCase>(`/v1/commercial/payments/disputes/${seg(id)}:record-submission`, { reference }),
      verifySubmission: (id: string) => post<M.DisputeCase>(`/v1/commercial/payments/disputes/${seg(id)}:verify-submission`),
      board: (entity: string, period: string) => get<M.ReconBoard>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}`),
      importLegs: (entity: string, legs: Record<string, unknown>[]) => post<{ added: number; skipped: number }>(`/v1/commercial/payments/reconciliation/${seg(entity)}:import-legs`, { legs }),
      importBankStatement: (entity: string, content: string) =>
        post<{ added: number; skipped: number; unreadable_entries: number }>(`/v1/commercial/payments/reconciliation/${seg(entity)}:import-bank-statement`, { format: "camt053", content }),
      importStripePayouts: (entity: string, payoutIds: string[]) =>
        post<{ added: number; skipped: number; unreadable_transactions: number }>(`/v1/commercial/payments/reconciliation/${seg(entity)}:import-stripe-payouts`, { payout_ids: payoutIds }),
      exceptions: (entity: string, period: string) => get<M.Items<M.ReconException>>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}/exceptions`),
      resolveException: (id: string, resolution: string) =>
        post<{ exception_id: string; resolved: boolean }>(`/v1/commercial/payments/reconciliation/exceptions/${seg(id)}:resolve`, { resolution }),
      prepareClose: (entity: string, period: string) => post<M.ReconBoard>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}:prepare-close`),
      signClose: (entity: string, period: string, body: { role: string; snapshot_hash: string }) =>
        post<M.ReconBoard>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}:sign-close`, body),
      requestReopen: (entity: string, period: string, body: { reason: string; impact: string }) =>
        post<{ state: string }>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}:request-reopen`, body),
      approveReopen: (entity: string, period: string, role: string) =>
        post<{ state: string }>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}:approve-reopen`, { role }),
      evidencePack: (entity: string, period: string) => get<Record<string, unknown>>(`/v1/commercial/payments/reconciliation/${seg(entity)}/${seg(period)}/evidence-pack`),
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

    /** ZID-RCG-003 regulatory records (CR-067 to CR-085). Every write is one operator capability; the cell enforces who may do what. */
    regulatory: {
      readiness: () => get<M.RegulatoryReadiness>("/v1/regulatory/readiness"),
      /** The Face lab (CR-128): the lab engine's state and the approval gate's missing records. */
      faceLab: () => get<M.FaceLabStatus>("/v1/regulatory/face-lab"),
      /** Two photographs in, a measurement out. Stores nothing; approves nobody. */
      faceLabCompare: (body: { probe_base64: string; reference_base64: string }) => post<M.FaceLabResult>("/v1/regulatory/face-lab/compare", body),
      /** Which countries are open for sign-up, and the decision recorded for each (CR-127). */
      countryOpenings: () => get<M.CountryOpeningsView>("/v1/regulatory/country-openings"),
      recordCountryOpening: (country: string, body: M.CountryOpeningIn) =>
        t.request<M.CountryOpening>("PUT", `/v1/regulatory/country-openings/${seg(country)}`, { body }),
      offerings: (country?: string) => get<M.Items<M.ServiceOffering>>("/v1/regulatory/offerings", { country }),
      trustLists: () => get<M.TrustListsView>("/v1/regulatory/trust-lists"),
      registerSources: () => get<M.Items<M.RegisterSource>>("/v1/regulatory/register-sources"),
      refreshFromRegister: (body: M.SupplierScope & { source_id: string }) => post<M.RegisterRefresh>("/v1/regulatory/attestations:refresh", body),
      packs: (country?: string, status?: M.PackStatus) => get<M.Items<M.PolicyPack>>("/v1/regulatory/policy-packs", { country, status }),
      createPack: (body: M.PackDraftIn) => post<M.PolicyPack>("/v1/regulatory/policy-packs", body),
      revisePack: (packId: string, body: { sources?: M.PackSource[]; rules?: M.PackRules }) =>
        post<M.PolicyPack>(`/v1/regulatory/policy-packs/${seg(packId)}:revise`, body),
      approvePack: (packId: string, version: number, role: M.PackApproverRole, signedHash: string) =>
        post<M.PolicyPack>(`/v1/regulatory/policy-packs/${seg(packId)}/versions/${version}:approve`, { role, signed_hash: signedHash }),
      publishPack: (packId: string, version: number) => post<M.PolicyPack>(`/v1/regulatory/policy-packs/${seg(packId)}/versions/${version}:publish`),
      retirePack: (packId: string, version: number) => post<M.PolicyPack>(`/v1/regulatory/policy-packs/${seg(packId)}/versions/${version}:retire`),

      releases: (country?: string, status?: M.ReleaseStatus) => get<M.Items<M.MarketRelease>>("/v1/regulatory/market-releases", { country, status }),
      marketStatus: (country: string, product: string, purpose: string) => get<M.MarketStatus>("/v1/regulatory/market-status", { country, product, purpose }),
      openRelease: (body: { release_id: string; country: string; product: string; purpose: string }) => post<M.MarketRelease>("/v1/regulatory/market-releases", body),
      reviseRelease: (releaseId: string) => post<M.MarketRelease>(`/v1/regulatory/market-releases/${seg(releaseId)}:revise`),
      attestGate: (releaseId: string, version: number, body: { gate: string; role: M.GateRole; status: "PASSED" | "FAILED"; evidence_ref: string }) =>
        post<M.MarketRelease>(`/v1/regulatory/market-releases/${seg(releaseId)}/versions/${version}:attest`, body),
      freezeRelease: (releaseId: string, version: number, suppliers: M.SupplierScope[]) =>
        post<M.MarketRelease>(`/v1/regulatory/market-releases/${seg(releaseId)}/versions/${version}:freeze`, { suppliers }),
      approveRelease: (releaseId: string, version: number, role: M.ReleaseApproverRole, signedHash: string) =>
        post<M.MarketRelease>(`/v1/regulatory/market-releases/${seg(releaseId)}/versions/${version}:approve`, { role, signed_hash: signedHash }),
      launchRelease: (releaseId: string, version: number) => post<M.MarketRelease>(`/v1/regulatory/market-releases/${seg(releaseId)}/versions/${version}:launch`),
      withdrawRelease: (releaseId: string, version: number) => post<M.MarketRelease>(`/v1/regulatory/market-releases/${seg(releaseId)}/versions/${version}:withdraw`),

      attestations: (entity?: string) => get<M.Items<M.AttestationRow>>("/v1/regulatory/attestations", { entity }),
      recordAttestation: (body: M.AttestationIn) => post<Record<string, unknown>>("/v1/regulatory/attestations", body),
      entitlements: (provider?: string) => get<M.Items<M.EntitlementRow>>("/v1/regulatory/entitlements", { provider }),
      recordEntitlement: (body: M.EntitlementIn) => post<Record<string, unknown>>("/v1/regulatory/entitlements", body),

      /** Company-verification market policy packs (WF02 K09): versions are identified by their digest, signed in four roles and published or rolled back by different people. */
      kybPolicies: () => get<{ items: M.KybPolicyVersion[] }>("/v1/kyb/market-policies"),
      kybPolicy: (hash: string) => get<M.KybPolicyDetail>(`/v1/kyb/market-policies/${seg(hash)}`),
      kybPolicyPropose: (pack: Record<string, unknown>) => post<M.KybPolicyProposed>("/v1/kyb/market-policies", { pack }),
      kybPolicyApprove: (hash: string, role: string) => post<{ policy_hash: string; role: string; approved: boolean }>(`/v1/kyb/market-policies/${seg(hash)}:approve`, { role }),
      kybPolicyPublish: (hash: string) => post<{ policy_hash: string; in_force: boolean }>(`/v1/kyb/market-policies/${seg(hash)}:publish`),
      kybPolicyActivateNow: (hash: string) =>
        post<{ policy_hash: string; in_force: boolean; without_signoff: boolean; skipped: string[] }>(`/v1/kyb/market-policies/${seg(hash)}:activate-now`, { acknowledge: "activate-without-signoff" }),
      kybPolicyRollback: (marketKey: string, toHash: string, reason: string) =>
        post<{ market_key: string; policy_hash: string; in_force: boolean }>("/v1/kyb/market-policies:rollback", { market_key: marketKey, to_hash: toHash, reason }),

      /** Official sanctions sources (WF06 S10): a corpus is fetched and staged, approved by two others, then published; a rollback needs two approvals of its own. */
      screeningSources: () => get<M.Items<M.ScreeningSource>>("/v1/screening/sources"),
      screeningReleases: (sourceId: string) => get<M.Items<M.ScreeningRelease>>(`/v1/screening/sources/${seg(sourceId)}/releases`),
      screeningComparison: (releaseId: string) => get<M.ScreeningComparison>(`/v1/screening/releases/${seg(releaseId)}/comparison`),
      screeningFetch: (sourceId: string) => post<M.ScreeningRelease>(`/v1/screening/sources/${seg(sourceId)}/releases:fetch`),
      screeningApprove: (releaseId: string, acknowledged: string[]) => post<M.ScreeningRelease>(`/v1/screening/releases/${seg(releaseId)}:approve`, { acknowledged }),
      screeningPublish: (releaseId: string) => post<M.ScreeningRelease>(`/v1/screening/releases/${seg(releaseId)}:publish`),
      screeningApproveRollback: (releaseId: string) => post<M.ScreeningRelease>(`/v1/screening/releases/${seg(releaseId)}:approve-rollback`),
      screeningRollback: (releaseId: string) => post<M.ScreeningRelease>(`/v1/screening/releases/${seg(releaseId)}:rollback`),

      /** Release gates G0 to G3 for a screening module in a market (WF06 section 22): the role someone signs in is the capability they hold. */
      screeningGate: (market: string, module: string) => get<M.ScreeningGateStatus>(`/v1/screening/gates/${seg(market)}/${seg(module)}`),
      screeningGateSign: (market: string, module: string, body: { gate: string; evidence_ref: string; valid_days: number }) =>
        post<M.ScreeningGateAct>(`/v1/screening/gates/${seg(market)}/${seg(module)}:sign`, body),
      screeningGateDefect: (market: string, module: string, ref: string) => post<M.ScreeningGateAct>(`/v1/screening/gates/${seg(market)}/${seg(module)}/defects`, { ref }),
      screeningGateHold: (market: string, module: string, ref: string) => post<M.ScreeningGateAct>(`/v1/screening/gates/${seg(market)}/${seg(module)}/holds`, { ref }),
      screeningGateClose: (recordId: string) => post<M.ScreeningGateAct>(`/v1/screening/gates/records/${seg(recordId)}:close`),
      screeningGateRelease: (recordId: string) => post<M.ScreeningGateAct>(`/v1/screening/gates/records/${seg(recordId)}:release`),
      screeningGateRescind: (recordId: string) => post<M.ScreeningGateAct>(`/v1/screening/gates/records/${seg(recordId)}:rescind`),

      /** The restricted screening case queue (WF06 S04, S05, S09): a decision is proposed by one person and finalised by another. */
      screeningQueue: (state?: string, cursor?: string) => get<{ items: M.ScreeningQueueItem[]; next_cursor: string | null }>("/v1/screening/review/queue", { state, cursor }),
      screeningCase: (requestId: string) => get<M.ScreeningReviewCaseDetail>(`/v1/screening/review/cases/${seg(requestId)}`),
      screeningInformationRequests: (requestId: string) => get<M.Items<M.ScreeningReviewInformation>>(`/v1/screening/review/cases/${seg(requestId)}/information-requests`),
      screeningAskInformation: (requestId: string, items: string[], dueInDays: number) =>
        post<M.ScreeningInformationRequest>(`/v1/screening/review/cases/${seg(requestId)}/information-requests`, { items, due_in_days: dueInDays }),
      screeningReviewInformation: (eddId: string, accepted: boolean, code?: string) =>
        post<M.ScreeningInformationRequest>(`/v1/screening/review/information-requests/${seg(eddId)}:review`, code ? { accepted, code } : { accepted }),
      screeningPropose: (requestId: string, body: M.ScreeningProposeIn) => post<M.ScreeningProposeOut>(`/v1/screening/review/cases/${seg(requestId)}:propose`, body),
      screeningFinalise: (proposalId: string) => post<Record<string, unknown>>(`/v1/screening/review/proposals/${seg(proposalId)}:finalise`),
      screeningWithdraw: (proposalId: string) => post<Record<string, unknown>>(`/v1/screening/review/proposals/${seg(proposalId)}:withdraw`),
      screeningProviders: () => get<M.Items<M.ScreeningProvider>>("/v1/screening/providers"),
      screeningExportRequest: (requestId: string, body: { purpose: string; include_sensitive: boolean; ttl_days: number }) =>
        post<M.ScreeningExportRequested>(`/v1/screening/review/cases/${seg(requestId)}/exports`, body),
      screeningExportApprove: (exportId: string) => post<M.ScreeningExportRequested>(`/v1/screening/review/exports/${seg(exportId)}:approve`),
      screeningExportFetch: (exportId: string) => get<M.ScreeningExportFetched>(`/v1/screening/review/exports/${seg(exportId)}`),
      screeningWatchCreate: (requestId: string, body: { cadence_days: number; triggers: string[] }) =>
        post<{ watch_id: string; active: boolean; classes: string[]; sources: string[] }>(`/v1/screening/review/cases/${seg(requestId)}/watches`, body),
      screeningWatchApprove: (watchId: string) => post<{ watch_id: string; approvals: number; active: boolean }>(`/v1/screening/review/watches/${seg(watchId)}:approve`),
      screeningWatchActivate: (watchId: string) => post<{ watch_id: string; active: boolean }>(`/v1/screening/review/watches/${seg(watchId)}:activate`),
      screeningWatch: (watchId: string) => get<M.ScreeningWatchStatus>(`/v1/screening/review/watches/${seg(watchId)}`),

      suspensions: (active = false) =>
        get<M.Items<{ current: M.SuspensionVersion; versions: M.SuspensionVersion[] }>>("/v1/regulatory/suspensions", { active: active ? "true" : undefined }),
      suspensionImpact: (scope: { provider: string; service_id?: string; scheme?: string; country?: string; purpose?: string }) =>
        get<{ scope: M.SuspensionScope; impact: M.SuspensionImpact }>("/v1/regulatory/suspensions/impact", scope),
      suspend: (body: M.SuspensionApplyIn) => post<M.SuspensionVersion & { impact: M.SuspensionImpact }>("/v1/regulatory/suspensions", body),
      liftSuspension: (id: string, reason: string, evidenceRef: string) =>
        post<M.SuspensionVersion>(`/v1/regulatory/suspensions/${seg(id)}:lift`, { reason, evidence_ref: evidenceRef }),

      /** WF-05 check method states (CR-112): the same two-person lifecycle as biometric methods. */
      checkMethods: () => get<C.CheckMethodsList>("/v1/regulatory/check-methods"),
      proposeCheckMethod: (body: C.CheckMethodProposeIn) => post<C.CheckMethodProposal>("/v1/regulatory/check-methods/proposals", body),
      approveCheckProposal: (id: string, reason: string) => post<unknown>(`/v1/regulatory/check-methods/proposals/${seg(id)}:approve`, { reason }),
      rejectCheckProposal: (id: string, reason: string) => post<unknown>(`/v1/regulatory/check-methods/proposals/${seg(id)}:reject`, { reason }),
      stepDownCheckMethod: (scopeId: string, body: { target: string; reason: string; evidence_ref: string }) =>
        post<C.CheckMethodVersion>(`/v1/regulatory/check-methods/${seg(scopeId)}:step-down`, body),

      /** Biometric method states per exact scope (ZID-DTV-WF-04; CR-107): a step up is proposed and approved by two people, a step down is at once. */
      biometricMethods: () => get<M.BiometricMethodsList>("/v1/regulatory/biometric-methods"),
      proposeBiometricMethod: (body: M.BiometricProposeIn) => post<M.BiometricProposal>("/v1/regulatory/biometric-methods/proposals", body),
      approveBiometricProposal: (id: string, reason: string) =>
        post<{ proposal: M.BiometricProposal; state: M.BiometricMethodVersion }>(`/v1/regulatory/biometric-methods/proposals/${seg(id)}:approve`, { reason }),
      rejectBiometricProposal: (id: string, reason: string) =>
        post<M.BiometricProposal>(`/v1/regulatory/biometric-methods/proposals/${seg(id)}:reject`, { reason }),
      stepDownBiometricMethod: (scopeId: string, body: M.BiometricStepDownIn) =>
        post<M.BiometricMethodVersion>(`/v1/regulatory/biometric-methods/${seg(scopeId)}:step-down`, body),
      /** CR-113 (QA-039): every scope in the country to OFF and its open proposals withdrawn, in one action by one person. */
      retireBiometricMarket: (country: string, body: { reason: string; evidence_ref: string }) =>
        post<M.MarketRetirement>(`/v1/regulatory/biometric-methods/markets/${seg(country)}:retire`, body),

      issuers: () => get<M.Items<string>>("/v1/regulatory/issuers"),
      recordIssuerStatement: (body: M.IssuerStatementIn) => post<M.IssuerStatementRecorded>("/v1/regulatory/issuers", body),
      issuerStatements: (issuerId: string) => get<M.Items<M.IssuerStatement>>(`/v1/regulatory/issuers/${seg(issuerId)}/statements`),
      awardCheck: (issuerId: string, program: string, awardedOn: string, knownAt?: string) =>
        get<M.AwardCheck>(`/v1/regulatory/issuers/${seg(issuerId)}/award-check`, { program, awarded_on: awardedOn, known_at: knownAt }),

      claims: (audience?: string) => get<M.Items<M.ClaimJudgement>>("/v1/regulatory/claims", { audience }),
      evidenceBundle: (bundleId: string, purpose: string, legalBasisRef: string) =>
        get<{ digest: string; signature: string | null; signature_status: string; content: Record<string, unknown> }>(
          `/v2/regulatory/evidence-bundles/${seg(bundleId)}`,
          { purpose, legal_basis_ref: legalBasisRef },
        ),

      conformanceMatrix: () => get<M.ConformanceMatrix>("/v1/regulatory/conformance-matrix"),
      conformanceRuns: () => get<M.Items<M.ConformanceRun>>("/v1/regulatory/conformance-runs"),
      conformanceRun: (runId: string) => get<M.ConformanceRun>(`/v1/regulatory/conformance-runs/${seg(runId)}`),
      reviewConformanceRun: (runId: string, decision: "ACCEPTED" | "REJECTED", signedHash: string, noteRef: string) =>
        post<M.ConformanceRun>(`/v1/regulatory/conformance-runs/${seg(runId)}:review`, { decision, signed_hash: signedHash, note_ref: noteRef }),

      privacyOverlays: () => get<M.Items<M.PrivacyOverlayView>>("/v1/regulatory/privacy-overlays"),
      privacyRouteCheck: (country: string, provider: string, biometric: boolean, consent: boolean) =>
        get<M.RouteCheck>("/v1/regulatory/privacy-route-check", { country, provider, biometric: String(biometric), consent: String(consent) }),
      monitoringCheck: (country: string, kind: string, sector = "ANY") => get<M.MonitoringCheck>("/v1/regulatory/monitoring-check", { country, kind, sector }),
      rightToRentCheck: (country: string, route: string, subdivision?: string, provider?: string) =>
        get<M.RightToRentCheck>("/v1/regulatory/right-to-rent/check", { country, route, subdivision, provider }),
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
    /** Operator Console email + password (the cell serves it only when operator password logins are configured). */
    operatorPasswordSignIn: (email: string, password: string) =>
      post<M.SignInResult>("/authn/operator/password", { client_id: clientId, email, password }),
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
