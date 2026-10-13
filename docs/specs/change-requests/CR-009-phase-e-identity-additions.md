# CR-009: Phase E identity, signals and credential additions

- **Target:** ZID-DATA-001 §5, §6, §10; ZID-EVENTS-001 §22; action catalogue (DEC-B1) · **Raised by:** Phase E implementation (AUTH-W1…W8, VERIFY, VC, SIGNALS) · **Status:** Open

The Phase E specs require state that ZID-DATA-001 v1.0 does not name, and a few events and catalogue actions that no spec lists. Each item below is implemented now as `proposed` (table-ownership registry, `proposed-events.yaml`, action catalogue `name_source: proposed`) and moves to canonical once the owners accept it.

## Tables

| Table | Owner | Required by | Why |
|---|---|---|---|
| `authn.authorization_code` | session | AUTH §6.4 | Single-use, <= 60 s codes, stored as hashes |
| `authn.client_directory` | session | ARCH edge rule (tenant from client registration) | The only pre-tenant lookup: client id -> tenant, written by trigger |
| `authn.dpop_nonce` | session | AUTH §7.1 | Server DPoP nonces (RFC 9449 §8) |
| `authn.par_request` | session | AUTH §6.2, §6.4, §13 | Pushed authorization requests (RFC 9126) |
| `authn.webauthn_challenge` | authentication | AUTH §3.1-3.3, §6.4 | Single-use, bound challenges |
| `authn.enrollment_ticket` | authentication | AUTH §3.1, §14 | Bootstrap / recovery-only enrollment context |
| `signals.ssf_stream` | signals | SIGNALS §5-6 | Receiver and transmitter streams |
| `signals.ssf_subject_map` | signals | SIGNALS §9 | Stream-scoped opaque subjects (no email correlation) |
| `signals.ssf_outbound_set` | signals | SIGNALS §7, §21 | Poll delivery queue (RFC 8936) |
| `authn.federation_login` | federation | AUTH §9, §10 | Single-use federated login transaction (state, nonce, PKCE / SAML request id) |
| `authn.saml_assertion_replay` | federation | AUTH §10 | Assertion-ID replay cache through validity |
| `platform.scim_resource` | provisioning | AUTH §11 | SCIM User/Group as provisioned (source-scoped externalId) |
| `platform.scim_group_member` | provisioning | AUTH §11 | Group membership facts (never authority) |
| `verify.provider_adapter` | verification | VERIFY §9-10 | Provider adapter contract, equivalence group, circuit breaker, quarantine |
| `verify.verification_session` | verification | VERIFY §4-5 | VerificationSession state machine |
| `verify.provider_callback` | verification | VERIFY §22 | Callback dedupe (event id + content hash) |
| `verify.review_case`, `verify.review_decision` | verification | VERIFY §16 | Reason-coded, dual-control manual review |
| `verify.reliance_decision` | verification | VERIFY §17 | Explicit reliance decisions |
| `authn.recovery_code` | authentication | AUTH §4 | One-time hashed recovery codes |
| `authn.recovery_case` | authentication | AUTH §14 | Recovery class, gates and outcome |
| `authn.federation_trust_anchor` | federation | AUTH §12 | OpenID Federation trust anchors (pinned keys, trust domains) |
| `cred.credential_configuration` | credential | VC §5 | Credential configuration registry (pinned adapter versions) |
| `cred.issuer_trust` | credential | VC §4, §15 | Typed issuer trust (per credential type and jurisdiction) |
| `cred.rp_registration` | credential | VC §18 | Relying-party purposes and permitted attributes |
| `cred.status_list` | credential | VC §13 | Token Status Lists for credentials Zoiko issues |

`verify.assurance_profile` is implemented tenant-scoped (`tenant_id NOT NULL`); DATA §6.2 allows a NULL tenant for platform profiles, which needs an RLS read policy for shared rows and is left to the data-model owner.

## Events

| Event | Lane | Why |
|---|---|---|
| `security.state.changed` | priority | SIGNALS §27 internal propagation envelope; no spec event list declares it |
| `provisioning.resource.changed` | domain | AUTH §11: SCIM lifecycle needs an id-only change event |

## Catalogue actions

| Action | Floor | Why |
|---|---|---|
| `signals.stream.manage` | R3 / E3 | SIGNALS §26 `signals:manage` (a stream can suspend principals) |
| `security_state.override` | R3 / E3 | SIGNALS §26 governed `:reevaluate` that can relax state |
| `provisioning.source.manage` | R3 / E3 | AUTH §11, §24: a SCIM connector can suspend every provisioned account |
| `verification.profile.manage` | R3 / E3 | VERIFY §21 protected profile activation |
| `verification.adapter.manage` | R3 / E3 | VERIFY §9-10 provider trust and quarantine |
| `verification.review.decide` | R2 / E2 | VERIFY §16 protected reviewer |
| `verification.assertion.revoke` | R2 / E2 | VERIFY §18 assertion invalidation |
| `authentication.recovery.assist` | R2 / E2 | AUTH §14: support opens a governed case (cannot complete it) |
| `authentication.recovery.approve` | R3 / E3 | AUTH §14.1 RC-3 second-administrator approval |
| `authentication.recovery.deny` | R2 / E2 | AUTH §14 deny a suspicious recovery |
| `session.bulk_revoke` | R2 / E2 | AUTH §24 bulk session revoke |
| `credential.trust.manage` | R3 / E3 | VC §4, §15 issuer trust and configuration releases |
| `credential.status.change` | R3 / E3 | VC §13 suspend/revoke an issued credential |
