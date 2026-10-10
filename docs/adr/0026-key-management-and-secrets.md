# ADR-0026: Key management and secrets implementation

- **Status:** Proposed
- **Owner:** Security / Cryptography Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** CRYPTO-MI-001 §1–3, §6, §9–10, §15–16, §20–23 on AWS (ADR-0018); supports ADR-0006, ADR-0009, ADR-0024
- **Sources:** Cryptography, Key Management & Machine Identity spec (ZID-CRYPTO-MI-001) §0.2, §1, §1.1, §2, §3, §9, §10, §15, §16, §20, §23; Multi-Tenant/Residency spec (key boundaries)

## Context
The crypto spec fixes the hierarchy (K0–K5), key classes, envelope encryption, CMK/BYOK/HYOK modes and secret delivery, but not the products. Architecture §14.3 mapped them to AWS without an ADR.

## Decision

### 1. Crypto broker: the only application interface
`zid-crypto` is the **only** way application code uses keys. It validates purpose and context, enforces the Crypto Policy Registry allowlists, and calls KMS/HSM. No service calls KMS directly.

### 2. Hierarchy on AWS

| Spec layer | Implementation |
|---|---|
| K0 hardware root | CloudHSM cluster per cell; KMS custom key store backed by CloudHSM where FIPS 140-3 / regulated tier requires it |
| K1 platform roots | Evidence/checkpoint root and CA root in CloudHSM. **The CA root is offline/ceremony-based** (M-of-N custodians). |
| K2 domain/cell KEKs | KMS keys **per purpose per cell** (identity, evidence, privacy, tenant-wrap, backup) |
| K3 tenant KEKs | KMS key per tenant (`DEDICATED_TENANT_KEY`), or a customer key per CMK mode |
| K4 DEKs / operational | Random AES-256-GCM DEKs from `zid-crypto`, **stored only wrapped**; token, webhook and VC signing keys are KMS asymmetric keys |
| K5 session | TLS/mesh, memory only |

- **Purpose separation** is absolute: DATA_KEK, DATA_DEK, SUBJECT_ERASURE_KEY, TOKEN_SIGNING, FEDERATION_SIGNING, VC_ISSUER_SIGNING, EVIDENCE_SIGNING, WEBHOOK_SIGNING, CA, HMAC_INTERNAL, BACKUP_KEK and SECRET_ENCRYPTION never share keys.
- **There is no global master key** that can decrypt every cell. KEKs stay in the cell's regional KMS/HSM.

### 3. Envelope encryption contract
- **AES-256-GCM** with unique nonces.
- **AAD** = `tenant | object_type | object_id | field/purpose | schema_version`, and the KMS encryption context matches it.
- **Stored with the ciphertext:** nonce, tag, wrapped DEK, key version and algorithm.
- **Read path:** authorize first, then unwrap under the same context, then decrypt, then zeroize the key material.

### 4. Customer key modes

| Mode | Notes |
|---|---|
| `PLATFORM_MANAGED` | Default |
| `DEDICATED_TENANT_KEY` | One KEK per tenant |
| `BYOK` | Imported key material, with documented provenance and expiry |
| `HYOK` | KMS external key store. Its availability becomes part of the SLO; it **fails closed** with no platform fallback. |

The UI and API require protected confirmation for destructive customer-key actions, because **key loss means data loss**. Platform root, signing, CA and evidence keys are never exportable.

### 5. Secrets (OpenBao)
- **OpenBao** per cell is the Secrets Broker.
- Workloads authenticate with their SPIFFE identity (ADR-0025) and get **short-lease dynamic credentials**, for example a PostgreSQL role per service (ADR-0016).
- Secrets are referenced by logical name and kept in memory. Values are **never** stored in code, images, Git, OpenTofu state, logs, traces, tickets or chat.
- External provider secrets are rotated, and replaced by OAuth/mTLS/`private_key_jwt` where the provider supports it.
- **No developer access to production secrets.** Troubleshooting uses JIT/lockbox (ADR-0028).

### 6. Ceremonies and dual control

| Operation | Control |
|---|---|
| Routine service keys | Automated rotation by service identity |
| Tenant BYOK/dedicated keys | Step-up + second approval for destructive actions |
| Root CA and evidence root | HSM ceremony, M-of-N custodians, `KeyCeremonyRecord` evidence |
| Key destruction | Dependency/legal-hold check + dual approval (K1/K2/high-impact) + destruction receipt |

Key administrators have **no standing data access** and cannot decrypt tenant ciphertext through the console.

### 7. Rotation and DR
- No flag-day rotations. Rotation overlaps, and rewrapping happens in the background.
- DR keys are pre-provisioned in the approved recovery cell per residency (ADR-0011). **No emergency key export** to files.

## Acceptance
- No plaintext KEK or long-lived DEK exists outside KMS/HSM.
- An AAD mismatch fails decryption.
- Crypto-erasure (destroying a SUBJECT_ERASURE_KEY) makes the data unreadable, including from backups (ADR-0006).
- A HYOK outage fails closed.
- The key inventory is complete, including algorithm and PQ-readiness (ADR-0009 PQC-0).

## Consequences
Any provider gap for PQ algorithms is absorbed inside `zid-crypto` (ADR-0009).
