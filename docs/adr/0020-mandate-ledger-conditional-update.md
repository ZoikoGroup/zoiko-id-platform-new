# ADR-0020: Mandate ledger using fenced conditional updates in PostgreSQL

- **Status:** Proposed
- **Owner:** Authority & Mandate Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** P0-03; Back-End Target Architecture §9.2

## Context
P0-03: parallel agent actions must never overspend or exceed count or data limits. The ledger must have a home-cell single writer, fencing, and atomic reserve/commit/release with idempotency and reconciliation.

## Decision
- The ledger runs in the `agent` schema of the home cell's primary database, owned by the MAND module.
- **Reserve** happens in one transaction:
  1. Writer-fence check (`agent.writer_fence` epoch, `FOR SHARE`).
  2. Idempotent reservation insert (`ON CONFLICT (tenant_id, idempotency_key)`).
  3. Conditional `UPDATE mandate_budget … WHERE consumed + reserved + :amount <= limit_amount`. Zero rows means `MANDATE_LIMIT_EXCEEDED`.
  4. Outbox event.
- **Commit and release** are state transitions on the reservation with matching budget arithmetic. An expiry sweeper releases stale reservations. Reconciliation uses the external `execution_id`.
- Cross-cell requests are routed to the home cell. There is never a multi-cell decrement.

## Consequences
- Row-level locking serializes contention on each budget row, which is correct and needs no SERIALIZABLE retry storms.
- A hot mandate is bounded by single-row throughput. The mitigation, if a design partner needs it, is budget sub-allocation ("slicing") under one parent limit.
- Acceptance: 10k-concurrency test with crash injection; no bound is ever exceeded; full reconciliation afterwards.
