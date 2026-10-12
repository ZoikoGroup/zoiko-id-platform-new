# Spec change requests

Raised by engineering when a decision ([docs/phases/](../../phases/README.md)) or the implementation finds a gap or inconsistency in a canonical spec. Each CR names the target spec section, the proposed change and the evidence. The spec owner accepts, amends or rejects it; an accepted CR updates the spec and this register.

| CR | Target | Summary | Raised by | Status |
|---|---|---|---|---|
| [CR-001](CR-001-continuity-modes.md) | ZID-DOM-001 §6.3, ZID-DATA-001 | Adopt four canonical `continuity_mode` values | DEC-C2 | Open |
| [CR-002](CR-002-revalidation-pending-state.md) | ZID-MANDATE-001 §6 | Add `REVALIDATION_PENDING` grant state | DEC-C2 | Open |
| [CR-003](CR-003-data-model-proposed-tables.md) | ZID-DATA-001 | Add 11 tables required by other specs | Table ownership registry | Open |
| [CR-004](CR-004-per-table-ownership.md) | ZID-DATA-001 §1.1 | Schemas are namespaces; ownership is per table | Repository structure | Open |
| [CR-005](CR-005-master-sequence-reconciliation.md) | Guideline §2, Docs 18/19/23 | Record the canonical document for each duplicate slot | ZID-SEC-001 §33 | Open |
| [CR-006](CR-006-mandate-ledger-columns.md) | ZID-DATA-001 §8.2–8.3 | Mandate ledger states and columns required by ZID-MANDATE-001 | DEC-C5 migration | Open |
| [CR-007](CR-007-phase-c-tables.md) | ZID-DATA-001 §3.2, §4.3, §7.1 | Grant, root, identifier and relationship columns required by upstream specs | Phase C migrations | Open |
| [CR-008](CR-008-relationship-edge-event.md) | ZID-EVENTS-001 §22 | Declare a relationship change event for the projector | DEC-C4 | Open |
