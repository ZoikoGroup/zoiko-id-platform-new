-- Cell database bootstrap: namespaces only (ZID-DATA-001 §1.1). No tables here.
-- Run as db_owner_migration. Tables are created by the owning module's Alembic stream.

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS org;
CREATE SCHEMA IF NOT EXISTS authn;
CREATE SCHEMA IF NOT EXISTS verify;
CREATE SCHEMA IF NOT EXISTS cred;
CREATE SCHEMA IF NOT EXISTS authz;
CREATE SCHEMA IF NOT EXISTS agent;
CREATE SCHEMA IF NOT EXISTS privacy;
CREATE SCHEMA IF NOT EXISTS evidence;
CREATE SCHEMA IF NOT EXISTS signals;
CREATE SCHEMA IF NOT EXISTS events;
CREATE SCHEMA IF NOT EXISTS metering;

-- Per-module Alembic version tables live here, never inside a domain schema.
CREATE SCHEMA IF NOT EXISTS zid_migrations;
