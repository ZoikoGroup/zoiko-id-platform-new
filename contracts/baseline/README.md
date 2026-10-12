# Architecture baseline registries

Machine-readable form of the architecture baseline (Phase A: invariants, boundaries, gates from ZID-ARCH-002) and the requirements contract (Phase B: risk classes, SLOs, capacity from ZID-REQ-001). See [phase A](../../docs/phases/phase-a-architecture-baseline.md) and [phase B](../../docs/phases/phase-b-requirements-contract.md).

| File | Contents | Source |
|---|---|---|
| [invariants.yaml](invariants.yaml) | The 15 non-negotiable invariants, each with its owning phase, how it is enforced, and status (`enforced` / `partial` / `planned`) | §4 |
| [capabilities.yaml](capabilities.yaml) | The bounded capabilities: what each owns and must not own, and which modules implement it | §5.1, §21.1 |
| [gates.yaml](gates.yaml) | P0 and P1 gaps (with the requirement IDs that close each P0), the 11 baseline gates and the ten baseline decisions, with status and evidence | ARCH §22–23, REQ §9 |
| [risk-classes.yaml](risk-classes.yaml) | Risk classes R0–R3: revocation SLO, failure posture, evidence floor, online/mandate/commit-recheck duties, weakening-exception rules | REQ §4, DEC-B1 |
| [slo.yaml](slo.yaml) | Availability targets, p95 latency budgets per path and component, the decision-service performance gate | REQ §3.1–3.2, DEC-B3 |
| [capacity.yaml](capacity.yaml) | Stage A/B/C design points, the 2× qualification gate, cell admission headroom, unit-economics meters | REQ §5, DEC-B2 |

`test_baseline.py`, `test_risk_contract.py` and `test_requirements_contract.py` (in `backend/app/tests/unit/`) run in CI and fail the build when:

- a reference doesn't resolve (a test, an acceptance test `AT-xx`, a decision `DEC-xx`, a spec ID)
- an invariant's status doesn't match its enforcement (for example `enforced` with no executable test)
- a module has no capability or two of them, or owns a table its capability must not own
- a gate is marked `closed` without an evidence link (§23.2: narrative assurance does not close a gate)
- BG-01 is closed while a baseline decision is still not `Accepted`
- the risk contract in code differs from `risk-classes.yaml`, or a catalogued action sits below its class's evidence floor
- an SLO or capacity number no longer appears in the ZID-REQ-001 requirement it cites
- a requirement row is malformed, has no owner or verification, or cites a decision that doesn't exist

## Updating

- **Closing a gate:** set `status: closed` and put a link to the retained evidence (test report, signed approval, dossier entry) in `evidence`.
- **Adding enforcement:** when a phase adds a guard for an invariant, add an `architecture-test` entry and move its status from `planned` to `partial` or `enforced`.
- **New module:** add it to exactly one capability in `capabilities.yaml`, then to `roles.py` and `table-ownership.yaml`.
- Changing an invariant or a capability boundary needs a recorded decision ([docs/phases/](../../docs/phases/README.md)) and ARB approval.
