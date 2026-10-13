# Phase E: Identity protocols and assurance

> **Gate (Build Documentation Guideline §3):** Implements authentication, verification, credentials, federation and revocation.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 06 | ZID-AUTH-001 | Authentication, Session & Federation |
| 11 | ZID-VERIFY-001 | Identity Verification & Assurance |
| 12 | ZID-VC-001 | Verifiable Credentials, EUDI & Trust Federation |
| 13 | ZID-SIGNALS-001 | Continuous Evaluation, Revocation & Security Signals |

## Deliverables and status

Built in the Auth spec's build order (§27, AUTH-W0…W8), then Signals, Verification and Credentials. Every row below is implemented and tested end to end against PostgreSQL with row-level security (component tests named in the last column). W9: the conformance harness and the propagation load demonstration are in the repository (below); running the certification plans needs a deployed cell and is an owner action ([phase index](README.md#owner-actions-phase-e)).

### Authentication, sessions and federation (ZID-AUTH-001)

| Slice | Deliverable | Where | Evidence |
|---|---|---|---|
| W0 | Key ring and JWS policy (ES256/Ed25519, explicit `typ`, `kid`-pinned, rotation window); AAL-Z0…Z3 mapping; identity sessions; RFC 9068 access tokens; DPoP (RFC 9449); every protected API authenticates real tokens | `libs/zid-crypto`, `modules/session`, `api_auth.py` | `test_authn_pg.py` |
| W1 | Passkeys (WebAuthn L3, py_webauthn): bootstrap enrollment tickets, registration and discoverable login with UV, single-use bound challenges, sign-count anomalies capped at Z1 (not locked), step-up rotates the session, `__Host-` cookie, CSRF origin check, protected authenticator removal (Z3, never the last strong factor, revision++), sessions list/revoke/revoke-all; Related Origin Requests (FR-AUT-010): a governed list of exact https origins (at most 5 registrable labels) served at `/.well-known/webauthn` and accepted for the one RP ID | `modules/authentication`, `session/api/sessions.py` | `test_webauthn_pg.py` (AUTH-01/02/03/12/18) |
| W2 | Authorization server: code + PKCE S256, exact redirects, RFC 9207 `iss`, refresh rotation with reuse = family + session revoked + signal, RFC 7009 revocation, metadata, JWKS, ID tokens, DPoP nonces, tenant from client registration | `session/application/oauth_server.py` | `test_oauth_pg.py` (AUTH-03/04/05/06/07/19) |
| W2 | Consent for third-party clients: a third-party client (or `prompt=consent`) without a covering consent parks on a single-use, 10-minute consent request bound to the session and the validated request; the consent UI (`ZID_CONSENT_URL`) shows it and posts the decision (CSRF origin check, same session only); approval records the consent and issues the code, denial returns `access_denied`; a covering consent skips the prompt; users list and withdraw consents, which ends that client's refresh tokens; `prompt=none` still answers `consent_required` | `oauth_server.py`, `session/api/oauth.py` | `test_oauth_consent_pg.py` |
| W3 | private_key_jwt (RFC 7523) and tls_client_auth (RFC 8705) with certificate-bound tokens enforced at the resource server; PAR (RFC 9126); FAPI 2.0 rules (confidential, PAR, sender-constrained); client_credentials bound to a service principal; RFC 8693 delegation-only token exchange (actor chain, only narrows, keeps the session); RFC 7662 introspection; OIDC UserInfo; client secrets refused | `session/application/client_auth.py`, `oauth_server.py` | `test_oauth_w3_pg.py` |
| W4 | Enterprise OIDC federation: pinned metadata with expiry, exact iss/aud/azp/nonce, RFC 9207, acr/amr through an approved mapping, outage fails closed; account linking by issuer+subject only (explicit authenticated linking from a fresh strong session, or JIT), conflicts refused; R3-protected integration admin with rotation and quarantine | `modules/federation` | `test_federation_oidc_pg.py` (AUTH-08/09/15/16) |
| W5 | SAML 2.0 SP: signed assertions (SHA-256+ only) verified against pinned certificates, facts read only from the signed element (no signature wrapping), exact Destination/Recipient/Audience, InResponseTo, persistent NameID, assertion-ID replay cache, XXE/DTD refused, certificate rollover | `federation/application/saml_login.py` | `test_federation_saml_pg.py` (AUTH-10) |
| W5 | Encrypted assertions (XML Encryption 1.1): AES-GCM content with RSA-OAEP key transport only (CBC and RSA 1.5 refused, one indistinguishable error), the SP key from the secrets broker by reference, `require_encrypted_assertions` per connection; the decrypted assertion must itself be signed, or the signed Response must cover the ciphertext. Governed IdP-initiated SSO: off by default; enabling it is an R3 change that always needs a second administrator's approval bound to the exact landing targets; unsolicited responses only at the connection's own ACS, no InResponseTo, AuthnInstant within 5 minutes, replay cache, RelayState only one of the approved targets | `federation/application/saml_decrypt.py`, `saml_login.py`, `federation/api/federation.py` | `test_federation_saml_encrypted_pg.py` |
| W6 | SCIM 2.0 (scim2-models): Users/Groups CRUD, strict PATCH paths, ETags, source-scoped externalId, `active:false`/DELETE travel the signals path (suspend, revision++, sessions revoked), re-activation never restores sessions, groups never grant authority, R3-protected provisioning sources | `modules/provisioning` | `test_scim_pg.py` (AUTH-11) |
| W7 | Recovery: hashed one-time codes; RC-1 (code), RC-2 (re-proofing + delay), RC-3 administrator (re-proofing + second-administrator approval bound to the case + 72 h cooling-off; a code alone is never enough); 15-minute purpose-bound enrollment ticket; completion revokes every session and marks old factors LOST; owner can cancel; support can open, never complete; §24 protected admin actions (bulk session revoke etc.) through the PDP | `authentication/application/recovery.py`, `protected.py` | `test_recovery_pg.py` (AUTH-13/14) |
| W7 | RC-4 sovereign / lockbox recovery: a per-account policy (2-5 designated approvers, k >= 2, cooling-off 7-30 days) set or lifted only by an R3 change with an approval bound to that exact policy, never while a case is open; recovery then needs IAL-Z3 re-proofing and k *designated* approvers (other administrators do not count), cases live up to 35 days | `authentication/domain/recovery.py`, `authentication/api/recovery.py` | `test_recovery_lockbox_pg.py` |
| W8 | OpenID Federation 1.1: trust chains to pinned anchors, keys only as vouched by the superior, expiry, `crit`, max path length and naming constraints, metadata policy merge/apply, SSRF-safe fetch limited to trust domains; federation-backed OIDC connections; Zoiko's own entity configuration | `federation/domain/openid_federation.py`, `federation/application/trust_chain.py` | `test_openid_federation_pg.py` |

### Security signals (ZID-SIGNALS-001)

| Deliverable | Where | Evidence |
|---|---|---|
| Canonical signals and security state; deterministic versioned rules for CAEP, RISC and internal events; tightening always applies, loosening only from an authoritative, newer event; terminal states; append-only signal ledger with dedupe | `modules/signals/domain/rules.py`, `application/ingest.py` | `test_signals_pg.py` (SIG-05/06/16) |
| SSF push receiver (RFC 8935): stream credential, explicit `secevent+jwt`, pinned keys and algorithms, exact iss/aud, jti dedupe, one allowlisted event, iss_sub (via federation link) / opaque subjects, critical members, informational sources cannot suspend | `signals/application/ssf.py` | SIG-01/02/03/04/07/08/14 |
| SSF transmitter (poll, RFC 8936) with pairwise opaque subjects, as an outbox consumer; commit -> receiver-ack propagation measured per stream against R0-R3 SLOs; PEP observation lag logged. SETs are signed by their own key set, published at `/ssf/jwks`; the consumer enables the transmitter when it holds that key | `ssf.py`, `entrypoints/consumer.py`, `signal_store.propagation` | FR-SIG-003/004 |
| Revision-aware security-state cache (FR-SIG-006): TTL by risk class (R0 30 s, R1 5 s, R2/R3 never cached); a newer revision written in-process invalidates at once and an older read never replaces a newer one; protected (R2/R3) actions re-read the committed state in their own transaction | `signals/application/state_cache.py`, `api_auth.py`, `protected.py` | `test_state_cache.py`, `test_load_gate.py` |
| Propagation under load (FR-SIG-004): concurrent PEP readers, another process suspending every principal, p99 commit -> enforcement per risk class checked against `slo.yaml` (`revocation_propagation_seconds`) | `tests/load/propagation/` | `test_propagation_load_pg.py` |
| Real security state reaches every caller and the PDP; known restrictions now STEP_UP or DENY (UNKNOWN still fails closed); callers are credited AAL-Z3 when strong and fresh | `api_auth.py`, `decision/domain/pdp.py` | `test_pdp.py` |

### Identity verification (ZID-VERIFY-001)

| Deliverable | Where | Evidence |
|---|---|---|
| Immutable versioned profiles (IAL-Z1…Z3, claims, evidence, binding, biometric jurisdictions, freshness, review, reliance purposes); provider adapters with pinned signed callbacks and mapping versions, equivalence-group failover, circuit breaker, quarantine; deterministic per-claim decisions; assertions store commitments only; fraud -> manual review (dual control, never the subject) + risk signal; reliance decisions; assurance facts without evidence; credential presentations as DIGITAL_CREDENTIAL evidence | `modules/verification` | `test_verification_pg.py` |

### Verifiable credentials (ZID-VC-001; DEC-E2 verifier first)

| Deliverable | Where | Evidence |
|---|---|---|
| OID4VP 1.0 verifier: registered RP + purpose, minimized DCQL, signed single-use request object, direct_post, state/nonce binding; SD-JWT VC (RFC 9901 disclosures, KB-JWT aud/nonce/sd_hash, typed issuer trust by pinned keys or OpenID Federation); ISO mdoc (IACA chain, value digests, device signature over the OID4VP transcript); Token Status List (whole list, UNKNOWN never VALID), for mdoc as a `statuslist+cwt` signed under the pinned IACA; claims handed to the RP once | `modules/credential` | `test_credentials_pg.py` |
| HAIP: `x509_hash` client identifier (the request object carries the verifier certificate in `x5c`; a development certificate locally, `ZID_VERIFIER_CERTIFICATE_CHAIN` elsewhere, which must certify the verifier key); `direct_post.jwt` responses encrypted (ECDH-ES, A128GCM/A256GCM) to a per-presentation key that is wiped when the presentation completes; plaintext, other keys, other states and other algorithms refused | `credential/application/verifier.py`, `keys.py` | `test_oid4vp_haip_pg.py` |
| W3C VCDM 2.0 (VC-JOSE-COSE) verifier: holder-signed `vp+jwt` enveloping one issuer-signed `vc+jwt`, contexts pinned (never fetched, no inline contexts), validity window, holder binding by aud/nonce; Bitstring Status List (revocation and suspension, MSB first, bounded GZIP) from the issuer's own domain | `credential/domain/vcdm.py`, `verifier.py` | `test_vcdm_pg.py`, `test_vcdm.py` |
| Controlled issuer (OID4VCI): assurance attestation (levels and claim names, never values), holder key proof, idempotent per proof, status list publication, R3 revoke/suspend; issuing a configuration needs `credential.issue`; QTSP, wallet, PID and Business Wallet roles refused | `credential/application/issuer.py` | same |

### Operations

| Deliverable | Where |
|---|---|
| Maintenance pass: purges spent single-use material, sweeps expired sessions, refresh families, verification assertions (with events), recovery cases and presentations, removes uncollected disclosed claims | `entrypoints/maintenance.py` (`test_maintenance_pg.py`) |
| Published contracts: authentication, federation, SCIM, verification, credentials, signals and protocol endpoints (drift-tested) | `contracts/openapi/public/*.json` |
| CR-009: Phase E tables, `security.state.changed` and `provisioning.resource.changed` events, catalogue actions | `docs/specs/change-requests/CR-009-phase-e-identity-additions.md` |
| Signing keys by purpose (token, SET, federation, credential, verifier), each with its own published key set; development keys per purpose locally (`ZID_DEV_SIGNING_KEYS` to share them between processes), real keys required elsewhere | `keys.py`, `main.py` (`test_keys.py`) |
| Conformance harness: OpenID Foundation suite plans (OIDC Basic/Config, FAPI 2.0 DPoP and mTLS, OID4VP HAIP for SD-JWT VC and mdoc, SSF transmitter) with a runner; the plans are tested against what the server advertises | `tests/conformance/` (`test_conformance_plans.py`) |

**Settings:** `ZID_TOKEN_ISSUER` (authorization server issuer; OAuth endpoints and their DPoP `htu` are built from it), `ZID_TOKEN_AUDIENCE`, `ZID_PUBLIC_BASE_URL`, `ZID_WEBAUTHN_RP_ID` / `ZID_WEBAUTHN_RP_NAME` / `ZID_WEBAUTHN_ORIGINS` (explicit RP and origin allowlist), `ZID_MTLS_CLIENT_CERT_HEADER` (only when the edge strips it inbound), `ZID_FEDERATION_AUTHORITY_HINTS`, `ZID_MAINTENANCE_DATABASE_URL` (maintenance job), `ZID_WEBAUTHN_RELATED_ORIGINS`, `ZID_CONSENT_URL`, `ZID_VERIFIER_CLIENT_ID_PREFIX` / `ZID_VERIFIER_CERTIFICATE_CHAIN` (HAIP), `ZID_DEV_SIGNING_KEYS` (local/test only).

**Open points for DEC-E1 / DEC-F5 (owner decisions, recorded, not assumed):**
- OIDC Discovery expects `RS256` in `id_token_signing_alg_values_supported`; the platform signs only ES256/Ed25519. Approve an ID-token exception or accept the conformance deviation.
- External verification (enterprise IdP ID tokens, SSF transmitters) allows RS256/PS256 with RSA >= 2048, per integration; Zoiko never signs with them.
- SAML uses signxml (pure Python, returns only the signed content) rather than pysaml2, whose verification needs the external xmlsec1 binary.
- SD-JWT verification is implemented in-house (RFC 9901, pinned draft SD-JWT VC and Token Status List) rather than sd-jwt-python, to keep the platform algorithm allowlist and version pinning in one place.
- Keys are separated by purpose in code (token, SET, federation, credential, verifier); deployed cells need the KMS-backed key broker (Phase F, DEC-F5) to supply them - without it they refuse to start.
- XML Encryption is implemented in-house on `cryptography` (decrypt side only, AES-GCM + RSA-OAEP), for the same reason as signxml.

**Known limits of this release:** the consent *screen* itself belongs to the product surfaces (Phase H); the platform provides the consent API it drives. Conformance certification runs and the propagation soak at production scale need a deployed PERF/STAGING cell (owner action). Zoiko issues only SD-JWT VC credentials; W3C VCDM and mdoc are verified, not issued.

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-E1](#dec-e1-identity-protocol-stack-and-profiles) | Identity protocol stack and profiles | Identity Lead | Proposed |
| [DEC-E2](#dec-e2-wallet-holder-and-trust-service-roles) | Wallet, holder and trust-service roles | Product Lead + Credentials Lead; Board for the deferred rol… | Proposed |

---

### DEC-E1: Identity protocol stack and profiles

- **Status:** Proposed
- **Owner:** Identity Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Authentication, Session & Federation spec §0.1–0.2 on Python (DEC-A2); Enterprise Overlay spec §6–7, §10
- **Sources:** Authentication, Session & Federation spec §0.1 (standards baseline), §0.2, AAL-Z0–Z3, FAPI 2.0 profile; Enterprise Overlay spec (OIDC/SAML/SCIM profiles); Readiness plan (OpenID conformance, G6)

#### Context
Identity protocols are where small implementation errors become account takeover. The specs fix the standards and profiles. This decision fixes the libraries and the conformance obligations that prove them.

#### Decision

##### 1. Standards baseline (from the Auth spec)

| Standard | Use |
|---|---|
| **WebAuthn Level 3** | Passkeys, the preferred native human authentication. Password-only is not a Tier-1 target state. |
| OAuth 2.0 Security BCP **RFC 9700** | Code + PKCE, exact redirects, no implicit flow, sender constraint |
| **DPoP RFC 9449** / **mTLS RFC 8705** | Sender-constrained tokens (DPoP for public clients, mTLS for confidential service clients) |
| **PAR RFC 9126** | Required for FAPI-class profiles |
| **FAPI 2.0 Security Profile** | Selected **by client risk**, not universal overhead |
| OpenID Connect Core | Federated login; issuer, audience, nonce and state validation mandatory |
| OpenID Federation 1.1 | New multilateral federation trust |
| SCIM 2.0 (RFC 7643/7644) | Lifecycle only; **never creates business authority** |
| SAML 2.0 | Incumbent compatibility adapter with a locked security profile |
| RFC 8693 / RAR | Token exchange and transaction detail (DEC-D3) |

##### 2. Libraries (Python)

| Area | Library | Notes |
|---|---|---|
| OAuth 2.0 / OIDC server and client | **Authlib** | Where Authlib lacks a profile (for example PAR, DPoP, RFC 8693 details), we implement it in the SES module on top of Authlib primitives. Conformance tests (§4) decide what's acceptable, not library claims. |
| JOSE / JWT | **joserfc** | Algorithm allowlists come from the crypto policy registry (DEC-F5); `alg=none` and algorithm confusion are rejected |
| WebAuthn / passkeys | **py_webauthn** | Attestation and metadata policy are configured per tenant |
| SAML 2.0 | **pysaml2** | Signed/pinned metadata, exact audience/ACS, `InResponseTo`, bounded clock skew, no open redirect via RelayState |
| SCIM 2.0 | **scim2-models** + FastAPI routes | Strict PATCH path/schema validation; idempotent |
| SD-JWT / credentials | **sd-jwt-python** | CRED module; mdoc/OID4VP may move to Kotlin (DEC-A2 exception, decided at W3) |

All signing goes through `zid-crypto` (DEC-F5). No library holds private keys.

##### 3. Artifact separation and assurance
- A Zoiko identity session, an application session, an OAuth token and an agent-mandate execution context are **separate artifacts with separate lifetimes**.
- Authentication assurance is expressed as **AAL-Z0 to AAL-Z3** and mapped from IdP `acr`/`amr` only through an approved mapping profile. **Authentication is not authority** (AUTHZ-10).
- Refresh tokens rotate, and **reuse revokes the family**.
- R2/R3 operations always require fresh online authorization, even when the token is valid.

##### 4. Conformance (release gates)
- OpenID Foundation conformance suites for **OIDC OP**, and for **FAPI 2.0** wherever that profile is offered.
- Passkey, federation, token and recovery security tests (Readiness plan).
- SAML negative tests: signature wrapping, audience and recipient mismatch, replay.

#### Acceptance
- The conformance suites pass for every claimed profile before the claim is made (G6).
- Account recovery cannot bypass passkey/MFA strength (P1-01).

#### Consequences
Library upgrades rerun the full conformance suite in CI. A library that fails conformance is replaced or patched; the profile is never weakened.

---

### DEC-E2: Wallet, holder and trust-service roles

- **Status:** Proposed. **Product and the Board decide the deferred roles.**
- **Owner:** Product Lead + Credentials Lead; Board for the deferred roles (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-08, §12.1
- **Sources:** Back-End Target Architecture §12.1–12.3, §18.3; Verifiable Credentials, EUDI & Trust Federation spec §16 (EUDI Wallet Integration Posture), §17; Org Authority spec (European Business Wallet adapter)

#### Context
Supporting OID4VCI/OID4VP and EUDI protocols does not make Zoiko a wallet provider, PID provider or qualified trust service provider. Each of those roles carries separate legal, certification and liability obligations.

#### Decision

| Role | Posture | Rule |
|---|---|---|
| **Relying party / verifier** | **BASELINE: build first** | Register where the Member State or ecosystem requires; request only registered purposes and attributes |
| **Issuer of non-qualified attestations (EAA)** | CONTROLLED: build second | Only after the credential type, legal role (DEC-F3) and trust registration are approved |
| **Issuer of qualified EAA** | DEFERRED | Needs a separate QTSP, legal and certification decision |
| **Wallet / holder product** | **DEFERRED: separate product decision** | Wallet UX and storage are never forced into the verifier core |
| **PID provider** | DEFERRED / ECOSYSTEM | Only if a Member State role and contracts justify it |
| **Qualified trust service provider** | **SEPARATE DECISION** (legal/certification gate) | No implied status from protocol support |
| **European Business Wallet provider** | TRACK | No product commitment until the final Regulation and implementing obligations are known; build only an isolated `BUSINESS_WALLET` source adapter |

##### Technical baseline that comes with the verifier/issuer roles
- **Protocols:** OID4VCI 1.0, OID4VP 1.0, HAIP 1.0, with EUDI ARF 3.0.0 as the current technical baseline.
- **Formats:** handled through adapters (SD-JWT VC, mdoc). The domain model never depends on one format.
- **Trust:** OpenID Federation 1.1 for multilateral trust. Wallet-architecture federation remains a tracked draft adapter.
- **Assertions:** verification outputs a normalized `VerificationAssertion` / `AssuranceResult`. **A valid credential never implies authority for an unrelated business action.**
- **Privacy:**
  - request the minimum attributes for the purpose
  - avoid stable cross-verifier identifiers
  - status checks must not track holders
  - raw presentations are retained only when the evidence class and legal schedule require it

#### Consequences
- W3 builds verifier infrastructure first, then issuer infrastructure.
- No wallet code lives in the platform repo.
- The Business Wallet regulation is tracked by Legal. A follow-up legal/architecture decision is required once it's final (Org Authority spec).
