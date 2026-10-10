# trust-services

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `credential` | `cred.credential_record`, `cred.presentation_record` |
| `organization` | `org.organization`, `org.org_unit`, `org.authority_root_assertion` |
| `verification` | `verify.verification_assertion`, `verify.assurance_profile` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
