# Workload attestor: design

Implements ZID-MACHINE-001 §9 (runtime attestation) and §21 (`POST /attestations:verify`). Decisions are recorded in [CR-042](../specs/change-requests/CR-042-verified-workload-attestation.md). Code: `backend/app/src/zid_platform/attestor/`, `entrypoints/attestor.py`, and the cell side in `modules/machine`.

## What problem it solves

A workload token is a risk class R2 credential, so the platform requires ATTEST-2 (the running artifact matches an approved digest) before issuing one. That evidence has to come from someone who looked at the runtime. The workload cannot be that someone (it would vouch for itself), and the cell cannot be (a compromised cell could then mint its own evidence).

## Trust model

```
   container engine / cluster API          platform team
            │ (observed facts)                  │ (reviewed change)
            ▼                                   ▼
        ┌─────────────────────────────────────────────┐
        │ ATTESTOR   own process, own key (KMS)       │   never shares a secret or a database with the cell
        │ inspects the runtime, compares to the       │
        │ registration file, signs a short statement  │
        └─────────────────────┬───────────────────────┘
                              │ attestation+jwt  (sub, environment, digest, level, iat, exp)
                              ▼
        ┌─────────────────────────────────────────────┐
        │ CELL  POST /v1/machine/attestations:verify  │   verifies the signature against pinned attestor keys
        │ records VALID or FAILED, emits an event,    │   checks the attestor's level ceiling and the clock
        │ audits it                                   │
        └─────────────────────┬───────────────────────┘
                              ▼
        workload presents its SVID  →  POST /v1/machine/workload-token
        the level used is the highest VALID, in-date record for this workload + environment + artifact
```

Three parties, three keys: the **mesh** proves who the workload is (SVID), the **attestor** proves what it is running (statement), the **cell** decides (binding, authority, policy). None of them can do another's job.

## Levels

| Level | Meaning | Earned when |
|---|---|---|
| 0 | nothing | the object is not running, or does not match every registered selector: no statement is signed |
| 1 | environment | running and satisfies the selectors; the statement names the digest that was actually observed |
| 2 | artifact | level 1 and an observed digest is one the registration approves |
| 3 | hardware / TEE | only when a `HardwareQuoteVerifier` is configured and accepts it. None ships: this attestor stops at 2 |

The cell also pins a **ceiling per attestor key** (`zid_max_level` on its JWK, default 2). A level-3 statement from an attestor not cleared for it is refused and kept as a FAILED record. The level is therefore limited twice: by what the attestor can check and by what the cell allows it to claim.

## The registration file

Owned by the platform team and changed by review, like SPIRE registration entries. Each entry names the SPIFFE ID (which carries the tenant), the environment, the selectors the runtime object must satisfy, the digests it may run, and an optional lower ceiling. A workload never edits it and the attestor never takes a digest or a name from the workload.

The digests are not typed. `python -m zid_platform.entrypoints.attestor_sync --workspace <company> --registrations workloads.json` fills each entry's `approved_digests` from the company's records: a digest is listed only while an ACTIVE workload binding runs a build that a second person approved (CR-064), an entry with none behind it is reported and left as it is, and other companies' entries are never touched. The selectors stay hand-written, because which container is which is operational. The output is still a reviewed change to this file (`--check` exits 1 when it is out of date, for a pipeline).

## Failure and renewal (fail closed)

- A statement lasts at most one hour (the cell's limit); the attestor default is 15 minutes, renewed every 5.
- A stopped, replaced or tampered workload gets no new statement and loses its attestation within the lifetime, then is refused new tokens. A token already issued lasts at most its own five minutes.
- If the attestor is down, statements lapse and workloads stop getting tokens after the lifetime. That is the specified behaviour (MACHINE-001 §26): authority is never granted to keep things running.
- The statement's tenant comes from the signed SPIFFE ID, and the cell checks every claim only after the signature passes. A forged signature leaves no record; a genuine but out-of-date one leaves a FAILED record.

## Threats

| Threat | Why it fails |
|---|---|
| A workload claims a level in its token request | the field is ignored; the level comes from verified records |
| A workload signs its own statement | its key is not in the cell's attestor key file |
| A compromised cell mints evidence | it holds no attestor key |
| An attestor key is stolen | statements last minutes; rotate the key by changing the cell's key file; a deployed attestor signs with KMS and the key never leaves it |
| A statement is replayed after the workload changed | it expires within the lifetime and is bound to one SPIFFE ID, environment and digest |
| A statement for one artifact is used for another | the cell matches the exact digest |
| The registration file is altered | it changes by reviewed change only; entries name selectors and digests, so widening one is visible in review |

## Running it

Development, with a real container:

```
python -m zid_platform.entrypoints.attestor keygen --out attestor.pem --kid attestor-1    # prints the public JWK for ZID_ATTESTOR_KEYS_FILE
python -m zid_platform.entrypoints.attestor run --registrations workloads.json --key attestor.pem --kid attestor-1 \
    --cell http://localhost:8000/v1/machine/attestations:verify --docker payments-agent=spiffe://...
```

Production: one attestor per node or per cluster, signing with `--gcp-kms-key` (or an injected AWS KMS signer), reading pods with its own read-only service account (`--k8s NAMESPACE/POD=SPIFFE_ID`). Where SPIRE issues the SVIDs, its workload-attestor selectors map onto the registration selectors one to one; SPIRE says who the workload is, this says what it is running.

## Not built here

- A hardware or TEE verifier (TPM quotes, SEV-SNP, Nitro, TDX reports). The extension point is `HardwareQuoteVerifier`; without one nothing reaches level 3.
- Artifact signature verification (for example Sigstore). The digest match is against the registration's approved list, which `attestor_sync` fills from the platform's approved builds. A build records a provenance reference, but nothing verifies a signature on it yet.
- A Kubernetes run against a live cluster. The inspector is tested against the API's response format only; the Docker path is tested against a real container.
