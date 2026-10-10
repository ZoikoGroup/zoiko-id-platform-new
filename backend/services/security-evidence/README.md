# security-evidence

Deployable unit. See [architecture §6](../../../docs/architecture/zoiko-id-architecture.md) and [table ownership](../../database/table-ownership.yaml).

| Module | Owned tables |
|---|---|
| `evidence` | `evidence.decision_receipt`, `evidence.subject_handle_map`, `evidence.evidence_object`, `evidence.ledger_entry`, `evidence.checkpoint` |
| `privacy` | `privacy.consent_receipt`, `privacy.purpose`, `privacy.processing_role_assignment` |
| `signals` | `signals.security_signal`, `signals.security_state` |

Each module: `api/` → `application/` → `domain/` ← `infrastructure/`. Modules talk only through `ports.py`. Each module with tables has its own Alembic stream in `migrations/<module>/`.
