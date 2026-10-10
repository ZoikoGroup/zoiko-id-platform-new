# authority-runtime

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `authority` | `authz.authority_grant`, `authz.resource_scope`, `authz.data_scope` |
| `machine` | `agent.agent_profile`, `agent.workload_binding` |
| `mandate` | `agent.mandate`, `agent.mandate_balance`, `agent.mandate_reservation`, `agent.writer_fence` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
