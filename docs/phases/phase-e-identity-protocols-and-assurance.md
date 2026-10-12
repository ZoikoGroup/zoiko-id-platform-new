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
