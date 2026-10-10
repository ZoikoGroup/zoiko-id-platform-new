# ADR-0006: Evidence privacy: pseudonymous receipts with crypto-erasure

- **Status:** Proposed
- **Owner:** Evidence Lead + Privacy/DPO (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-06, §13; closes P0-06
- **Sources:** Back-End Target Architecture §13.1–13.2; Decision Evidence spec §0.2, §1, §3, §13; Consent/Privacy/Retention spec (retention actions, `backup_erasure_window`); Cryptography spec (SUBJECT_ERASURE_KEY)

## Context
P0-06: append-only evidence conflicts with the right to erasure, because immutable logs can keep personal data forever. Evidence is a moat only if it doesn't become an irreversible privacy liability.

## Decision

### 1. Receipt classes

| Class | Use | Stored |
|---|---|---|
| E0 Aggregate | Low-risk telemetry | Counts and outcome categories; no subject-level receipt |
| E1 Compact | Ordinary decisions | Pairwise pseudonymous handle, decision, action/resource class, policy and relation revisions, reason/obligation codes |
| E2 Standard | Sensitive or privileged decisions | E1 + authority/mandate refs, assurance and risk state, evidence commitments, execution outcome |
| E3 Forensic/replay | R3 and regulated material actions | E2 + deterministic inputs for replay, signed transaction context, mandate reservation/commit proof, tamper-evident chain |

### 2. Structure, not a data dump
- **Handles are keyed pseudonyms** (HMAC-based, scoped by tenant and evidence domain), **never unsalted hashes** of email, account numbers or other guessable identifiers.
- Receipts hold handles, commitments and revisions. They never hold names, emails, documents, biometrics, prompts, credentials or full payloads.
- **Direct identifiers and raw evidence** (identity documents, biometric templates, provider responses) live in **separate subject-evidence stores**, encrypted with per-subject or narrow-scope `SUBJECT_ERASURE_KEY` data keys.
- The handle-to-person **identity resolver** is a separately encrypted, tightly access-controlled mapping.

### 3. Erasure
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

### 4. Backups
- Backups are encrypted and covered by key destruction **plus a bounded physical expiry**. An ordinary restore must not bring erased personal data back.
- **The privacy SLO states two numbers per retention class:**
  1. the time until personal data is cryptographically inaccessible
  2. the maximum backup media expiry window (`backup_erasure_window`)

  Privacy/DPO sets the values per retention class before G4 (they are not fixed in the specs).

## Acceptance (P0-06)
- After erasure, the PII behind a receipt can't be resolved within the SLO, **and** E3 structural replay still passes.
- Restore tests prove erased subjects stay erased once restored from backup (Readiness plan: erasure and crypto-shredding verification, including backups).

## Consequences
- The `evidence` module owns the identity resolver and the subject-evidence vault. `zid-crypto` manages the per-subject keys.
- Analytics never mines the evidence ledger by default.
