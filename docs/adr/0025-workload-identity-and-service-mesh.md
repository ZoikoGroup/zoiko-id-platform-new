# ADR-0025: Workload identity (SPIFFE/SPIRE) and service mesh (Istio ambient)

- **Status:** Proposed
- **Owner:** Machine Identity Lead + Platform Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Engineering Brief non-negotiable "no static machine secrets"; AI Agent & Workload Identity spec §5; Cryptography & Machine Identity spec §24–29; Service Decomposition spec §10
- **Sources:** AI Agent spec §4–6, §8, §18; CRYPTO-MI-001 §25 (SPIFFE profile), §27 (mTLS/SVID/PKI), §29 (revocation/quarantine/federation); Service spec §10, §23

## Context
The specs require short-lived workload identity, mTLS on critical paths and automatic rotation, with no static secrets. The spec calls SPIRE "an implementation option, not hard-coded into the domain model", so the choice is recorded here.

## Decision

### 1. Implementation
- **SPIRE** server per cell, with an agent on every node, issuing **X.509-SVIDs** for mTLS. **JWT-SVIDs** are audience-bound, for JWT-oriented integrations.
- **Istio ambient mode** uses the SPIFFE identities for mTLS on every service hop, plus authorization policies that allow only the dependency graph declared in architecture §6.1.
- **Cloud API access** uses EKS Pod Identity. No static AWS keys.

### 2. Trust domains
- **One governed trust domain per cell and environment**, for example `spiffe://uk-1.prod.id.zoiko`. Sovereign or dedicated cells get their own. Names are governed, never ad hoc.
- **Federation across trust domains** requires an explicit federation policy. There is **no trust-on-first-use** for production R2/R3.
- **Intermediate CAs** are scoped to the cell and environment, with non-exportable keys in HSM/KMS (ADR-0026). Trust bundles are versioned and independently rotatable.

### 3. Mapping invariant (an SVID authenticates; it never authorizes)
```
SPIFFE ID / SVID → WorkloadIdentity → logical principal (SERVICE / WORKLOAD / AI_AGENT)
                 + environment + artifact/selector evidence + current security_revision
                 → OAuth token exchange or direct mTLS → authority + mandate + policy evaluation
```
- The SPIFFE ID maps to a registered workload binding (MACH module). The URI alone is not business authority.
- Never authorize from a certificate CN or display name.
- Service credentials never impersonate end users. A represented actor travels separately (TCE, ADR-0005).

### 4. Rotation and revocation
- SVIDs are short-lived and auto-renewed before expiry, with hot reload and bounded overlap.
- If rotation fails, alert before the safety window ends. There is no fallback to a shared certificate, and no extending timestamps by hand.
- For ordinary ephemeral SVIDs we avoid depending on CRL/OCSP. **Urgent compromise is enforced through `security_revision` and workload quarantine** (SIG), on the R3 revocation SLO.

### 5. Network
- Network location is never identity (Zero Trust).
- Egress to registries, IdPs, verification vendors and webhooks goes through allowlisted, proxied integration egress, which blocks SSRF.

## Acceptance
- A pod without a valid SVID can't call any internal API.
- A call outside the declared dependency graph is rejected by mesh policy.
- A quarantined workload is denied within the R3 SLO.
- Compromise of one cell's trust domain doesn't authenticate in another.

## Consequences
- Local development uses a SPIRE dev profile in docker-compose.
- `zid-context` reads the caller identity from the mesh-verified peer identity, never from headers.
