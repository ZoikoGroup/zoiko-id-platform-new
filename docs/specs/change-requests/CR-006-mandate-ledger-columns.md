# CR-006: Mandate ledger columns and states required by ZID-MANDATE-001

- **Target:** ZID-DATA-001 §8.2–8.3 · **Raised by:** `backend/app/migrations/mandate/versions/0001_mandate_ledger.py` (DEC-C5) · **Status:** Open

ZID-DATA-001 v1.0 is narrower than ZID-MANDATE-001, its upstream for the ledger. The migration implements the Mandate spec; the Data Model should be aligned:

| Item | ZID-DATA-001 v1.0 | Required by ZID-MANDATE-001 | Implemented as |
|---|---|---|---|
| `mandate_status` | ACTIVE, PAUSED, EXHAUSTED, REVOKED, EXPIRED, COMPLETED | §10 starts mandates in `PENDING_APPROVAL` | enum adds `PENDING_APPROVAL` |
| `reservation_status` | RESERVED, COMMITTED, RELEASED, EXPIRED | §14: unknown side-effect outcome → `RECONCILIATION_REQUIRED`, capacity stays blocked | enum adds `RECONCILIATION_REQUIRED` |
| Per-transaction ceiling | not present | §9 `per_transaction_value_limit` | `agent.mandate.per_transaction_value_limit numeric(38,9)` |
| Transaction commitment | not present | §12.1, §20: reservation binds to `transaction_context_hash` | `agent.mandate_reservation.transaction_context_hash bytea` + `hash_algorithm` (ZID-DATA-001 §1: hashes carry their algorithm) |
| Execution reference | not present | §13–14: stable execution ID for reconciliation | `agent.mandate_reservation.execution_id`, `decision_id`, `settled_at` |
| Writer fence | not present (CR-003 proposes it) | §19.1 writer epoch | `agent.writer_fence (tenant_id, cell_id, epoch, updated_at)`, checked `FOR SHARE` on every write |

Additional guards beyond the spec (no change requested; recorded for review): non-negative `CHECK`s on every balance and requested amount, `FORCE ROW LEVEL SECURITY` on all four tables, and a conditional `UPDATE` that re-checks limits and `ledger_version` in SQL.
