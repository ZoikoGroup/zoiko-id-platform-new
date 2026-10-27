-- Fallback when Cloud SQL will not let the admin user create a BYPASSRLS role (see 00_check_cloudsql.sql).
-- Every tenant table has FORCE ROW LEVEL SECURITY, so even the owner is filtered; the worker therefore needs its own
-- permissive policy on each table: `TO zid_worker USING (true)`. Permissive policies are OR-ed, so this widens access
-- for the worker role only and leaves zid_runtime exactly as restricted as before.
-- Run as zid_owner (or postgres) on zid_cell after the migrations, and again after each migration that adds tables.
-- With BYPASSRLS available this file is unnecessary.
\set ON_ERROR_STOP on
SELECT format('CREATE ROLE zid_worker LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'worker_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zid_worker') \gexec

SELECT format('DROP POLICY IF EXISTS zid_worker_all ON %I.%I', n.nspname, c.relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity
   AND n.nspname NOT IN ('pg_catalog', 'information_schema') \gexec
SELECT format('CREATE POLICY zid_worker_all ON %I.%I AS PERMISSIVE FOR ALL TO zid_worker USING (true) WITH CHECK (true)',
              n.nspname, c.relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity
   AND n.nspname NOT IN ('pg_catalog', 'information_schema') \gexec
