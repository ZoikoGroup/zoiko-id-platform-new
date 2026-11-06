-- The databases the services VM uses (OpenBao and SpiceDB store in PostgreSQL). Run as `postgres` against the `postgres` database.
-- Passwords arrive as psql variables, never in a file:
--   psql "host=$ZID_DB_HOST dbname=postgres user=postgres sslmode=require" -v openbao_pw="$OPENBAO_DB_PW" -v spicedb_pw="$SPICEDB_DB_PW" -f db/05_service_databases.sql
-- OpenBao's table is created by OpenBao's own role (db-init.sh does it). Idempotent.
\set ON_ERROR_STOP on
SELECT format('CREATE ROLE openbao LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'openbao_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openbao') \gexec
SELECT 'CREATE DATABASE openbao OWNER openbao'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'openbao') \gexec
SELECT format('CREATE ROLE spicedb LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'spicedb_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'spicedb') \gexec
SELECT 'CREATE DATABASE spicedb OWNER spicedb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'spicedb') \gexec
