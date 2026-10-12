# CR-004: Schemas are namespaces; ownership is per table

- **Target:** ZID-DATA-001 §1.1 · **Status:** Open

## Problem
ZID-DATA-001 §1.1 assigns each schema to one "owning boundary" (e.g. `authz` → Authority/Policy Service), but ZID-SVC-001 §5 gives tables inside that schema to different services (AUTHY grants, POL releases, AUTHZ decisions), and §6 says each logical service owns its tables and migration lifecycle.

## Proposed change
Add to ZID-DATA-001 §1.1: *"Schema names are namespaces. Each table has exactly one owning logical service (ZID-SVC-001 §5). Migrations, runtime role grants and RLS coverage are managed per table owner."* The implementation registry is `backend/database/table-ownership.yaml` (DEC-D1).
