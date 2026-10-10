# ADR-0031: Modular monolith deployment

- **Status:** Proposed
- **Owner:** Chief Architect + Engineering Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Supersedes:** the 7+1 deployable-unit packaging in architecture §6 (earlier draft) and the related wording in ADR-0012, ADR-0021 and ADR-0022
- **Implements:** ZID-SVC-001 §4 ("No microservice dogma. An early modular deployment is acceptable.")
- **Sources:** ZID-SVC-001 §4–6, §17, §19, §30; ZID-TENANCY-001 §2–3; ZID-RESILIENCE-001 §0.2, §3

## Context
The specs define **27 logical services** but explicitly do not require 27 deployables (ZID-SVC-001 §4). The earlier draft grouped them into 8 deployable units. The team prefers a **monolithic architecture**: one codebase, one build, one deployable artifact, which is simpler to develop, test, release and operate in the early waves.

The specs allow this **only as a modular monolith**. ZID-SVC-001 §4 says *"What is not acceptable is a shared mutable domain model that makes future extraction impossible or allows one module to bypass another module's authority rules."* A classic shared-model monolith would also break the Data Model rule of one owner per table.

## Decision

### 1. One package, one image
- The whole backend is **one Python package**, `zoiko-id-platform` (`backend/app/src/zid_platform/`), with **29 modules** in `modules/`: the 27 logical services plus `locator` and `discovery`.
- One Docker image (`backend/Dockerfile`), one release, one version.
- The shared `zid-*` platform libraries stay separate workspace packages (mechanics only, no domain logic) and are bundled into the same image.

### 2. Run modes of the same image
| Setting | Values | Purpose |
|---|---|---|
| `ZID_ROLE` | `cell` (27 modules) or `global` (`locator`, `discovery`) | The global plane must hold no personal data and use its own database (ZID-TENANCY-001 §2), so it runs the same image with only its two modules loaded |
| Process | API (default), `consumer`, `worker`, `outbox_relay` | `python -m zid_platform.entrypoints.<name>`; background work runs as separate processes of the same image |

The cell role is deployed **once per regional cell** (`uk-1`, `eu-1`). Cells, residency and failover (ADR-0011) are unchanged; the monolith is the unit deployed into each cell.

### 3. Boundaries kept, and enforced by tests
These rules from the specs stay mandatory inside the monolith. `backend/app/tests/unit/test_architecture.py` fails the build if any is broken:
- **Modules call each other only through `ports.py`.** No module imports another module's `domain/`, `application/`, `infrastructure/` or ORM models.
- **One owner per table** (`backend/database/table-ownership.yaml`), with one migration stream per owning module in `backend/app/migrations/<module>/`.
- **Every module belongs to exactly one plane** (`roles.py`). The global role never loads cell modules.

### 4. Hot-path protection without separate services
ZID-SVC-001 §19 and ZID-RESILIENCE-001 §3 require that admin, batch and support work never starves R2/R3 decisions. In a monolith:
- **Bulkheads in-process:** separate database connection pools and worker limits for runtime vs. control/integration traffic.
- **Same image, separate replica pools (when load requires):** route runtime paths (`/authz`, `/sessions`, …) and admin paths to different replica sets of the same image behind the gateway. `roles.py` `PLANES` already holds the grouping.
- Background work (consumers, workflows, outbox relay) always runs in separate processes, never in the API process.

### 5. Extraction stays possible
A module can leave the monolith without touching callers: move `modules/<name>/` and `migrations/<name>/` to a new service, and swap its `ports.py` adapter for an HTTP client generated from `contracts/`. The **only pre-approved extraction** is the `decision` module, if the W0 performance benchmark fails (ADR-0021).

## Consequences
- **Simpler:** one build, one image (~312 MB), one compose service per role, and one version to release and roll back.
- **Faster module calls:** everything is in-process, which helps the ≤15 ms mandate and ≤60 ms authorization budgets.
- **Shared blast radius inside a cell:** a crash or memory leak in any module affects the whole cell process. Mitigated by the bulkheads and replica pools above, plus N+2 replicas across AZs (ADR-0011).
- **Shared release cadence:** a change to any module ships the whole image. Mitigated by canary rollout per cell (ADR-0027).
- **The boundaries depend on CI.** The architecture tests are a required check, and removing them needs an ADR.

## Alternatives considered
- **8 deployable units (previous draft):** stronger isolation per plane, but more images, pipelines and network hops than the team wants at this stage.
- **Classic shared-model monolith:** rejected; it violates ZID-SVC-001 §4–6 and the Data Model's single table ownership.
- **One deployable per logical service (27):** rejected by ZID-SVC-001 §4 ("no microservice dogma") and far too costly for Stage A.
