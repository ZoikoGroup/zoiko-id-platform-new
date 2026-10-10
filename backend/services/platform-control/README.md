# platform-control

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `configuration` | `platform.configuration_snapshot` |
| `metering` | `metering.usage_event`, `metering.correction` |
| `notification` | `platform.notification_request` |
| `operator_access` | `platform.jit_grant`, `platform.lockbox_request` |
| `policy_authoring` | `authz.policy_definition`, `authz.policy_release` |
| `search` | — (no tables) |
| `tenant` | `platform.tenant`, `platform.cell`, `platform.tenant_shard`, `platform.entitlement` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
