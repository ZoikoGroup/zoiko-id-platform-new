# Architecture baseline registries

Machine-readable form of the Back-End Target Architecture's invariants, boundaries and gates (ZID-ARCH-002 §4, §5.1, §22–§24). They are the Phase A deliverable: see [phase A](../../docs/phases/phase-a-architecture-baseline.md).

| File | Contents | Source |
|---|---|---|
| [invariants.yaml](invariants.yaml) | The 15 non-negotiable invariants, each with its owning phase, how it is enforced, and status (`enforced` / `partial` / `planned`) | §4 |
| [capabilities.yaml](capabilities.yaml) | The bounded capabilities: what each owns and must not own, and which modules implement it | §5.1, §21.1 |
| [gates.yaml](gates.yaml) | P0 and P1 gaps, the 11 baseline gates and the ten baseline decisions, with status and evidence | §22, §23 |

`backend/app/tests/unit/test_baseline.py` runs in CI and fails the build when:

- a reference doesn't resolve (a test, an acceptance test `AT-xx`, a decision `DEC-xx`, a spec ID)
- an invariant's status doesn't match its enforcement (for example `enforced` with no executable test)
- a module has no capability or two of them, or owns a table its capability must not own
- a gate is marked `closed` without an evidence link (§23.2: narrative assurance does not close a gate)
- BG-01 is closed while a baseline decision is still not `Accepted`

## Updating

- **Closing a gate:** set `status: closed` and put a link to the retained evidence (test report, signed approval, dossier entry) in `evidence`.
- **Adding enforcement:** when a phase adds a guard for an invariant, add an `architecture-test` entry and move its status from `planned` to `partial` or `enforced`.
- **New module:** add it to exactly one capability in `capabilities.yaml`, then to `roles.py` and `table-ownership.yaml`.
- Changing an invariant or a capability boundary needs a recorded decision ([docs/phases/](../../docs/phases/README.md)) and ARB approval.
