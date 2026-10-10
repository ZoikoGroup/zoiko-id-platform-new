# ADR-0024: Evidence ledger and integrity profile

- **Status:** Proposed
- **Owner:** Evidence Lead + Security Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0006; Decision Evidence spec §5–6, §10–12; closes architecture open question **Q5** (anchoring target)
- **Sources:** Decision Evidence, Audit & Provenance spec §0.2, §5, §5.1, §6, §10, §11, §11.1, §12; Cryptography spec (EVIDENCE_SIGNING key class); Deployment spec §23 (TSA isolation)

## Context
A database administrator must not be able to rewrite E2/E3 history undetected. The spec rejects "blockchain theater" in favor of a controlled ledger with cryptographic checkpoints. Architecture §21 Q5 asked what external anchoring target to use; the spec answers it.

## Decision

### 1. Canonicalization, hashing and signatures

| Function | Profile |
|---|---|
| JSON canonicalization | **RFC 8785 JCS**; reject ambiguous inputs |
| CBOR evidence | Deterministic CBOR + COSE, only where binary interop requires it |
| Record digest | **SHA-256 minimum**; the algorithm ID is stored with every digest (no implicit global hash) |
| JSON signature | JWS (detached or embedded, per export contract) |
| CBOR signature | COSE_Sign1 |
| Checkpoint signature | Dedicated checkpoint signing key, separate from OAuth/ID-token keys |
| External timestamp | **RFC 3161 (as updated by RFC 5816)**: optional by default; required for selected E3 export, legal or archival profiles |

### 2. Append-only ledger (in PostgreSQL, `evidence` schema)
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
- **Runtime:** the `evidence-checkpointer` runs in the platform image's `worker` process (`python -m zid_platform.entrypoints.worker`, ADR-0031).

### 3. Anchoring target (resolves Q5)
- **No public blockchain.**
- Defensibility comes from three layers:
  - signed Merkle checkpoints under HSM-protected keys
  - **checkpoint witnesses / independent retention:** tenants can obtain signed checkpoint manifests where contracted
  - **optional RFC 3161 TSA** timestamps, with TSA trust configured per jurisdiction or profile
- A TSA outage queues anchoring within bounds. It blocks sealing only where the E3 profile requires a synchronous timestamp.
- SCITT compatibility may be added later through an adapter. The ledger never depends on unfinished SCITT drafts.

### 4. Time integrity
- `committed_at` is the evidence service's trusted UTC commit time. Application timestamps never replace it.
- `effective_at`, `observed_at` and `source_effective_at` are kept as separate fields.
- If clock sync drifts beyond tolerance, **new E3 sealing is blocked** (degraded/fail-closed per profile).

### 5. Keys and rotation

| Key | Use |
|---|---|
| Record-signing key | Signs selected E2/E3 records and exports |
| Checkpoint-signing key | Signs Merkle/checkpoint manifests (separate policy) |
| Export-sealing key | Signs customer/auditor bundles |

- All three are HSM-protected (ADR-0026).
- Historic public keys are **retained for verification** for the life of the evidence.
- Rotation uses overlap with explicit key IDs and activation/retirement timestamps.
- **Compromise response:** emergency revoke/rotate, an incident annotation, and re-attestation of unaffected commitments. **History is never silently re-signed.**
- Hash and PQ migration happens through new attestations over existing digests, without rewriting payloads (ADR-0009 PQC-2).

### 6. Independent verification
An exported E3 bundle contains:
- the receipt
- the canonicalization and hash profile
- the signature chain
- the checkpoint and inclusion proof

A verifier can validate it **without write access to Zoiko production**.

## Acceptance
- Tampering with any ledger row, checkpoint or archived package is detected.
- An archived package restores with an identical digest, signature and checkpoint proof.
- Evidence verifies across a key rotation and a hash migration.
- A replay mismatch for E3 raises a security incident.

## Consequences
Open question Q5 is closed. The architecture doc §13 and §21 are updated to point here.
