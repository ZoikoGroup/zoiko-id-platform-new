-- Step 1 of the database setup on Cloud SQL. Run as the `postgres` admin user against zid_cell, the one platform database.
-- Passwords come from psql variables so they never sit in a file:
--
--   psql "host=<private-ip> dbname=zid_cell user=postgres sslmode=require" -v owner_pw="$OWNER_PW" \
--        -v runtime_pw="$RUNTIME_PW" -v worker_pw="$WORKER_PW" -f db/01_roles.sql
--   then create the namespaces as the owner (backend/database/schemas/001_cell_schemas.sql and
--   001_global_schemas.sql, both on zid_cell), run the `migrate` service, and finally db/02_grants.sql.
--
--   zid_owner    owns the database and schemas and runs the migrations (used only by the one-shot `migrate` service).
--   zid_runtime  the API: NOSUPERUSER NOBYPASSRLS, so row-level security applies and a company only sees its own rows.
--   zid_worker   the background worker: BYPASSRLS because it sweeps every tenant; SELECT/INSERT/UPDATE only (no DELETE).
--                The global plane's schema (global_directory) holds no personal data, and the worker never touches it.
--
-- BYPASSRLS needs a privileged creator. If Cloud SQL refuses it for `postgres`, create zid_worker in the console
-- (Users) as a built-in user and re-run; every statement here is idempotent.
\set ON_ERROR_STOP on
-- Pass -v worker_attr=NOBYPASSRLS when db/00_check_cloudsql.sql says BYPASSRLS is not allowed (then run 03_worker_policies.sql).
\if :{?worker_attr}
\else
  \set worker_attr BYPASSRLS
\endif

SELECT format('CREATE ROLE zid_owner LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'owner_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zid_owner') \gexec
SELECT format('CREATE ROLE zid_runtime LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'runtime_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zid_runtime') \gexec
SELECT format('CREATE ROLE zid_worker LOGIN PASSWORD %L NOSUPERUSER %s', :'worker_pw', :'worker_attr')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zid_worker') \gexec

-- The admin user must be a member of the owner role to hand it the database.
GRANT zid_owner TO CURRENT_USER;
SELECT format('ALTER DATABASE %I OWNER TO zid_owner', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO zid_runtime, zid_worker', current_database()) \gexec
ALTER SCHEMA public OWNER TO zid_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
