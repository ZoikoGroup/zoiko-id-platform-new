# Runbook: moving an existing deployment from two databases (`zid_cell` + `zid_global`) to one

For deployments that already run the global plane (tenant locator + discovery) as its own process on port 8100 with its own `zid_global` database. A new deployment needs none of this:
`docker compose` and the `deploy/*` files already create one database, `zid_cell`, with the global plane in its `global_directory` schema, served by the cell on port 8000
(`ZID_SERVE_GLOBAL_PLANE=true`).

The procedure below was rehearsed on throwaway databases with real rows: 5 directory records, 5 routing aliases and 1 key were copied into a freshly migrated database, the record
identifiers matched, and a second copy attempt was refused and changed nothing.

## Before you start

- **Same master key.** If `ZID_KEY_PROVIDER=db`, the global plane's discovery key (`global_directory.key_version`) is wrapped by a master key. The merged process uses **one** master key file
  (`ZID_KEY_MASTER_FILE`). If the global process used a different file, the copied key cannot be unwrapped: use the same file, or accept that a new discovery key is created (rotate
  it afterwards; clients re-read `/.well-known/zoiko-discovery-keys`). With a KMS provider (`ZID_DISCOVERY_KMS_KEY_ID`) nothing is wrapped in the database and this does not apply.
- **The discovery key stays in `global_directory`**, also in the merged process (`plane="global"`), so its location does not change.
- **Grants.** The runtime role needs the `global_directory` privileges: `deploy/gcp/db/02_grants.sql` already lists that schema (run it after the migration, as in the normal release steps).
- **Pause publishing** for the window: unset `ZID_GLOBAL_LOCATOR_URL` on the cells' workers (an unset URL means "publish nothing"). Nothing then writes to the global plane while you copy.

## Steps

1. **Stop the global service** (`docker compose stop global`, or the `zid@global` unit). The old `zid_global` database is now a stable copy.
2. **Create the new layout.** Deploy the new compose/units, but do not point cells at it yet. Run the normal migration (`docker compose run --rm migrate`): it migrates the cell streams, then
   creates `global_directory` and migrates the global streams **into the same database**.
3. **Copy the data only**, in one transaction, so a failure leaves nothing behind (pg_dump and psql from any host that can reach both):
   ```
   pg_dump -h <old-db> -U <owner> --data-only --no-owner --schema=global_directory zid_global \
     | psql  -h <new-db> -U <owner> -d zid_cell --single-transaction -v ON_ERROR_STOP=1
   ```
   Do not copy the schema or `zid_migrations`: the migration in step 2 already created both. Run it **once**: a second run fails on duplicate keys and changes nothing.
4. **Check the copy:**
   ```
   SELECT (SELECT count(*) FROM global_directory.tenant_directory_record) AS records,
          (SELECT count(*) FROM global_directory.routing_alias)           AS aliases,
          (SELECT count(*) FROM global_directory.key_version)             AS keys,
          (SELECT count(*) FROM global_directory.key_event)               AS key_events;
   ```
   Run it on both databases; the four numbers must match.
5. **Start the merged cell** with `ZID_SERVE_GLOBAL_PLANE=true` and no `ZID_GLOBAL_DATABASE_URL`. `GET /health/ready` must list `global_directory` among the schemas present.
6. **Route the global hostname to the cell.** `global.<domain>` goes to `cell:8000` (the Caddyfiles in `deploy/` already do this). Check discovery:
   `GET https://global.<domain>/v1/discovery?workspace=<a known workspace>` returns a signed answer, and `/.well-known/zoiko-discovery-keys` returns the key.
7. **Resume publishing:** set `ZID_GLOBAL_LOCATOR_URL` on the workers again, pointing at the cell (`http://cell:8000` or `https://global.<domain>`), never at the old `global:8100`.
8. **Keep `zid_global` for a rollback window** (a week is usual), read-only and not connected to anything, then drop it.

## Rollback

- **Before step 7** (publishing still paused): nothing has been written to the new database that the old one lacks. Restore the previous compose/units and the old hostname routing, and
  start the global service against `zid_global`. Nothing is lost.
- **After step 7:** the new database is the source of truth, and records published since exist only there. Rolling back then is a separate decision that needs the reverse copy;
  do not do it casually.

## What I did not verify

The rehearsal used a local PostgreSQL 16 and superuser access. It did not cover Cloud SQL specifics (a managed `pg_dump` path, private IP and SSL flags, `pgaudit`), a master-key
mismatch, or a very large directory. Rehearse once against a snapshot of your real `zid_global` before the maintenance window.
