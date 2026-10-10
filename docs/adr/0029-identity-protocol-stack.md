# ADR-0029: Identity protocol stack and profiles

- **Status:** Proposed
- **Owner:** Identity Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Authentication, Session & Federation spec §0.1–0.2 on Python (ADR-0012); Enterprise Overlay spec §6–7, §10
- **Sources:** Authentication, Session & Federation spec §0.1 (standards baseline), §0.2, AAL-Z0–Z3, FAPI 2.0 profile; Enterprise Overlay spec (OIDC/SAML/SCIM profiles); Readiness plan (OpenID conformance, G6)

## Context
Identity protocols are where small implementation errors become account takeover. The specs fix the standards and profiles. This ADR fixes the libraries and the conformance obligations that prove them.

## Decision

### 1. Standards baseline (from the Auth spec)

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
| RFC 8693 / RAR | Token exchange and transaction detail (ADR-0005) |

### 2. Libraries (Python)

| Area | Library | Notes |
|---|---|---|
| OAuth 2.0 / OIDC server and client | **Authlib** | Where Authlib lacks a profile (for example PAR, DPoP, RFC 8693 details), we implement it in the SES module on top of Authlib primitives. Conformance tests (§4) decide what's acceptable, not library claims. |
| JOSE / JWT | **joserfc** | Algorithm allowlists come from the crypto policy registry (ADR-0026); `alg=none` and algorithm confusion are rejected |
| WebAuthn / passkeys | **py_webauthn** | Attestation and metadata policy are configured per tenant |
| SAML 2.0 | **pysaml2** | Signed/pinned metadata, exact audience/ACS, `InResponseTo`, bounded clock skew, no open redirect via RelayState |
| SCIM 2.0 | **scim2-models** + FastAPI routes | Strict PATCH path/schema validation; idempotent |
| SD-JWT / credentials | **sd-jwt-python** | CRED module; mdoc/OID4VP may move to Kotlin (ADR-0012 exception, decided at W3) |

All signing goes through `zid-crypto` (ADR-0026). No library holds private keys.

### 3. Artifact separation and assurance
- A Zoiko identity session, an application session, an OAuth token and an agent-mandate execution context are **separate artifacts with separate lifetimes**.
- Authentication assurance is expressed as **AAL-Z0 to AAL-Z3** and mapped from IdP `acr`/`amr` only through an approved mapping profile. **Authentication is not authority** (AUTHZ-10).
- Refresh tokens rotate, and **reuse revokes the family**.
- R2/R3 operations always require fresh online authorization, even when the token is valid.

### 4. Conformance (release gates)
- OpenID Foundation conformance suites for **OIDC OP**, and for **FAPI 2.0** wherever that profile is offered.
- Passkey, federation, token and recovery security tests (Readiness plan).
- SAML negative tests: signature wrapping, audience and recipient mismatch, replay.

## Acceptance
- The conformance suites pass for every claimed profile before the claim is made (G6).
- Account recovery cannot bypass passkey/MFA strength (P1-01).

## Consequences
Library upgrades rerun the full conformance suite in CI. A library that fails conformance is replaced or patched; the profile is never weakened.
