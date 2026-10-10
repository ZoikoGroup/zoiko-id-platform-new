# ADR-0008: Wallet, holder and trust-service roles

- **Status:** Proposed. **Product and the Board decide the deferred roles.**
- **Owner:** Product Lead + Credentials Lead; Board for the deferred roles (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-08, §12.1
- **Sources:** Back-End Target Architecture §12.1–12.3, §18.3; Verifiable Credentials, EUDI & Trust Federation spec §16 (EUDI Wallet Integration Posture), §17; Org Authority spec (European Business Wallet adapter)

## Context
Supporting OID4VCI/OID4VP and EUDI protocols does not make Zoiko a wallet provider, PID provider or qualified trust service provider. Each of those roles carries separate legal, certification and liability obligations.

## Decision

| Role | Posture | Rule |
|---|---|---|
| **Relying party / verifier** | **BASELINE: build first** | Register where the Member State or ecosystem requires; request only registered purposes and attributes |
| **Issuer of non-qualified attestations (EAA)** | CONTROLLED: build second | Only after the credential type, legal role (ADR-0007) and trust registration are approved |
| **Issuer of qualified EAA** | DEFERRED | Needs a separate QTSP, legal and certification decision |
| **Wallet / holder product** | **DEFERRED: separate product decision** | Wallet UX and storage are never forced into the verifier core |
| **PID provider** | DEFERRED / ECOSYSTEM | Only if a Member State role and contracts justify it |
| **Qualified trust service provider** | **SEPARATE ADR** (legal/certification gate) | No implied status from protocol support |
| **European Business Wallet provider** | TRACK | No product commitment until the final Regulation and implementing obligations are known; build only an isolated `BUSINESS_WALLET` source adapter |

### Technical baseline that comes with the verifier/issuer roles
- **Protocols:** OID4VCI 1.0, OID4VP 1.0, HAIP 1.0, with EUDI ARF 3.0.0 as the current technical baseline.
- **Formats:** handled through adapters (SD-JWT VC, mdoc). The domain model never depends on one format.
- **Trust:** OpenID Federation 1.1 for multilateral trust. Wallet-architecture federation remains a tracked draft adapter.
- **Assertions:** verification outputs a normalized `VerificationAssertion` / `AssuranceResult`. **A valid credential never implies authority for an unrelated business action.**
- **Privacy:**
  - request the minimum attributes for the purpose
  - avoid stable cross-verifier identifiers
  - status checks must not track holders
  - raw presentations are retained only when the evidence class and legal schedule require it

## Consequences
- W3 builds verifier infrastructure first, then issuer infrastructure.
- No wallet code lives in the platform repo.
- The Business Wallet regulation is tracked by Legal. A follow-up legal/architecture ADR is required once it's final (Org Authority spec).
