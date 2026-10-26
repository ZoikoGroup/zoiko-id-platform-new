# Phase I: Production proof

> **Gate (Build Documentation Guideline §3):** Produces executable evidence that the complete system meets architecture and market-entry gates.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 25 | ZID-VERIFY-MASTER-001 | Master Verification, Test, Certification & Production Readiness |

Phase I records no new decisions. It proves the earlier ones: Document 25 must show executable evidence that every decision in Phases A–H meets its **Acceptance** section.

## Deliverables and status

What can be proven without a deployed cell is proven by tests that run in Docker. Everything below is linked from [`acceptance-tests.csv`](../traceability/acceptance-tests.csv) and [`gates.yaml`](../../contracts/baseline/gates.yaml), and `tools/scripts/evidence.py` turns the test results into [`evidence-results.csv`](../traceability/evidence-results.csv) (latest result per linked test, with run time and commit). The index never changes a status: it makes the evidence current and checkable, and `--check` fails the build if linked evidence fails or disappears.

| ID | Proof | Tests / evidence | Status |
|---|---|---|---|
| I-1 | **Evidence index.** Every `path::test` link in the traceability matrix is run and recorded (MPR-01) | `tools/scripts/evidence.py` | Built |
| I-2 | **Tenant escape, data plane** (MPR-04, TEN-01/02, SEC-02): every registered tenant-scoped table has forced, tenant-keyed row-level security; with real data in two tenants, another tenant or no tenant reads and changes nothing | `test_tenant_isolation_pg.py` (128 tables structurally; 18 tables with live cross-tenant read and write attacks) | Built |
| I-3 | **Hostile API sweep** (MPR-03/04/08 slice): every published operation is called with no credentials and with forged tokens (`alg: none`, unknown key, garbage, oversized); none may succeed unless on a named public list, and none may crash | `test_api_security_sweep_pg.py` | Built. Found and fixed an information-leaking service banner |
| I-4 | **No placeholder controls** (MPR-03): shipped code has no TODO/FIXME, disabled TLS checks, test doubles, hard-coded credentials or auth bypass | `test_no_placeholder_controls.py` | Built |
| I-5 | **Golden decisions** (MPR-05, SEC-01): 32 data-driven cases through the real Cedar engine and platform floor (R0 to R3 fail behaviour, forbid dominance, floors tenants cannot override, hostile input), each evaluated three times for determinism | `tests/golden/decisions/core.yaml`, `test_golden_decisions.py` | Built. Found and fixed non-deterministic ordering of determining policies, which would have broken receipt replay (MPR-11) |
| I-6 | **Mandate concurrency with crash injection** (P0-03, MPR-06, BG-05): 10,000 parallel reservations, 10% crashing mid-transaction, against a 5,000 limit | `test_mandate_ledger_pg.py`, `tests/property/test_mandate_ledger.py` | Built (already existed); now linked |
| I-7 | **Stale projection and event flow** (P0-04, AT-05): SpiceDB and Kafka behind the `backbone-tests` compose profile | `test_spicedb_projection_pg.py`, `test_backbone_pg.py` | Built. Five tests that used to skip now run. Found and fixed consumers failing on a fresh broker (`ensure_topics`) |
| I-8 | **Overlay proof** (P0-01, AT-01, BG-09): an HRIS provisions a person; their own IdP authenticates them; they hold a grant and delegate to an agent with a mandate; decisions allow and deny correctly and leave receipts; a leaver event revokes the person and the agent's delegation and blocks the IdP login | `test_overlay_proof_pg.py` | Built with a signing fake IdP. A real incumbent IdP and application are still required for BG-09. Found and fixed: the decision endpoint ignored `context.mandate_id` |
| I-9 | **Revocation latency** (P0-05, AT-06, MPR-10), **erasure and replay** (P0-06, AT-10, BG-10), **authority algebra** (P0-02, BG-04) | existing component and property tests, now linked | Built in-cell. AZ-loss chaos needs a multi-cell environment |
| I-10 | **Decision-path load baseline** (P0-09, AT-16, BG-06) | `tests/load/decision-gate/RESULTS.md`, `tools/scripts/decision_baseline.py` | Measured. **The gate is not met** (about 40 decisions/s per API process, p50 71 ms); the order of work is recorded there |

### Still open, and what each needs

| Item | Needs |
|---|---|
| Decision-path performance (P0-09, BG-06, BG-08) | The tuning and extraction work in `RESULTS.md`, then a PERF-cell soak at 2x Stage A |
| Real incumbent IdP and application overlay proof (BG-09) | A deployed cell and a real IdP tenant (Entra, Okta, Keycloak as a stand-in) |
| Regional failover rehearsal, restore proof, DR exercise (AT-13, G3) | Two or more cells and real backups |
| Signed artifacts and admission (P0-07, MPR-02, DEVSEC) | CI image signing, SBOM, provenance and an admission controller in the deployed cluster |
| Conformance (AT-15, MPR-09, G6) | The OpenID suite against a deployed cell (`tests/conformance`) |
| External penetration test (G4), counsel's legal-role map (P0-08, BG-07), Architecture Review Board ratification (BG-01) | People and third parties |
| Acceptance tests with no test yet | 160 of 760 distinct acceptance tests (the register has 792 rows: CRYP-01..32 appear twice); 600 are linked to tests and in progress, none verified. About 100 of the 160 need a deployed environment (load at 2x Stage A, chaos, failover, restore, conformance, rehearsals), 23 are front-end checks (UX), and about 35 are features not built yet: bulk evidence export (API-13), OpenID IDA `verified_claims` (VERIFY-25), cross-cell event bridge and loop guard (EVT-16), cross-tenant credential trust test (VC-26), tenant deletion and residency evidence (TEN-25, TEN-32), signal reconciliation and cross-cell revocation (SIG-21, 22, 25), incident evidence preservation (OPS-20) and others |

## P0 gaps and the decision that closes each

| P0 gap | Closed by | Proof expected here |
|---|---|---|
| P0-01 Overlay without rip-and-replace | [DEC-A1](phase-a-architecture-baseline.md) | End-to-end action with external IdP + agent: grant and mandate enforced, revocation propagates, DecisionReceipt generated, no identity migration |
| P0-02 Authority algebra | [DEC-C1](phase-c-domain-and-authority-core.md) | Property tests: attenuation, no privilege stitching, revocation cascades |
| P0-03 Mandate overspend | [DEC-C5](phase-c-domain-and-authority-core.md) | 10k-concurrency test with crash injection; no bound exceeded; full reconciliation |
| P0-04 Stale projection grants | [DEC-C1](phase-c-domain-and-authority-core.md), [DEC-C4](phase-c-domain-and-authority-core.md) | A known-stale projection never produces a new R2/R3 ALLOW |
| P0-05 Revocation latency | [DEC-B1](phase-b-requirements-contract.md) | p99 propagation per risk class under injected regional failure |
| P0-06 Evidence vs. erasure | [DEC-F1](phase-f-privacy-evidence-and-security.md), [DEC-F2](phase-f-privacy-evidence-and-security.md) | PII unresolvable after erasure, E3 replay still passes, erased subjects stay erased after restore |
| P0-07 Insider and supply chain | [DEC-F4](phase-f-privacy-evidence-and-security.md), [DEC-G4](phase-g-enterprise-integration-and-operations.md) | No standing data access; unsigned or tampered artifacts rejected at admission |
| P0-08 Legal roles | [DEC-F3](phase-f-privacy-evidence-and-security.md) | Counsel-approved role map; every launch flow has a `processing_role_assignment` |
| P0-09 Design point and unit cost | [DEC-B2](phase-b-requirements-contract.md), [DEC-B3](phase-b-requirements-contract.md) | 2× Stage A load test meets SLOs; unit costs published |

## Other gates proved here

- Cell failover rehearsal with fenced writer and zero double-spend; quarterly DR exercises before any Tier-1 claim ([DEC-G1](phase-g-enterprise-integration-and-operations.md)).
- OpenID conformance for every claimed profile before the claim is made (G6, [DEC-E1](phase-e-identity-protocols-and-assurance.md)).
- Telemetry PII/secret audit and residency match ([DEC-G5](phase-g-enterprise-integration-and-operations.md)).
- Traceability: requirement → implementation → test → result → evidence link in [docs/traceability/](../traceability/README.md).
