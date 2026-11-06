# Runbook: software keys in the database (`ZID_KEY_PROVIDER=db`)

Change record: [CR-060](../specs/change-requests/CR-060-software-keys-in-the-database.md). Read its "what it does not protect" section first.

## Set up (once per cell, and once for the global plane)

1. **Master key.** On the host that runs the platform, as root:
   `python -m zid_platform.entrypoints.keys init-master --file /etc/zoiko/master_key`
   The file is mode 600. **Copy it offline now**, split between two people. Without it every stored key is unrecoverable; with it a copy of the database is readable. Never store it in the database, the repository, the image or a backup that sits beside the database dump.
2. **Settings:** `ZID_KEY_PROVIDER=db`, `ZID_KEY_MASTER_FILE=/etc/zoiko/master_key` (mounted read-only into the containers), and, in a deployed environment, `ZID_KEY_PROVIDER_DB_ACK=accept-software-keys` after the Security Lead has accepted CR-060.
3. **Database:** run the migrations as the owner role (creates `keys.key_version`; the global plane's `global_directory.key_version`), then `deploy/gcp/db/02_grants.sql`.
4. **First start:** keys are created on first use. Run `keys status` to see them; **pin the cell's directory public key in the global plane** with `keys public` (`ZID_LOCATOR_CELL_KEYS`).

## Audit trail

Every create, rotate, retire, destroy and re-wrap is a row in `keys.key_event` (`global_directory.key_event` on the global plane) with the actor (`process:<role>` or `cli:<user>@<host>`), written in the same transaction as the change. The evidence worker copies each into the ledger as a `key.*` event (class E3), so "who rotated the evidence key and when" is answerable from the same hash-chained, timestamped record as everything else. Every change the platform's own code makes has an event. **Residual risk:** the triggers stop the runtime, worker and relay roles, but not the database owner or a superuser: someone with that access could change key rows directly, without an event (the trigger can be disabled, as the tests themselves do). Detection then rests on the external timestamps and the scheduled verification of the evidence ledger, not on the key tables. Keep owner credentials off the app VMs and use them only for migrations.

## Daily

`keys status` lists key ids, versions, states and whether the material is present. It never prints material.

## Rotate a signing key

`keys rotate-signing --key-id signing_token-eu-1`, then restart the processes. The old public key stays published (retired) for the verification window.

## Rotate the master key

1. Add a new `<id> <base64>` line **at the top** of the master file (keep the old line below). Restart the processes.
2. `keys rewrap` re-wraps every row under the new master.
3. Take a fresh backup. Remove the old line only when every backup wrapped under it has aged out or been destroyed.

## Erasing a person (crypto-erasure)

Destroying a subject key wipes the stored material and marks the row. **Backups taken earlier still contain the wrapped key.** The erasure is complete only when those backups are gone or the master key they were wrapped under has been retired (step 3 above). Record the date in the erasure case.

## Lost master key

Stored keys cannot be recovered. Evidence signed with the lost evidence key stays verifiable from its published public keys and external timestamps, but envelope-encrypted data cannot be read. Restore the master key from the offline copy; this is why it exists.

## Suspected host compromise

Treat every key as exposed: rotate all signing keys, move to a new master key, rewrap, rotate the OAuth clients and sessions, and compare checkpoints against the timestamp authority's stamps to find any rewritten history.
