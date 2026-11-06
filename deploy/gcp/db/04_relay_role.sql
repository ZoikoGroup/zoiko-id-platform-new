-- Step 4 (only when the `events` profile runs): the outbox relay's own database role. Run as `postgres` against zid_cell, after the
-- migrations (the events schema must exist). Idempotent.
--
--   psql "host=<db> dbname=zid_cell user=postgres sslmode=require" -v relay_pw="$RELAY_PW" -f db/04_relay_role.sql
--
-- zid_relay reads every tenant's outbox, so it needs BYPASSRLS, and nothing else: it marks events published and takes leases (UPDATE),
-- and it cannot read or change any other table. Put its connection string in ZID_RELAY_DATABASE_URL.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE zid_relay LOGIN PASSWORD %L NOSUPERUSER BYPASSRLS', :'relay_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zid_relay') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO zid_relay', current_database()) \gexec
GRANT USAGE ON SCHEMA events TO zid_relay;
GRANT SELECT, UPDATE ON events.outbox_event TO zid_relay;
