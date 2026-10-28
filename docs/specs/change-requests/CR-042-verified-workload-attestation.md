# CR-042: Workload attestation is verified, not claimed

- **Target:** ZID-MACHINE-001 §9 (runtime attestation), §21 (`POST /attestations:verify`), §25 · **Raised by:** Agent-spec review (MACH-12, MACH-26) · **Status:** Open (built)

## Gap

`POST /v1/machine/workload-token` took the attestation level from the request body (`"attestation": 2`). Workload tokens are risk class R2 and the platform's floor for R2 is ATTEST-2 (artifact attestation), so any workload with a valid SVID and a registered binding could simply state that it was attested and receive a token. Attestation is evidence about a runtime; evidence the requester writes about itself is not evidence. The spec's verify endpoint (`/attestations:verify`) and its attestation record (§9.1) did not exist.

## Change

1. **Approved attestors.** `ZID_ATTESTOR_KEYS_FILE` is a JWK set of public keys (ES256 or Ed25519) of the attestors this cell trusts. Private material in the file stops the cell starting. Unset, the verify endpoint answers 404 and no workload can reach a level above 0.
2. **Statement.** An attestor signs a compact JWS of type `attestation+jwt` with `sub` (the workload's SPIFFE ID, which carries the tenant), `environment_id`, `artifact_digest` (`sha256:`), `level` (1 to 3), `iat` and `exp`. Lifetime at most one hour; one minute of clock skew.
3. **`POST /v1/machine/attestations:verify`** `{statement}`. The attestor's signature is the only credential (keys pinned in the file; `jku`, `x5u` and embedded keys are refused). The tenant is read from the signed SPIFFE ID, and the claims are read only after the signature passes. A bad signature is a 400 and leaves no record. A genuinely signed but out-of-date statement is kept as a FAILED record (`ATTESTATION_STALE`, `ATTESTATION_LIFETIME_INVALID`, `ATTESTATION_NOT_YET_VALID`) and answered 422, so the refusal is evidence.
4. **Record.** `agent.workload_attestation` (migration `machine_0004`, tenant RLS): workload, environment, artifact digest, level, verifier key id, a SHA-256 commitment over the claims, status VALID or FAILED, and `valid_until`. Rows are never edited; a statement past its `valid_until` is not counted.
5. **Events and audit.** `workload.attestation.verified` and `workload.attestation.failed` (both already in the event catalogue) are emitted with an audit record.
6. **Token issuance.** The attestation level used for the floor is the highest VALID, in-date record for exactly this workload, environment and artifact digest. The body field `attestation` is kept for older callers, marked deprecated, and ignored.

## Consequence to plan for

Every workload token now needs a real attestor behind it. A deployed workload that used to send `"attestation": 2` will be refused (`ATTESTATION_INSUFFICIENT`) until its attestor signs statements for it and the cell trusts that attestor's key. Local and test setups need the same: an attestor key file and a statement per workload.

## Attestor and ceilings (follow-up in the same change)

- **Per-attestor ceiling.** A key in `ZID_ATTESTOR_KEYS_FILE` may carry `zid_max_level` (default 2). A statement above its signer's ceiling, or from a key the cell does not list, is refused with `ATTESTATION_LEVEL_NOT_PERMITTED` and kept as a FAILED record. Level 3 therefore needs an attestor explicitly cleared for hardware evidence.
- **The attestor itself** (`zid_platform/attestor`, `entrypoints/attestor.py`, design in [workload-attestor.md](../../architecture/workload-attestor.md)) is a separate process with its own key. It reads the runtime (Docker engine, Kubernetes API), compares it to a platform-owned registration file, and signs a statement only for what it observed: level 1 for matching selectors, level 2 for an approved running digest, never level 3 without a hardware verifier. It signs with a KMS key when deployed and renews statements well inside their lifetime.

## Not in this change

- A hardware or TEE verifier (the `HardwareQuoteVerifier` extension point has no implementation), and artifact signature verification such as Sigstore.
- A Kubernetes run against a live cluster: that inspector is tested against the API's response format only. The Docker path is tested against a real container.

Tests: `test_attestor.py` (registration, levels, statement round trip with the cell, inspectors, key handling), `test_workload_attestor_pg.py` (a real container attested end to end; a name or digest mismatch earns less or nothing; a stopped container cannot be renewed; concurrent exchanges), `test_workload_attestation.py` (signature, wrong type, malformed claims, timing, commitment) and `test_workload_attestation_pg.py` (a claim earns nothing; a rogue or too-low or wrong-digest statement does not help; a stale one is kept as a refusal; a real level-2 statement lets the workload in, for that environment only; no record for a forged statement).
