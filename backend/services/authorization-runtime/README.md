# authorization-runtime

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `decision` | `authz.policy_decision` |
| `policy_runtime` | — (no tables) |
| `relationship` | `org.relationship_edge`, `authz.relation_publication` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
