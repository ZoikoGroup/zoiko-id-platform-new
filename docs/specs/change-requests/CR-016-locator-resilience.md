# CR-016: Global locator, signed discovery and governed recovery operations

- **Target:** ZID-TENANCY-001 §14–16; ZID-RESILIENCE-001 §4, §11, §15–20, §25–30; DEC-G1, DEC-G3; SEC-001 §11–13 (capabilities); event catalogue · **Raised by:** Phase G W6–W7 · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `platform.directory_publication` | tenant | Per tenant, the last directory record this cell published to the global locator (version, epoch, hash). |
| `global_directory.tenant_directory_record` | locator | **Global role.** The signed directory record per tenant (home cell, status, placement epoch), verified against pinned per-cell keys. No personal data. |
| `global_directory.routing_alias` | locator | **Global role.** Workspace and verified-domain aliases → tenant, for discovery. |
| `platform.cell_state` | resilience | Operator-set cell state: ACTIVE, DEGRADED, DRAINING, QUARANTINED. A quarantined or draining cell takes no new tenant. |
| `platform.cell_readiness` | resilience | Latest recovery readiness per cell: replica lag, backup age, recovery-key readiness, one-AZ-loss headroom. |
| `platform.recovery_operation` | resilience | One governed failover, migration, failback or restore; one open operation per tenant. |
| `platform.recovery_step` | resilience | The append-only, strictly ordered runbook step log with evidence references and gate reports. |
| `platform.dr_exercise` | resilience | DR exercise results with observed vs target RTO/RPO; pass/fail is computed. |

`platform.tenant` guard change (tenant_0006): a live tenant's home cell changes only inside a transaction marked by a governed recovery operation.

## Operator capabilities

| Capability | Risk | Note |
|---|---|---|
| `platform.dr.execute` | R3, two-person, break-glass allowed | Declare and run a failover or failback for one tenant. |
| `platform.migration.execute` | R3, two-person | Governed cell migration or restore. |
| `platform.cell.manage` | R2, break-glass allowed | Quarantine/restore a cell, report readiness, record exercises. |

## Events

Canonical: `tenant.failover.started|completed|failed`, `tenant.migration.started|quiesced|cutover|completed|failed`, `tenant.failback.started|completed`, `tenant.placement.changed`, `writer.epoch.changed`, `mandate.reconciliation.required`, `cell.quarantined`, `cell.status.changed`, `recovery.started`, `restore.completed`, `dr.exercise.completed`, `resilience.slo.breached`. Proposed: `tenant.failback.failed`, `restore.failed`.

## Engineering readings to confirm

- Runbooks (RESILIENCE §15–20) as enforced step sequences; CONFIRM steps need an evidence reference, probe-gated steps need every probe passed (no partial credit).
- A failover target must be one of the tenant's pre-approved recovery cells; a migration target any cell the residency does not prohibit.
- No abort after `ADVANCE_EPOCHS`: the only way back is a governed failback.
- `RECONCILE` marks every open mandate reservation `RECONCILIATION_REQUIRED`; DR never frees authority.
- Requests carrying a stale placement epoch are refused with `CELL_ROUTING_STALE` (421) and a location hint.
