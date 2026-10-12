# Phase F: Privacy, evidence and security

> **Gate (Build Documentation Guideline §3):** Closes privacy, audit, cryptographic, operator-trust and sovereignty controls.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 14 | ZID-PRIV-001 | Consent, Privacy, Retention & Data Rights |
| 15 | ZID-EVIDENCE-001 | Decision Evidence, Audit & Provenance |
| 17 | ZID-SEC-001 | Security Architecture, Threat Model & Operator Trust |
| 18 | ZID-CRYPTO-MI-001 | Cryptography, Key Management & Machine Identity |
| 19 | ZID-TENANCY-001 | Multi-Tenant, Regional Cell, Residency & Sovereignty |

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-F1](#dec-f1-evidence-privacy-pseudonymous-receipts-with-crypto-erasure) | Evidence privacy: pseudonymous receipts with crypto-erasure | Evidence Lead + Privacy/DPO | Proposed |
| [DEC-F2](#dec-f2-evidence-ledger-and-integrity-profile) | Evidence ledger and integrity profile | Evidence Lead + Security Lead | Proposed |
| [DEC-F3](#dec-f3-legal-role-and-establishment-model) | Legal role and establishment model | General Counsel + DPO; engineering co-owner: Privacy Engine… | Proposed |
| [DEC-F4](#dec-f4-operator-access-zero-standing-privilege-jit-lockbox-and-break-glass) | Operator access: zero standing privilege, JIT, lockbox and break-glass | Security Lead + SRE Lead | Proposed |
| [DEC-F5](#dec-f5-key-management-and-secrets-implementation) | Key management and secrets implementation | Security / Cryptography Lead | Proposed |
| [DEC-F6](#dec-f6-post-quantum-cryptography-migration) | Post-quantum cryptography migration | Security / Cryptography Lead | Proposed |
| [DEC-F7](#dec-f7-workload-identity-spiffespire-and-service-mesh-istio-ambient) | Workload identity (SPIFFE/SPIRE) and service mesh (Istio ambient) | Machine Identity Lead + Platform Lead | Proposed |

---

### DEC-F1: Evidence privacy: pseudonymous receipts with crypto-erasure

- **Status:** Proposed
- **Owner:** Evidence Lead + Privacy/DPO (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-06, §13; closes P0-06
- **Sources:** Back-End Target Architecture §13.1–13.2; Decision Evidence spec §0.2, §1, §3, §13; Consent/Privacy/Retention spec (retention actions, `backup_erasure_window`); Cryptography spec (SUBJECT_ERASURE_KEY)

#### Context
P0-06: append-only evidence conflicts with the right to erasure, because immutable logs can keep personal data forever. Evidence is a moat only if it doesn't become an irreversible privacy liability.

#### Decision

##### 1. Receipt classes

| Class | Use | Stored |
|---|---|---|
| E0 Aggregate | Low-risk telemetry | Counts and outcome categories; no subject-level receipt |
| E1 Compact | Ordinary decisions | Pairwise pseudonymous handle, decision, action/resource class, policy and relation revisions, reason/obligation codes |
| E2 Standard | Sensitive or privileged decisions | E1 + authority/mandate refs, assurance and risk state, evidence commitments, execution outcome |
| E3 Forensic/replay | R3 and regulated material actions | E2 + deterministic inputs for replay, signed transaction context, mandate reservation/commit proof, tamper-evident chain |

##### 2. Structure, not a data dump
- **Handles are keyed pseudonyms** (HMAC-based, scoped by tenant and evidence domain), **never unsalted hashes** of email, account numbers or other guessable identifiers.
- Receipts hold handles, commitments and revisions. They never hold names, emails, documents, biometrics, prompts, credentials or full payloads.
- **Direct identifiers and raw evidence** (identity documents, biometric templates, provider responses) live in **separate subject-evidence stores**, encrypted with per-subject or narrow-scope `SUBJECT_ERASURE_KEY` data keys.
- The handle-to-person **identity resolver** is a separately encrypted, tightly access-controlled mapping.

##### 3. Erasure
```
Before:  actor_handle → resolvable via protected identity map; supporting evidence decryptable
After eligible crypto-erasure:
         actor_handle opaque / non-resolvable
         record_hash, signatures, policy/authority/revision commitments STILL VALID
         supporting PII ciphertext unreadable / deleted
         replay reports "identity-resolution data unavailable by lawful erasure"
```
- Erasure deletes the subject mapping and **destroys the relevant keys, after legal-hold evaluation**.
- **Legal holds** are explicit objects (basis, approver, scope, start/end, audit receipt). They never silently turn into indefinite retention.
- Crypto-shredding is **not claimed** while any plaintext copy remains elsewhere.

##### 4. Backups
- Backups are encrypted and covered by key destruction **plus a bounded physical expiry**. An ordinary restore must not bring erased personal data back.
- **The privacy SLO states two numbers per retention class:**
  1. the time until personal data is cryptographically inaccessible
  2. the maximum backup media expiry window (`backup_erasure_window`)

  Privacy/DPO sets the values per retention class before G4 (they are not fixed in the specs).

#### Acceptance (P0-06)
- After erasure, the PII behind a receipt can't be resolved within the SLO, **and** E3 structural replay still passes.
- Restore tests prove erased subjects stay erased once restored from backup (Readiness plan: erasure and crypto-shredding verification, including backups).

#### Consequences
- The `evidence` module owns the identity resolver and the subject-evidence vault. `zid-crypto` manages the per-subject keys.
- Analytics never mines the evidence ledger by default.

---

### DEC-F2: Evidence ledger and integrity profile

- **Status:** Proposed
- **Owner:** Evidence Lead + Security Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-F1; Decision Evidence spec §5–6, §10–12; closes architecture open question **Q5** (anchoring target)
- **Sources:** Decision Evidence, Audit & Provenance spec §0.2, §5, §5.1, §6, §10, §11, §11.1, §12; Cryptography spec (EVIDENCE_SIGNING key class); Deployment spec §23 (TSA isolation)

#### Context
A database administrator must not be able to rewrite E2/E3 history undetected. The spec rejects "blockchain theater" in favor of a controlled ledger with cryptographic checkpoints. Architecture §21 Q5 asked what external anchoring target to use; the spec answers it.

#### Decision

##### 1. Canonicalization, hashing and signatures

| Function | Profile |
|---|---|
| JSON canonicalization | **RFC 8785 JCS**; reject ambiguous inputs |
| CBOR evidence | Deterministic CBOR + COSE, only where binary interop requires it |
| Record digest | **SHA-256 minimum**; the algorithm ID is stored with every digest (no implicit global hash) |
| JSON signature | JWS (detached or embedded, per export contract) |
| CBOR signature | COSE_Sign1 |
| Checkpoint signature | Dedicated checkpoint signing key, separate from OAuth/ID-token keys |
| External timestamp | **RFC 3161 (as updated by RFC 5816)**: optional by default; required for selected E3 export, legal or archival profiles |

##### 2. Append-only ledger (in PostgreSQL, `evidence` schema)
```
evidence_ledger_entry: partition_id, sequence_no, receipt_id/audit_id, record_hash,
                       previous_entry_hash, committed_at, evidence_class, signer_key_id,
                       entry_signature (profile-dependent)
checkpoint:            checkpoint_id, partition_id, first_sequence, last_sequence, merkle_root,
                       previous_checkpoint_hash, created_at, signer_key_id,
                       checkpoint_signature, external_timestamp_ref (optional)
```
- **Partitions:** a ledger per tenant and cell by default. A separate **platform-security partition** holds Zoiko operator and security evidence.
- **Sequence:** monotonic within a partition. Ordering comes from sequence plus revisions, never wall-clock alone.
- **Checkpoint cadence:** risk- and volume-based. E3 checkpoints are more frequent than archival E1.
- **Checkpoint retention:** at least as long as the longest-retained evidence that relies on them.
- **Proofs:** inclusion proofs always; consistency proofs (a later checkpoint extends the earlier history) where implemented.
- **Runtime:** the `evidence-checkpointer` runs in the platform image's `worker` process (`python -m zid_platform.entrypoints.worker`, DEC-D6).

##### 3. Anchoring target (resolves Q5)
- **No public blockchain.**
- Defensibility comes from three layers:
  - signed Merkle checkpoints under HSM-protected keys
  - **checkpoint witnesses / independent retention:** tenants can obtain signed checkpoint manifests where contracted
  - **optional RFC 3161 TSA** timestamps, with TSA trust configured per jurisdiction or profile
- A TSA outage queues anchoring within bounds. It blocks sealing only where the E3 profile requires a synchronous timestamp.
- SCITT compatibility may be added later through an adapter. The ledger never depends on unfinished SCITT drafts.

##### 4. Time integrity
- `committed_at` is the evidence service's trusted UTC commit time. Application timestamps never replace it.
- `effective_at`, `observed_at` and `source_effective_at` are kept as separate fields.
- If clock sync drifts beyond tolerance, **new E3 sealing is blocked** (degraded/fail-closed per profile).

##### 5. Keys and rotation

| Key | Use |
|---|---|
| Record-signing key | Signs selected E2/E3 records and exports |
| Checkpoint-signing key | Signs Merkle/checkpoint manifests (separate policy) |
| Export-sealing key | Signs customer/auditor bundles |

- All three are HSM-protected (DEC-F5).
- Historic public keys are **retained for verification** for the life of the evidence.
- Rotation uses overlap with explicit key IDs and activation/retirement timestamps.
- **Compromise response:** emergency revoke/rotate, an incident annotation, and re-attestation of unaffected commitments. **History is never silently re-signed.**
- Hash and PQ migration happens through new attestations over existing digests, without rewriting payloads (DEC-F6 PQC-2).

##### 6. Independent verification
An exported E3 bundle contains:
- the receipt
- the canonicalization and hash profile
- the signature chain
- the checkpoint and inclusion proof

A verifier can validate it **without write access to Zoiko production**.

#### Acceptance
- Tampering with any ledger row, checkpoint or archived package is detected.
- An archived package restores with an identical digest, signature and checkpoint proof.
- Evidence verifies across a key rotation and a hash migration.
- A replay mismatch for E3 raises a security incident.

#### Consequences
Open question Q5 is closed. The architecture doc §13 and §21 are updated to point here.

---

### DEC-F3: Legal role and establishment model

- **Status:** Proposed. **Legal must own the decision; engineering owns the mechanism.**
- **Owner:** General Counsel + DPO; engineering co-owner: Privacy Engineering Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-07, §18; closes P0-08
- **Sources:** Back-End Target Architecture §18.1–18.3; Consent/Privacy/Retention spec §1 (Controller/Processor Operating Model, role map record); Readiness plan §24.1 (no-go conditions); Multi-Tenant/Residency spec (jurisdiction packs)

#### Context
P0-08: certification, liability and legal roles can't be established from feature lists. Zoiko iD cannot carry one universal "processor" label. The same data can be processed under different roles for different purposes.

#### Decision

##### 1. Roles are assigned per processing activity, not per tenant
Every service flow records:
- who determines purpose and means
- the source of identity evidence
- the recipient
- the retention owner
- the legal basis context

| Zoiko role | When |
|---|---|
| Controller | Zoiko determines purposes and means (direct Zoiko service, platform security/evidence) |
| Processor | The enterprise customer determines purpose; Zoiko acts on instructions (DPA) |
| Independent controllers | Zoiko and another party each determine separate purposes |
| Joint controllers | **Only where counsel determines it**; responsibilities allocated and surfaced |
| Subprocessor / recipient | Downstream providers and relying parties, with minimum disclosure and purpose binding |

##### 2. Role map record (the deployable artifact)
```
processing_role_assignment
  processing_activity_id   tenant_id / product   data_controller_principal_id
  zoiko_role: CONTROLLER | PROCESSOR | JOINT_CONTROLLER | INDEPENDENT_CONTROLLER
  processor_principal_ids[]   recipient_classes[]   jurisdiction_pack_id
  contract_or_dpa_ref   effective_from / valid_to   approval_ref
```

##### 3. Jurisdiction packs
- Counsel approves the legal classification as a **versioned jurisdiction pack**, which becomes deployable policy and configuration.
- **Engineers never infer the legal answer from a country code.**
- Packs set rights, retention and transfer rules. They are versioned independently of the code.

##### 4. Flows that must be assessed (Architecture §18.1)

| Flow | Likely posture | Question counsel must answer |
|---|---|---|
| Enterprise authentication overlay | Processor in many deployments | Does Zoiko determine any independent fraud/security purpose? |
| Reusable verification | May create independent-controller or statutory duties | Who may rely, who is liable for stale evidence, and what is retained? |
| Credential issuance | Issuer role plus controller/processor duties by scheme | What authority permits issuing the claim? |
| EUDI relying party | Verifier obligations | Registration, requested attributes, purpose limitation, logging |
| Agent mandate / authority evidence | Customer-directed plus Zoiko security/evidence purposes | How is representation established, and who bears transaction liability? |
| Security telemetry | Zoiko controller interests may apply | Minimize PII; separate the security purpose from analytics |

##### 5. Reliance and establishment
- **Reliance:** no "verified once, trusted forever" claim. Every assertion has scope, assurance profile, source, `performed_at`, `valid_until` and refresh semantics.
- **AML reliance:** contracts define who performed CDD, evidence access, refresh duties, liability and jurisdictions. **EU AMLR 2024/1624 applies from 10 July 2027**, and the packs must be ready before customers rely on Zoiko for it.
- **Establishment:** entity or establishment decisions are taken **only where market law or a scheme requires them**. No assumption is taken from proposals; for example, the European Business Wallet Regulation is still a proposal.

#### Engineering enforcement
- A processing activity with **no approved role assignment cannot go to production**. CI and release checks verify that every launch flow has a `processing_role_assignment`.
- Readiness no-go condition: an unknown or untested controller/processor role for a launch-tenant flow blocks G4.

#### Acceptance (P0-08)
A counsel-approved role map and a dated market/certification plan exist before regulated production.

#### Consequences
- `privacy` owns the role map and jurisdiction pack registry.
- The packs themselves are Legal's deliverable, and are open question Q7 in the architecture doc.

---

### DEC-F4: Operator access: zero standing privilege, JIT, lockbox and break-glass

- **Status:** Proposed
- **Owner:** Security Lead + SRE Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture P0-07 ("no human holds standing production data access"); Security spec §11–13
- **Sources:** Security Architecture, Threat Model & Operator Trust spec §11, §12, §13; Multi-Tenant/Residency spec §19 (operator sovereignty); UI/UX Wireframes §21 (Operator Console); Data Model §12.2 (`breakglass_dba`)

#### Context
Insiders, coerced operators and compromised support tooling are named adversaries. Zoiko staff must be able to operate the platform without becoming an implicit trust bypass.

#### Decision

##### 1. Zero standing production privilege
Operators have no routine access to customer data or unrestricted infrastructure administration. All privilege is **requested for a purpose, approved, time-bounded, scoped, attributable and auto-revoked**.

##### 2. OPS service (`operator_access` module)

| Control | Implementation |
|---|---|
| Workforce identity | Dedicated workforce IdP identity, a phishing-resistant passkey, managed-device posture, and a separate privileged identity where required |
| JIT request | Case/ticket, reason, exact tenant/environment/resource, capability and duration |
| Approval | Independent approver; **two-person control** for R3, security, key and data-export operations |
| Customer lockbox | Customer approval for customer-data access where required or contracted; scoped and time-bound |
| Session brokerage | Ephemeral privileged session through a controlled proxy or bastion; no credential export, no shared credentials |
| Evidence | Every protected action is audited (E1/E2/E3 by sensitivity) with before/after commitments, without capturing customer content unnecessarily |
| Auto-revoke | Privilege expires automatically; no open-ended extension |
| Break-glass | Named emergency role, narrow scope, immediate Security notification, **mandatory post-review**, region/profile constraints still apply |

##### 3. Flow
```
request (identity + device + passkey + case + scope + duration)
  → policy check: ordinary → independent approver
                  customer data → + customer lockbox
                  R3 / key / evidence admin → dual approval, separated duties
                  emergency → break-glass policy
  → ephemeral capability (audited, continuously re-checked against security state)
  → automatic expiry / explicit revoke → review + evidence receipt
```

##### 4. Separation of duties
- Production admin, evidence admin, key admin and security-policy capabilities are **never concentrated** by default.
- Key control doesn't allow evidence deletion.
- Support can't create tenant roles or grants.
- Commercial status can't override trust decisions.
- Incident response can contain systems but cannot delete evidence.

##### 5. Hard rules
- **Impersonating a customer user is prohibited.** Troubleshooting uses explicit support context.
- **No direct database mutation** as an operator path. Operator actions go through protected service APIs.
- `breakglass_dba` is JIT-only, with captured sessions.
- The Operator Console is a **separate origin, OIDC client and identity policy** (DEC-H1).
- SRP-2/3 tenants may restrict operator jurisdiction and location (DEC-G1).

#### Acceptance
- No production role grants standing data access (verified by an access review).
- A JIT grant expires on time.
- Data access without the lockbox is denied where it's required.
- Break-glass triggers an alert and the review workflow.
- Readiness plan: protected administrator and customer-lockbox control tests pass.

#### Consequences
The OPS module and `operator-console` are W1/W2 deliverables, because controlled production (G4) requires them.

---

### DEC-F5: Key management and secrets implementation

- **Status:** Proposed
- **Owner:** Security / Cryptography Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** CRYPTO-MI-001 §1–3, §6, §9–10, §15–16, §20–23 on AWS (DEC-G2); supports DEC-F1, DEC-F6, DEC-F2
- **Sources:** Cryptography, Key Management & Machine Identity spec (ZID-CRYPTO-MI-001) §0.2, §1, §1.1, §2, §3, §9, §10, §15, §16, §20, §23; Multi-Tenant/Residency spec (key boundaries)

#### Context
The crypto spec fixes the hierarchy (K0–K5), key classes, envelope encryption, CMK/BYOK/HYOK modes and secret delivery, but not the products. Architecture §14.3 mapped them to AWS without a recorded decision.

#### Decision

##### 1. Crypto broker: the only application interface
`zid-crypto` is the **only** way application code uses keys. It validates purpose and context, enforces the Crypto Policy Registry allowlists, and calls KMS/HSM. No service calls KMS directly.

##### 2. Hierarchy on AWS

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

##### 3. Envelope encryption contract
- **AES-256-GCM** with unique nonces.
- **AAD** = `tenant | object_type | object_id | field/purpose | schema_version`, and the KMS encryption context matches it.
- **Stored with the ciphertext:** nonce, tag, wrapped DEK, key version and algorithm.
- **Read path:** authorize first, then unwrap under the same context, then decrypt, then zeroize the key material.

##### 4. Customer key modes

| Mode | Notes |
|---|---|
| `PLATFORM_MANAGED` | Default |
| `DEDICATED_TENANT_KEY` | One KEK per tenant |
| `BYOK` | Imported key material, with documented provenance and expiry |
| `HYOK` | KMS external key store. Its availability becomes part of the SLO; it **fails closed** with no platform fallback. |

The UI and API require protected confirmation for destructive customer-key actions, because **key loss means data loss**. Platform root, signing, CA and evidence keys are never exportable.

##### 5. Secrets (OpenBao)
- **OpenBao** per cell is the Secrets Broker.
- Workloads authenticate with their SPIFFE identity (DEC-F7) and get **short-lease dynamic credentials**, for example a PostgreSQL role per service (DEC-D1).
- Secrets are referenced by logical name and kept in memory. Values are **never** stored in code, images, Git, OpenTofu state, logs, traces, tickets or chat.
- External provider secrets are rotated, and replaced by OAuth/mTLS/`private_key_jwt` where the provider supports it.
- **No developer access to production secrets.** Troubleshooting uses JIT/lockbox (DEC-F4).

##### 6. Ceremonies and dual control

| Operation | Control |
|---|---|
| Routine service keys | Automated rotation by service identity |
| Tenant BYOK/dedicated keys | Step-up + second approval for destructive actions |
| Root CA and evidence root | HSM ceremony, M-of-N custodians, `KeyCeremonyRecord` evidence |
| Key destruction | Dependency/legal-hold check + dual approval (K1/K2/high-impact) + destruction receipt |

Key administrators have **no standing data access** and cannot decrypt tenant ciphertext through the console.

##### 7. Rotation and DR
- No flag-day rotations. Rotation overlaps, and rewrapping happens in the background.
- DR keys are pre-provisioned in the approved recovery cell per residency (DEC-G1). **No emergency key export** to files.

#### Acceptance
- No plaintext KEK or long-lived DEK exists outside KMS/HSM.
- An AAD mismatch fails decryption.
- Crypto-erasure (destroying a SUBJECT_ERASURE_KEY) makes the data unreadable, including from backups (DEC-F1).
- A HYOK outage fails closed.
- The key inventory is complete, including algorithm and PQ-readiness (DEC-F6 PQC-0).

#### Consequences
Any provider gap for PQ algorithms is absorbed inside `zid-crypto` (DEC-F6).

---

### DEC-F6: Post-quantum cryptography migration

- **Status:** Proposed
- **Owner:** Security / Cryptography Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-09, §15.2
- **Sources:** Back-End Target Architecture §15.2; Cryptography, Key Management & Machine Identity spec §0.2, §12–13 (ZID-CRYPTO-MI-001)
- **Note:** ZID-CRYPTO-001 (Secrets spec, 6 Oct) and ZID-CRYPTO-MI-001 (7 Oct) have **identical** PQ sections. CRYPTO-MI-001 looks like a superset; which one is authoritative is open question Q2.

#### Context
As of October 2026:
- hybrid post-quantum TLS is standardized (RFC 10024)
- ML-DSA has standard JOSE/COSE and X.509 encodings
- NIST SP 800-227 is final

Evidence and credentials live a long time, so harvest-now-decrypt-later and future signature forgery risks are real for them. The crypto spec therefore moves from "crypto-agility only" to a controlled production migration.

#### Decision

##### 1. Crypto-agility is mandatory now
- Algorithms and key versions are **metadata**, never hard-coded schema assumptions.
- Every algorithm is chosen from a centrally versioned allowlist (the Crypto Policy Registry). Untrusted `alg` or header input never selects behavior.
- All keys and certificates are inventoried with algorithm and PQ-readiness.

##### 2. Migration waves

| Wave | Scope | Position |
|---|---|---|
| **PQC-0 Inventory** | Every asymmetric key exchange and signature, library, certificate, token and long-retention data path | **Mandatory now (W0)** |
| **PQC-1 Hybrid transport** | RFC 10024 TLS 1.3 hybrid groups on supported internal and edge stacks; measure fallback | Production target 2026/27 |
| **PQC-2 Long-lived signatures** | ML-DSA-65 (or approved profile) for evidence, checkpoints and selected issuer artifacts; **dual-sign** where classical compatibility is needed | **Priority**: evidence and credentials are long-lived |
| **PQC-3 Certificates/PKI** | Pilot RFC 9881 ML-DSA and RFC 9935 ML-KEM certificates only where the ecosystem supports them | No forced public-Web breakage |
| **PQC-4 Token ecosystems** | Keep short-lived OAuth JWT interoperability; add PQ-capable JOSE paths | Migrate when counterparties support it |
| **PQC-5 Additional algorithms** | Track HQC and FN-DSA (FIPS 206) | No production requirement until final and mature |

##### 3. Hybrid TLS policy
- **Preferred group:** `X25519MLKEM768`. `SecP256r1MLKEM768` is the FIPS-oriented option.
- **Fallback** to classical key exchange happens only when the profile permits, and is measured. Regulated high-value profiles may require hybrid.
- **Pre-standard Kyber code points are disabled.**
- **Prioritization:** harvest-now-decrypt-later risk ranks which flows move to hybrid transport first (long-retention identity, authority, evidence, regulated data).

##### 4. Signing posture

| Artifact | Algorithm posture |
|---|---|
| First-party OAuth/OIDC tokens | ES256 baseline; EdDSA in approved non-FIPS ecosystems; RS256 only for compatibility; never HS* for cross-party tokens |
| Evidence / checkpoints | Strong classical signature + ML-DSA migration / dual-sign |
| Verifiable credentials | Determined by the credential profile; issuer keys isolated per trust profile |
| Webhooks | RFC 9421 asymmetric profile |

#### Implementation note (AWS, DEC-G2)
Confirm ML-DSA support in AWS KMS and CloudHSM, and hybrid TLS support in Envoy, Istio and the Python TLS stack, before PQC-1 and PQC-2 start. Where a provider lacks support, the crypto broker (`zid-crypto`) isolates the gap. No algorithm is ever hard-coded into callers.

#### Acceptance
- The PQC-0 inventory is complete and kept current.
- Evidence verification succeeds across an algorithm rotation, including dual-signed artifacts.
- Hybrid-to-classical fallback is observable in telemetry.

#### Consequences
- Evidence checkpoint signing is the first PQ production use (W1/W2 timeframe).
- Token ecosystems follow counterparties, not Zoiko's schedule.

---

### DEC-F7: Workload identity (SPIFFE/SPIRE) and service mesh (Istio ambient)

- **Status:** Proposed
- **Owner:** Machine Identity Lead + Platform Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Engineering Brief non-negotiable "no static machine secrets"; AI Agent & Workload Identity spec §5; Cryptography & Machine Identity spec §24–29; Service Decomposition spec §10
- **Sources:** AI Agent spec §4–6, §8, §18; CRYPTO-MI-001 §25 (SPIFFE profile), §27 (mTLS/SVID/PKI), §29 (revocation/quarantine/federation); Service spec §10, §23

#### Context
The specs require short-lived workload identity, mTLS on critical paths and automatic rotation, with no static secrets. The spec calls SPIRE "an implementation option, not hard-coded into the domain model", so the choice is recorded here.

#### Decision

##### 1. Implementation
- **SPIRE** server per cell, with an agent on every node, issuing **X.509-SVIDs** for mTLS. **JWT-SVIDs** are audience-bound, for JWT-oriented integrations.
- **Istio ambient mode** uses the SPIFFE identities for mTLS on every service hop, plus authorization policies that allow only the dependency graph declared in architecture §6.1.
- **Cloud API access** uses EKS Pod Identity. No static AWS keys.

##### 2. Trust domains
- **One governed trust domain per cell and environment**, for example `spiffe://uk-1.prod.id.zoiko`. Sovereign or dedicated cells get their own. Names are governed, never ad hoc.
- **Federation across trust domains** requires an explicit federation policy. There is **no trust-on-first-use** for production R2/R3.
- **Intermediate CAs** are scoped to the cell and environment, with non-exportable keys in HSM/KMS (DEC-F5). Trust bundles are versioned and independently rotatable.

##### 3. Mapping invariant (an SVID authenticates; it never authorizes)
```
SPIFFE ID / SVID → WorkloadIdentity → logical principal (SERVICE / WORKLOAD / AI_AGENT)
                 + environment + artifact/selector evidence + current security_revision
                 → OAuth token exchange or direct mTLS → authority + mandate + policy evaluation
```
- The SPIFFE ID maps to a registered workload binding (MACH module). The URI alone is not business authority.
- Never authorize from a certificate CN or display name.
- Service credentials never impersonate end users. A represented actor travels separately (TCE, DEC-D3).

##### 4. Rotation and revocation
- SVIDs are short-lived and auto-renewed before expiry, with hot reload and bounded overlap.
- If rotation fails, alert before the safety window ends. There is no fallback to a shared certificate, and no extending timestamps by hand.
- For ordinary ephemeral SVIDs we avoid depending on CRL/OCSP. **Urgent compromise is enforced through `security_revision` and workload quarantine** (SIG), on the R3 revocation SLO.

##### 5. Network
- Network location is never identity (Zero Trust).
- Egress to registries, IdPs, verification vendors and webhooks goes through allowlisted, proxied integration egress, which blocks SSRF.

#### Acceptance
- A pod without a valid SVID can't call any internal API.
- A call outside the declared dependency graph is rejected by mesh policy.
- A quarantined workload is denied within the R3 SLO.
- Compromise of one cell's trust domain doesn't authenticate in another.

#### Consequences
- Local development uses a SPIRE dev profile in docker-compose.
- `zid-context` reads the caller identity from the mesh-verified peer identity, never from headers.
