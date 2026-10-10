# enterprise-integration

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `federation` | `authn.federation_connection` |
| `integration` | `platform.integration_application`, `platform.mapping_release` |
| `onboarding` | `platform.onboarding_plan` |
| `provisioning` | `platform.provisioning_source` |
| `webhook` | `events.webhook_endpoint`, `events.webhook_delivery` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
