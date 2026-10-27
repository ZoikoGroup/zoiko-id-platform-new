-- Probe: can this admin user create a BYPASSRLS role on this Cloud SQL instance? Run first, as `postgres`:
--   psql "host=<private-ip> dbname=zid_cell user=postgres sslmode=require" -f db/00_check_cloudsql.sql
-- If it says NOT ALLOWED, use the fallback in README step 3: run db/03_worker_policies.sql instead of relying on
-- BYPASSRLS (01_roles.sql then creates zid_worker without it).
DO $$
BEGIN
    CREATE ROLE zid_probe_bypass NOLOGIN BYPASSRLS;
    DROP ROLE zid_probe_bypass;
    RAISE NOTICE 'OK: BYPASSRLS roles can be created here; 01_roles.sql is enough for the worker.';
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'NOT ALLOWED: use db/03_worker_policies.sql for the worker role.';
END $$;
