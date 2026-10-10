# ADR-0009: Post-quantum cryptography migration

- **Status:** Proposed
- **Owner:** Security / Cryptography Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-09, §15.2
- **Sources:** Back-End Target Architecture §15.2; Cryptography, Key Management & Machine Identity spec §0.2, §12–13 (ZID-CRYPTO-MI-001)
- **Note:** ZID-CRYPTO-001 (Secrets spec, 6 Oct) and ZID-CRYPTO-MI-001 (7 Oct) have **identical** PQ sections. CRYPTO-MI-001 looks like a superset; which one is authoritative is open question Q2.

## Context
As of October 2026:
- hybrid post-quantum TLS is standardized (RFC 10024)
- ML-DSA has standard JOSE/COSE and X.509 encodings
- NIST SP 800-227 is final

Evidence and credentials live a long time, so harvest-now-decrypt-later and future signature forgery risks are real for them. The crypto spec therefore moves from "crypto-agility only" to a controlled production migration.

## Decision

### 1. Crypto-agility is mandatory now
- Algorithms and key versions are **metadata**, never hard-coded schema assumptions.
- Every algorithm is chosen from a centrally versioned allowlist (the Crypto Policy Registry). Untrusted `alg` or header input never selects behavior.
- All keys and certificates are inventoried with algorithm and PQ-readiness.

### 2. Migration waves

| Wave | Scope | Position |
|---|---|---|
| **PQC-0 Inventory** | Every asymmetric key exchange and signature, library, certificate, token and long-retention data path | **Mandatory now (W0)** |
| **PQC-1 Hybrid transport** | RFC 10024 TLS 1.3 hybrid groups on supported internal and edge stacks; measure fallback | Production target 2026/27 |
| **PQC-2 Long-lived signatures** | ML-DSA-65 (or approved profile) for evidence, checkpoints and selected issuer artifacts; **dual-sign** where classical compatibility is needed | **Priority**: evidence and credentials are long-lived |
| **PQC-3 Certificates/PKI** | Pilot RFC 9881 ML-DSA and RFC 9935 ML-KEM certificates only where the ecosystem supports them | No forced public-Web breakage |
| **PQC-4 Token ecosystems** | Keep short-lived OAuth JWT interoperability; add PQ-capable JOSE paths | Migrate when counterparties support it |
| **PQC-5 Additional algorithms** | Track HQC and FN-DSA (FIPS 206) | No production requirement until final and mature |

### 3. Hybrid TLS policy
- **Preferred group:** `X25519MLKEM768`. `SecP256r1MLKEM768` is the FIPS-oriented option.
- **Fallback** to classical key exchange happens only when the profile permits, and is measured. Regulated high-value profiles may require hybrid.
- **Pre-standard Kyber code points are disabled.**
- **Prioritization:** harvest-now-decrypt-later risk ranks which flows move to hybrid transport first (long-retention identity, authority, evidence, regulated data).

### 4. Signing posture

| Artifact | Algorithm posture |
|---|---|
| First-party OAuth/OIDC tokens | ES256 baseline; EdDSA in approved non-FIPS ecosystems; RS256 only for compatibility; never HS* for cross-party tokens |
| Evidence / checkpoints | Strong classical signature + ML-DSA migration / dual-sign |
| Verifiable credentials | Determined by the credential profile; issuer keys isolated per trust profile |
| Webhooks | RFC 9421 asymmetric profile |

## Implementation note (AWS, ADR-0018)
Confirm ML-DSA support in AWS KMS and CloudHSM, and hybrid TLS support in Envoy, Istio and the Python TLS stack, before PQC-1 and PQC-2 start. Where a provider lacks support, the crypto broker (`zid-crypto`) isolates the gap. No algorithm is ever hard-coded into callers.

## Acceptance
- The PQC-0 inventory is complete and kept current.
- Evidence verification succeeds across an algorithm rotation, including dual-signed artifacts.
- Hybrid-to-classical fallback is observable in telemetry.

## Consequences
- Evidence checkpoint signing is the first PQ production use (W1/W2 timeframe).
- Token ecosystems follow counterparties, not Zoiko's schedule.
