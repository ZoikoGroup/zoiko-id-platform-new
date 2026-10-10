#!/bin/sh
# Local only: the global plane uses its own database (ZID-TENANCY-001 §2 — no personal data).
# In production it is a separate cluster in the global-plane account (ADR-0018).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "CREATE DATABASE zid_global OWNER \"$POSTGRES_USER\";"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname zid_global \
  -f /zid/schemas/001_global_schemas.sql
