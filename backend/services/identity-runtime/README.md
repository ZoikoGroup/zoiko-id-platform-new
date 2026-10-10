# identity-runtime

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `authentication` | `authn.authenticator`, `authn.external_identity_link` |
| `principal` | `core.principal`, `core.principal_identifier`, `core.device` |
| `session` | `authn.session`, `authn.oauth_client`, `authn.refresh_token_family` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
