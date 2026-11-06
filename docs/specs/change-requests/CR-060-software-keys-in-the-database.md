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

## Not changed
KMS providers, the crypto policy registry, the envelope format of data (`seal`/`open`), the action catalogue, outbox events, tenant tables. No tenant data is stored in the key tables.

## Owner decisions (docs/signoff)
1. Accept the exception to DEC-F5 for this deployment, in writing, with the risks above.
2. Where the master key lives and who holds the offline copy (two people).
3. The backup retention that bounds the erasure promise.
4. Whether a customer-facing statement about key custody changes ("keys in a hardware module" must not be claimed).

## Tests
`libs/zid-crypto/tests/test_software_keys.py` (wrapping, binding, races, rotation, erasure, master rotation, signers), `app/tests/component/test_software_keys_pg.py` (the real table and trigger, least-privilege roles, races across threads, the acknowledgement gate, every key of a production configuration, the global plane's own table).
