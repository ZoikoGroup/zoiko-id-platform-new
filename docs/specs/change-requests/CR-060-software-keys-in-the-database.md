# CR-060: Software keys held in the database (standard assurance), an amendment to DEC-F5

Status: open (built). **Needs the Security / Cryptography Lead's written acceptance before use in a production environment.** Without it the deployment keeps `ZID_KEY_PROVIDER=gcp-kms` or `aws-kms`.

## Why
DEC-F5 requires every platform key to live in a KMS/HSM and refuses the local provider outside local and test. A deployment on plain virtual machines, with no Google Cloud KMS and no AWS KMS, therefore cannot start in a deployed environment. The owner chose to run on VMs with all stored data in PostgreSQL. This change adds a third, lower-assurance way to hold keys so that such a deployment is possible, explicitly and on the record, without weakening the two existing ones.

## What changes
- **A new provider, `ZID_KEY_PROVIDER=db`** (`zid_crypto.software_keys`, wired in `zid_platform.crypto_runtime`). Every key is a row in `keys.key_version` (a cell) or `global_directory.key_version` (the global plane), encrypted with AES-256-GCM by a **master key held outside the database** (`ZID_KEY_MASTER_FILE`, one `<id> <base64>` per line, first is current). The associated data of each row is its key id, version, purpose and algorithm, so a row moved to another key does not unwrap.
- **What it covers, through the same interfaces as the KMS providers:** the data, subject-erasure, backup and secret KEKs; the HMAC key for pseudonymous handles; the signing key rings (token, set, federation, credential, verifier); the evidence signing key; the directory, webhook, discovery and company-evidence signers. Keys are created on first use; racing processes converge on one row (insert-or-adopt).
- **Rotation and erasure:** `rotate_key` makes a new ACTIVE version and RETIRES the old one (still unwraps); signing-key rotation keeps the retired public keys for the verification window (`retired_public_jwks`). `destroy_key` overwrites the stored material (`wrapped` becomes NULL, enforced by a check constraint) and marks the row; the trigger lets a destroyed key stay destroyed.
- **The table is append-only apart from the lifecycle** (trigger): no delete, no change of identity or public key, no reactivation of a retired key, and destruction is final. The runtime and worker roles get SELECT, INSERT and UPDATE only (`deploy/gcp/db/02_grants.sql`).
- **Master key rotation** without downtime: a new first line in the file, `keys rewrap`, then remove the old line.
- **Operator tool:** `python -m zid_platform.entrypoints.keys` (`init-master`, `status`, `public`, `rotate-signing`, `rewrap`). It never prints key material.
- **Migrations:** stream `keys` (`keys_0001`, a platform stream like `outbox`) and `locator_0002`. New schema `keys` in the cell bootstrap and readiness checks.
- **A deployed environment refuses to start the provider** unless `ZID_KEY_PROVIDER_DB_ACK=accept-software-keys` is set, the same pattern as the operator direct-mode acknowledgement.

## What it protects, and what it does not
- Protects against: a stolen database, backup, snapshot or replica on its own (useless without the master key); a row altered or moved inside the database (the associated data fails); silent rewriting of key history (the trigger).
- **Does not protect against:** anyone who controls a running platform process or the host that holds the master key file: plaintext keys exist in process memory and the master key sits on the same machine as the database credentials. A KMS/HSM does not have this weakness.
- **Not FIPS-validated** (`fips_validated = False`): operations marked FIPS-required refuse this provider by design.
- **Erasure depends on the master key as well as the row:** a backup taken before a key was destroyed still holds its wrapped material. The erasure promise to a data subject therefore holds only once every backup wrapped under the same master key has aged out or the master key is retired (`docs/runbooks/software-keys.md`).
- **Evidence integrity:** the evidence signing key is now reachable by someone with the host. The mitigation is the external timestamp on each checkpoint (`ZID_EVIDENCE_TSA_URL`, CR on RFC 3161): a history re-signed after a key leak will not match what the timestamp authority stamped. Production use should therefore also configure a TSA.

## Addendum: closing the audit gaps this mode makes more important
- **Scheduled integrity verification.** The evidence worker re-verifies each partition's hash chain, checkpoint roots, links, signatures and external timestamps every `ZID_EVIDENCE_VERIFY_INTERVAL` (default 6 hours), at most `ZID_EVIDENCE_VERIFY_BATCH` partitions per pass. A failure raises the existing `evidence.integrity.failed` incident (an email to the SECURITY role) once per partition until it verifies clean, and is counted. Before this, tampering was found only if someone called `partition:verify`.
- **`ZID_EVIDENCE_TSA_REQUIRED` now works.** It was read by nothing. With it set, a checkpoint is not sealed without an external timestamp; the entries stay visibly unsealed and the worker retries.
- **A deployed cell on software keys needs a timestamp authority.** Without `ZID_EVIDENCE_TSA_URL` it refuses to start unless the owners accept the exposure in writing with `ZID_EVIDENCE_TSA_WAIVER=accept-no-external-timestamps`: an external timestamp is what exposes a history re-signed with a leaked key.
- **Every key change is audited.** `keys.key_event` (and `global_directory.key_event`) records create, rotate, retire, destroy and re-wrap in the same transaction as the change, with who made it (`process:<role>` or `cli:<user>@<host>`); an append-only trigger protects it. The evidence worker copies each into the ledger (platform-security partition, class E3, `key.*` events, operators as a pseudonymous reference) and stamps the row. The global plane has no ledger, so its table is its audit trail.
- **The revocation transmitter runs on software keys.** The Shared Signals transmitter (continuous revocation push) was off in every deployed cell; with `ZID_KEY_PROVIDER=db` it signs with the same `set` key ring the API publishes. On a KMS it stays off until given the KMS signer.
- **Alerts.** The worker can serve `/metrics` (`ZID_METRICS_PORT`); `deploy/observability/prometheus/rules/zid-evidence.yaml` alerts on a failed or erroring verification, a stalled scheduler, sealing that is behind, and checkpoints without a timestamp.
- Tests: `test_evidence_integrity_pg.py`, the key-event and transmitter cases in `test_software_keys_pg.py`.

Still open: an independent witness copy of the checkpoints (who holds it is a business decision).

## Not changed
KMS providers, the crypto policy registry, the envelope format of data (`seal`/`open`), the action catalogue, outbox events, tenant tables. No tenant data is stored in the key tables.

## Owner decisions (docs/signoff)
1. Accept the exception to DEC-F5 for this deployment, in writing, with the risks above.
2. Where the master key lives and who holds the offline copy (two people).
3. The backup retention that bounds the erasure promise.
4. Whether a customer-facing statement about key custody changes ("keys in a hardware module" must not be claimed).

## Tests
`libs/zid-crypto/tests/test_software_keys.py` (wrapping, binding, races, rotation, erasure, master rotation, signers), `app/tests/component/test_software_keys_pg.py` (the real table and trigger, least-privilege roles, races across threads, the acknowledgement gate, every key of a production configuration, the global plane's own table).
