-- The databases that hold OpenBao's and SpiceDB's data (their VMs store in PostgreSQL). Run as `postgres` against the `postgres` database;
-- the table is created by rehearse.sh as the openbao role. Passwords arrive as psql variables, never in a file.
\set ON_ERROR_STOP on
SELECT format('CREATE ROLE openbao LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'openbao_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openbao') \gexec
SELECT 'CREATE DATABASE openbao OWNER openbao'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'openbao') \gexec
SELECT format('CREATE ROLE spicedb LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'spicedb_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'spicedb') \gexec
SELECT 'CREATE DATABASE spicedb OWNER spicedb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'spicedb') \gexec
