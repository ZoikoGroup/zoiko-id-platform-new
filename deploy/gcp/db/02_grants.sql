-- Step 2: run as `postgres` (or zid_owner) after the migrations, on zid_cell. Idempotent: re-run after
-- every migration that adds a schema. Same schema list and privileges as local/postgres/runtime_role.sql, applied to
-- the Cloud SQL roles and made permanent for tables the owner creates later (default privileges).
\set ON_ERROR_STOP on

DO $$
DECLARE s text;
BEGIN
    FOREACH s IN ARRAY ARRAY['agent','core','org','authz','events','platform','authn','signals','verify','cred',
                             'evidence','privacy','metering','commercial','notification','configuration',
                             'zid_migrations','machine','tenant','federation','integration','webhook','provisioning','operations',
                             'operator','locator','search','relationship','policy','session','decision','mandate',
                             'keys', 'global_directory']  -- the last is the global plane's only schema, in the same database
    LOOP
        IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO zid_runtime, zid_worker', s);
            EXECUTE format('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA %I TO zid_runtime, zid_worker', s);
            EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO zid_runtime, zid_worker', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE zid_owner IN SCHEMA %I GRANT SELECT, INSERT, UPDATE ON TABLES TO zid_runtime, zid_worker', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE zid_owner IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO zid_runtime, zid_worker', s);
        END IF;
    END LOOP;
END $$;

-- The one DELETE the platform needs outside the worker: the global locator drops a tenant's retired routing aliases when its
-- directory record is republished. The global plane holds no personal data; nothing else gets DELETE (the cell runtime never does).
DO $$
BEGIN
    IF to_regclass('global_directory.routing_alias') IS NOT NULL THEN
        GRANT DELETE ON global_directory.routing_alias TO zid_runtime;
    END IF;
END $$;
