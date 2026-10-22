# CR-014: Residency enforcement and workload identity tables

- **Target:** ZID-TENANCY-001 §11, §13, §19, §25; AI Agent spec §5, §16; CRYPTO-MI-001 §29; action catalogue; event catalogue · **Raised by:** Phase F W5/W6 (DEC-F7; DEC-G1 inputs) · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `platform.tenant_residency` | tenant | The TenantPlacementDecision this cell is bound by (TENANCY §13.1): profile, home cell, permitted/prohibited recovery cells, operator jurisdictions (required for SRP-2/3), key profile, jurisdiction pack, placement epoch, approval reference. |
| `platform.residency_transfer_rule` | tenant | The approved exceptions to `cross_border_default: DENY`: data class, destination cell, purpose, transfer mechanism (must be one the tenant's jurisdiction pack supports), field allowlist, approval, validity; independently suspendable. |
| `agent.environment_quarantine` | machine | Quarantine episodes per workload environment; quarantine bumps `security_revision` of every binding in it. |

## Actions

`tenant.residency.manage` (R3/E3; changes need a second administrator), `machine.workload.manage` (R3/E3).

## Events

`tenant.residency_profile.changed` and `privacy.transfer_policy.changed` (canonical) are emitted; `machine.environment.released` is proposed. Transfer gateway decisions for DC1-DC3 leave protected audit records `cross_cell_transfer.authorized` / `cross_cell_transfer.denied` (TEN-21).

## Rules implemented (engineering reading of TENANCY §5-6, §11, §19)

- Storage: DC0 anywhere; DC1/DC2 within the home legal zone for SRP-1+; DC3 within the home jurisdiction, and the home cell only for SRP-2+; SRP-3 never leaves its cell.
- Transfer: leaving the boundary needs an exact ACTIVE rule; unknown destinations fail closed (`DEPENDENCY_RESIDENCY_UNKNOWN`); key material never transfers.
- Operators: SRP-2/3 restrict operator jurisdictions to the residency record (default: the home jurisdiction), intersected with the tenant's own policy.

Counsel/residency owners must confirm the DC1 rule for SRP-1 and the mechanisms each pack lists.
