#!/usr/bin/env bash
# Create the databases, roles and privileges on the database VM, the way the production guide does (deploy/gcp/README.md step 3).
# Run from any machine with the `psql` client that can reach the database VM (the app VM works). Idempotent.
#   ZID_DB_HOST=10.0.0.3 PG_ADMIN_PASSWORD=... ZID_OWNER_PASSWORD=... ZID_RUNTIME_PASSWORD=... ZID_WORKER_PASSWORD=... \
#   ZID_RELAY_PASSWORD=... ZID_OPENBAO_DB_PASSWORD=... ZID_SPICEDB_DB_PASSWORD=... ./db-init.sh pre
#   ... then run the migration (`docker compose run --rm migrate`) and:  ... ./db-init.sh post
# The passwords are the ones in .env's connection URLs; generate each with `openssl rand -hex 24`. They are passed to psql as variables,
# never written to a file.
set -euo pipefail
cd "$(dirname "$0")"
GCP="../gcp/db"
SCHEMAS="../../backend/database/schemas"
: "${ZID_DB_HOST:?}" "${PG_ADMIN_PASSWORD:?}"
export PGSSLMODE=require

admin() { PGPASSWORD="$PG_ADMIN_PASSWORD" psql -v ON_ERROR_STOP=1 -h "$ZID_DB_HOST" -U postgres "$@"; }
as_role() { local role="$1" pw="$2"; shift 2; PGPASSWORD="$pw" psql -v ON_ERROR_STOP=1 -h "$ZID_DB_HOST" -U "$role" "$@"; }

pre() {
  : "${ZID_OWNER_PASSWORD:?}" "${ZID_RUNTIME_PASSWORD:?}" "${ZID_WORKER_PASSWORD:?}" "${ZID_OPENBAO_DB_PASSWORD:?}" "${ZID_SPICEDB_DB_PASSWORD:?}"
  echo "== databases"
  for DB in zid_cell zid_global; do
    admin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1 || admin -d postgres -c "CREATE DATABASE $DB"
  done
  echo "== roles: owner (migrations), runtime (row-level security applies), worker (bypasses it)"
  for DB in zid_cell zid_global; do
    admin -d "$DB" -v owner_pw="$ZID_OWNER_PASSWORD" -v runtime_pw="$ZID_RUNTIME_PASSWORD" -v worker_pw="$ZID_WORKER_PASSWORD" -f "$GCP/01_roles.sql" >/dev/null
  done
  echo "== namespaces, as the owner"
  as_role zid_owner "$ZID_OWNER_PASSWORD" -d zid_cell -f "$SCHEMAS/001_cell_schemas.sql" >/dev/null
  as_role zid_owner "$ZID_OWNER_PASSWORD" -d zid_global -f "$SCHEMAS/001_global_schemas.sql" >/dev/null
  echo "== the services VM's databases (OpenBao, SpiceDB)"
  admin -d postgres -v openbao_pw="$ZID_OPENBAO_DB_PASSWORD" -v spicedb_pw="$ZID_SPICEDB_DB_PASSWORD" -f db/05_service_databases.sql >/dev/null
  as_role openbao "$ZID_OPENBAO_DB_PASSWORD" -d openbao >/dev/null <<'SQL'
CREATE TABLE IF NOT EXISTS openbao_kv_store (
  parent_path TEXT COLLATE "C" NOT NULL, path TEXT COLLATE "C", key TEXT COLLATE "C", value BYTEA,
  CONSTRAINT pkey PRIMARY KEY (path, key));
CREATE INDEX IF NOT EXISTS parent_path_idx ON openbao_kv_store (parent_path);
SQL
  echo "done. Next: docker compose run --rm migrate   then   ./db-init.sh post"
}

post() {
  : "${ZID_RELAY_PASSWORD:?}"
  echo "== privileges for the runtime and worker roles (re-run after any migration that adds a schema)"
  for DB in zid_cell zid_global; do admin -d "$DB" -f "$GCP/02_grants.sql" >/dev/null; done
  echo "== the event relay's own role"
  admin -d zid_cell -v relay_pw="$ZID_RELAY_PASSWORD" -f "$GCP/04_relay_role.sql" >/dev/null
  echo "done."
}

case "${1:-}" in
  pre) pre ;;
  post) post ;;
  *) sed -n 2,9p "$0"; exit 2 ;;
esac
