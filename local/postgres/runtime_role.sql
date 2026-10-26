-- Local development: run the cell API as a role that row-level security applies to.
--
-- The compose database user (`zid`) is a superuser with BYPASSRLS, so an API connected as `zid` sees every
-- company's rows and tenant isolation is not exercised. Production gives the API a runtime role like this one;
-- the migrations and the worker keep the owner role. Apply after the migrations have run:
--
--   docker compose exec -T postgres psql -U zid -d zid_cell -f /dev/stdin < local/postgres/runtime_role.sql
--
-- The password is for local use only (override with ZID_RUNTIME_PASSWORD in the compose environment).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zid_runtime') THEN
        CREATE ROLE zid_runtime LOGIN PASSWORD 'zid_runtime_local_only' NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;

DO $$
DECLARE s text;
BEGIN
    FOREACH s IN ARRAY ARRAY['agent','core','org','authz','events','platform','authn','signals','verify','cred',
                             'evidence','privacy','metering','commercial','notification','configuration',
                             'zid_migrations','machine','tenant','federation','integration','webhook','provisioning','operations',
                             'operator','locator','search','relationship','policy','session','decision','mandate']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO zid_runtime', s);
            EXECUTE format('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA %I TO zid_runtime', s);
            EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO zid_runtime', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE zid IN SCHEMA %I GRANT SELECT, INSERT, UPDATE ON TABLES TO zid_runtime', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE zid IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO zid_runtime', s);
        END IF;
    END LOOP;
END $$;
