# CR-011: Evidence ledger, receipts and the subject-evidence vault

- **Target:** ZID-DATA-001 §9.2-9.3; ZID-EVIDENCE-001 §3, §7, §10, §13-14, §18, §20, §22-23; action catalogue · **Raised by:** Phase F W1/W2 (DEC-F1, DEC-F2) · **Status:** Open

## Tables (`evidence` schema, owner: evidence)

| Table | Spec | Change |
|---|---|---|
| `ledger_entry`, `checkpoint` | EVIDENCE §10 | Adds `tenant_id` (RLS), `record_type`, `entry_hash`, `effective_at`/`observed_at`, `checkpoint_hash`, `alg`, `signer_public_jwk`. The spec's `receipt_id / audit_id` is the generic `(record_type, record_id)` pair, so receipts, audit records, annotations, custody records and erasure tombstones share one ledger. Every digest is `<alg>:<hex>`. |
| `decision_receipt` | DATA §9.2 | `actor_handle` / `represented_handle` are keyed-HMAC text (`ph1.<key version>.<mac>`), not uuid: a handle is recomputable under the key, needs no lookup table, and makes rotation explicit. `policy_release_id`, `relation_revision`, `security_revision` are nullable: a decision denied for lack of a release or an unavailable fact still leaves a receipt. `body` holds exactly the JCS-hashed receipt; `material_object_id` points at the E3 material inputs. |
| `audit_record` | EVIDENCE §7 | New table (the spec defines the record, not its table). Platform-security rows have `tenant_id` NULL. `source_event_id` makes outbox ingestion effectively-once. |
| `receipt_annotation` | EVIDENCE §20 | New: correction, supersession, annotation, invalidation as linked append-only records. |
| `custody_record` | EVIDENCE §18 | New: chain of custody for exports and re-identification. |
| `erasure_tombstone` | PRIV-001 §10; DEC-F1 §4 | New: what was erased, replayed on restore. |
| `subject_key` | DEC-F1 §2 | New: per-subject AES-256 key, stored only wrapped under the cell `SUBJECT_ERASURE_KEY`; erasure removes the only wrapped copy. |
| `subject_handle_map`, `evidence_object` | DATA §9.3 | `subject_ref` (keyed handle) replaces `subject_key_ref`; objects carry ciphertext in-row (`object_uri` `pg:...`) until the object store adapter lands; `status` STORED / ERASED / EXPIRED. |
| `legal_hold` | EVIDENCE §22; PRIV §11 | New: scoped (subjects and/or object types, never tenant-wide), approver ≠ requester, review date, optional expiry. One hold model serves evidence and privacy retention. |
| `decision_aggregate` | EVIDENCE §1 (E0) | New: E0 counts only, no subject-level receipt. |

Immutable tables refuse UPDATE/DELETE/TRUNCATE by trigger for every role. The vault tables move one way only (`evidence.no_resurrection`).

## Actions (`name_source: proposed`, `note: "CR-011"`)

| Action | Floor | Endpoint |
|---|---|---|
| `evidence.read` | R1/E1 | `GET /v1/evidence/receipts`, `/audit-events` |
| `evidence.verify` | R1/E1 | `POST /v1/evidence/receipts/{id}:verify`, `/proof`, `/partition:verify` |
| `evidence.replay` | R2/E2 | `POST /v1/evidence/receipts/{id}:replay` |
| `evidence.annotate` | R2/E2 | `POST /v1/evidence/receipts/{id}:annotate` |
| `evidence.identity.resolve` | R3/E3 | `POST /v1/evidence/handles:resolve` (case-bound, custody-recorded) |
| `evidence.legal_hold.manage` | R3/E3 | `/v1/evidence/legal-holds` (create needs a second administrator's approval) |
| `evidence.object.read` | R3/E3 | raw supporting evidence (purpose-bound) |

## Write path

Receipts and audit records are written in the producer's own transaction (they commit iff the domain change does); the worker sequences them into the hash chain (E3 entries carry their own signature) and seals signed Merkle checkpoints on a risk-based cadence. This keeps the per-partition ledger lock out of the PDP request path (EVID-24, EVID-30).

## Open questions for the spec owners

1. Accept text handles in place of uuid `actor_handle` (DATA §9.2).
2. Accept nullable `policy_release_id` for receipts of decisions taken without a release.
3. Confirm that `legal_hold` lives in `evidence` and privacy retention consults it through `evidence.ports.is_held`.
