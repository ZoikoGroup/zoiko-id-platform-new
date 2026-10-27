# Phase G: Enterprise integration and platform operations

> **Gate (Build Documentation Guideline §3):** Makes the platform deployable into incumbent estates and operable at Tier-1 standards.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 20 | ZID-OVERLAY-001 | Enterprise Overlay, Integration & Tenant Onboarding |
| 22 | ZID-DEVSECOPS-001 | Infrastructure, Environments & DevSecOps |
| 19a | ZID-RESILIENCE-001 | Deployment Topology, Cells, HA & DR (supplementary to 19; [CR-005](../specs/change-requests/CR-005-master-sequence-reconciliation.md)) |
| 23 | ZID-SRE-001 | Observability, SRE, Resilience & DR |

## Deliverables and status

| ID | Deliverable | Implements | Where | Status |
|---|---|---|---|---|
| G-W0 | **Tenant lifecycle.** DRAFT → ORGANIZATION_VERIFYING → CONFIGURING → INTEGRATING → SHADOW → PILOT → ACTIVE, plus BLOCKED, SUSPENDED, OFFBOARDING, CLOSED; every move with its gate report; OWNER/ADMIN/MEMBER roles, invitations with progress, owner changes under a second administrator | OVERLAY-001 §2–4 | `modules/tenant` | Built — `test_tenant_lifecycle_pg.py`, `test_team_invitations_pg.py` |
| G-W1 | **Organization gate and sign-up.** Registry roots from Companies House / GLEIF with holder binding to a verified legal-name commitment; public company sign-up storing the declared registration, notice acknowledgement and home cell; workspace discovery | OVERLAY-001 §4; ROOT-001 §6, §19 | `modules/organization`, `modules/onboarding/api/signup.py` | Built — `test_onboarding_organization_pg.py`, `test_signup_pg.py` |
| G-W2 | **Domain verification.** DNS TXT and HTTPS challenges, one verified claimant per domain, consumer domains refused, periodic re-verification | OVERLAY-001 §5 | `modules/tenant` (domains) | Built — `test_tenant_lifecycle_pg.py` |
| G-W3 | **Integration registry.** Applications and PEP profiles, connectors with health, freshness, write-only credentials and quarantine, immutable mapping releases with simulation, multi-IdP routing | OVERLAY-001 §8–11, §14–16 | `modules/integration` | Built — `test_integration_registry_pg.py`, `test_mapping_routing_pg.py` |
| G-W4 | **Onboarding plan, shadow and pilot.** Plan object, readiness gates, shadow observations with exit criteria, pilot stages, activation and rollback; overlay verdicts on the AuthZEN path | OVERLAY-001 §12, §17–20 | `modules/onboarding`, `modules/decision/api/authzen.py` | Built — `test_onboarding_plan_pg.py`, `test_overlay_rules.py` |
| G-W5 | **HR lifecycle and offboarding.** Joiner/mover/leaver/leave events; company offboarding drains integrations and revokes sessions | OVERLAY-001 §2, §22 | `modules/provisioning`, `modules/onboarding/application/offboarding.py` | Built — `test_hr_lifecycle_offboarding_pg.py` |
| G-W6 | **Global locator and signed discovery.** Signed directory records verified against pinned cell keys, signed discovery documents, placement-epoch fencing (`CELL_ROUTING_STALE`) | DEC-G3; TENANCY-001 §14 | `modules/locator`, `modules/discovery`, `modules/tenant/application/directory.py` | Built — `test_locator_discovery_pg.py` |
| G-W7 | **Governed recovery.** Failover, migration, failback and restore runbooks as enforced step sequences; residency, replica, key and capacity gates; epoch advance and writer fencing; reservation reconciliation; cell quarantine; DR exercises | DEC-G1; RESILIENCE-001 §11–20, §25–30 | `modules/resilience` | Built — `test_resilience_operations_pg.py` |
| G-W8 | **SRE and observability.** Telemetry redaction, governed metrics, W3C trace context, journey SLIs; service catalogue with generated burn-rate and invariant alerts; runbooks RB-01–RB-18; incidents with PIR, readiness reviews, change freezes and change policy | DEC-G5; SRE-001 §1–6, §15–30 | `libs/zid-telemetry`, `modules/operations`, `backend/config/operations`, `deploy/observability`, `docs/runbooks` | Built — `test_telemetry.py`, `test_operations_catalogue.py`, `test_operations_pg.py` |
| G-W9 | **Supply chain, environments and deployment.** CI security scans, signing, SBOM, provenance, admission policy, infrastructure as code | DEC-G2, DEC-G4; DEVSECOPS-001 | `.github/workflows`, `deploy/` | Partly — CI pinned by SHA with step timeouts. Deployment is being done separately on **GCP**; DEC-G2 (AWS) needs a superseding decision |
| G-W10 | **Product surfaces.** Company sign-up, company verification, onboarding wizard, domains, applications, integrations, company settings, overview; Operator Console cells, recovery operations, DR exercises, services and SLOs, incidents | UX-001 §5, §15, §19, §21 | `frontend/apps/web`, `frontend/apps/operator` | Built — typecheck, lint and `next build` of both apps |

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-G1](#dec-g1-cell-topology-failure-domains-and-recovery-tiers) | Cell topology, failure domains and recovery tiers | SRE / Platform Lead + Chief Architect | Proposed |
| [DEC-G2](#dec-g2-aws-for-stage-a-with-one-account-per-production-cell) | AWS for Stage A, with one account per production cell | SRE / Platform Lead | Proposed |
| [DEC-G3](#dec-g3-cell-direct-endpoints-via-discovery-global-plane-out-of-the-hot-path) | Cell-direct endpoints via discovery; global plane out of the hot path | SRE / Platform Lead | Proposed |
| [DEC-G4](#dec-g4-software-supply-chain-cicd-and-admission-control) | Software supply chain, CI/CD and admission control | DevSecOps / SRE Lead + Security Lead | Proposed |
| [DEC-G5](#dec-g5-observability-stack-and-telemetry-residency) | Observability stack and telemetry residency | SRE Lead + Privacy Engineering Lead | Proposed |

---

### DEC-G1: Cell topology, failure domains and recovery tiers

- **Status:** Proposed
- **Owner:** SRE / Platform Lead + Chief Architect (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Deployment Topology spec RES-W0 ("cell reference architecture, plane boundaries, tenant placement model and failure-domain ADRs"); Deployment spec §22 (edge provider choice)
- **Sources:** Deployment Topology, HA & DR spec §0.2, §2–6, §9–17, §22; Multi-Tenant/Cell/Residency spec §0.1, §3, §9, §15; Back-End Target Architecture §15–16

#### Context
The 99.995% target leaves about 26 minutes of downtime a year. AZ failures must therefore recover in seconds, and cell disasters in minutes. Correctness (no split-brain, no double-spend, residency) must hold during every failure.

#### Decision

##### 1. Failure domains

| Domain | Rule |
|---|---|
| **Cell** | **The primary blast-radius unit.** One cell's failure never takes down another. Per-cell compute, databases, brokers, caches, credentials, quotas and rate limits are independent. |
| **AZ** | ≥2 AZs per cell (3 where available). Stateless tiers run active-active with N+2 replicas; stateful tiers use quorum. Normal AZ loss needs no DNS or manual action. |
| **Tenant** | A noisy or compromised tenant is throttled or isolated (dedicated shard or pool) before it can affect the fleet. |
| **Global plane** | Never a synchronous dependency of an already-routed decision. Cells keep running on cached signed placement and the last known good config if it's lost. |
| **Release** | Rollouts are staged by cell. A bad release can be halted per cell. Fleet-wide admin actions need canary and stop conditions. |

##### 2. Planes
Data, security, evidence, control, management and analytics planes are separated as described in architecture §5. **The security plane uses a priority path and is never starved by bulk work. Analytics failure never affects decisions.**

##### 3. Consistency classes

| Class | Data | Consistency |
|---|---|---|
| C0 | Derived, cache, analytics | Eventual; rebuildable |
| C1 | Profiles, config | Read-after-write / bounded staleness |
| C2 | Sessions, security state, relationships | Strong for protected use; stale copies never create a new R2/R3 ALLOW |
| C3 | Mandate budgets, writer epochs, root-critical state, evidence commit metadata | Serializable single writer; **never multi-region writers** |

##### 4. Recovery tiers

| Tier | Subsystem | RTO | RPO |
|---|---|---|---|
| T0 | Edge / stateless runtime | ≤60 s | 0 |
| T1 | Authentication, PDP, security-state runtime | ≤5 min (cell disaster) | ≤5 s, revision-convergent |
| T2 | PostgreSQL C2 tenant state | ≤5 min | ≤30 s target (stricter per tenant possible) |
| T3 | Mandate C3 writer / critical authority | ≤5 min | 0 where a synchronous approved pair exists; **otherwise no automatic unsafe failover** |
| T4 | Evidence E3 ledger | ≤15 min ingest/replay | 0 for acknowledged synchronous E3 |
| T5 | Analytics, search, reporting | ≤24 h | ≤24 h (rebuild) |

**RPO applies to a declared cell or regional disaster.** Normal HA failures lose no acknowledged state. Where cross-region RPO 0 isn't achievable, **C3 operations prefer temporary unavailability over split-brain or unprovable consumption.**

##### 5. Failover gate (no split-brain)
Cell disaster failover is a declared incident, not an automatic reaction to transient failure. The runbook steps are:
1. Declare
2. Freeze and fence the old writer
3. Assess replicated revisions
4. Promote data under a new epoch
5. Activate recovery keys
6. Advance the placement/writer epoch
7. Validate
8. Route
9. Reconcile
10. Observe

The recovery cell **cannot accept authoritative C3 writes** in its normal state. If the old writer can't be fenced, protected writes stay stopped.

##### 6. Residency-bound recovery
- Recovery honors the tenant's residency profile. **There is no emergency failover to an unapproved jurisdiction.**
- Sovereign tenants that forbid cross-jurisdiction failover must use an approved second location **or explicitly accept a different RTO/RPO**.
- This is the basis for open question Q1 (UK: AWS has a single region).

##### 7. Edge provider (Deployment spec §22)
- **Stage A:** AWS edge (Route 53, Global Accelerator, WAF/Shield; DEC-G2).
- **Before any Tier-1 / 99.995% contractual claim:** add a **second edge/DNS provider**, because the spec prefers multiple edge providers for mature Tier-1 service.
- DDoS/WAF/rate limiting sits in front of expensive identity and verification work. Health checks distinguish network failure from application failure, to avoid failover storms.

#### Acceptance
- AZ-loss chaos test keeps service within SLO with no manual action.
- Cell failover rehearsal meets the T1–T4 objectives with a fenced writer and zero double-spend.
- Failback is rehearsed.
- Backup restore is proven: "backups are not DR; replicas are not backups."
- DR exercises run quarterly before any Tier-1 claim.

#### Consequences
- The OpenTofu `cell` module is the unit of deployment.
- Each cell is self-sufficient: runtime, data, keys, telemetry and evidence.
- Recovery cells are pre-provisioned for T1–T3.

---

### DEC-G2: AWS for Stage A, with one account per production cell

- **Status:** Proposed
- **Owner:** SRE / Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-G1 (cell topology, edge provider)

#### Context
The specs require regional cells, HSM-backed keys (FIPS 140-3), WORM evidence storage, managed Kafka and Postgres, and residency evidence. The cloud provider is not specified.

#### Decision
- **AWS** for Stage A: EKS, Aurora PostgreSQL, MSK, ElastiCache for Valkey, OpenSearch, S3 Object Lock (compliance mode), KMS with a CloudHSM custom key store, Route 53 + Global Accelerator + WAF/Shield.
- AWS Organizations / Control Tower. **One account per production cell** (`cell-uk-1-prod`, `cell-eu-1-prod`), a `global-plane-prod` account, and separate lower-environment, security and log-archive accounts.
- All infrastructure in **OpenTofu**, with modules `env-baseline`, `global-plane` and `cell`.
- Cloud-specific code stays behind `zid-crypto`, storage and messaging adapters, so a later sovereign or alternative-cloud cell is possible (W6).

- **Edge provider (Deployment spec §22):** AWS edge only for Stage A. A second edge/DNS provider is added before any Tier-1 / 99.995% contractual claim (DEC-G1 §7).

#### Consequences
- The UK has a single AWS region, so UK residency DR is an open question (architecture §21 Q1; DEC-G1 §6).
- PQ support in KMS/CloudHSM must be confirmed before PQC-1/PQC-2 (DEC-F6).

---

### DEC-G3: Cell-direct endpoints via discovery; global plane out of the hot path

- **Status:** Proposed
- **Owner:** SRE / Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-G1 §1 (global plane out of the hot path)

#### Context
The specs say global routing and control services must not be a synchronous dependency for an already-routed R2/R3 decision, and that stale routing must be fenced by `placement_epoch`.

#### Decision
- The global plane serves **discovery/bootstrap only**. It resolves a tenant (by alias host, client ID or issuer) through the Tenant Locator and returns a signed `{cell_endpoint, placement_epoch, ttl}`.
- SDKs, PEPs and BFFs call the **cell endpoint directly** (for example `uk-1.api.id.zoiko.com`). Tokens carry signed `tid` and `cell` claims.
- The cell ingress re-derives the tenant and checks the epoch. A wrong or stale cell returns `CELL_ROUTING_STALE` with the new location, and clients re-discover.
- The global edge can proxy or redirect for clients that can't do discovery (legacy SAML flows), but runtime SDKs must not depend on it.

#### Consequences
- No extra global hop per decision, and a global-plane outage doesn't affect routed traffic.
- SDKs must implement discovery caching and re-discovery on `CELL_ROUTING_STALE`.

---

### DEC-G4: Software supply chain, CI/CD and admission control

- **Status:** Proposed
- **Owner:** DevSecOps / SRE Lead + Security Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture P0-07 (SLSA Build L3); Infrastructure, Environments & DevSecOps spec §5–15, §18–20; Security spec §17
- **Sources:** DevSecOps spec §1, §5, §6, §7, §8, §9, §10, §13, §14, §15, §19, §20; Security spec §13 (separation of duties), §17

#### Context
P0-07 treats insiders and supply-chain compromise as real adversaries. The DevSecOps spec says artifact signature and attestation tooling is "an implementation choice, not a domain dependency", so the choices are recorded here.

#### Decision

##### 1. Source control
- **Protected default branch:** no direct push.
- **Reviews:** a reviewed PR is required. **CODEOWNERS Security review** is required for security, crypto, IAM and trust-kernel paths (see `.github/CODEOWNERS`).
- **Status checks:** required checks can't be bypassed by normal contributors.
- **Identity:** SSO/MFA identities only. Release tags are signed.
- **Secrets:** a detected secret is treated as **compromised and rotated**, not just removed.
- **AI-assisted code:** gets the same review and gates as human-authored code.

##### 2. Pipeline (GitHub Actions)
Each change runs: lint/type, unit/property tests, SAST (Semgrep), secret scan (gitleaks), IaC scan, SCA + license policy (pip-audit, pnpm audit), an **isolated ephemeral-runner build**, SBOM generation, image scan (Trivy), integration/contract tests, provenance attestation, **signing by digest**, and an immutable publish.

**Build-runner rules:**
- runners have no production network path
- untrusted and fork PRs get no secrets or signing credentials
- toolchains are pinned
- the builder image is itself signed

##### 3. Provenance and signing
- **Target: SLSA Build L3 under SLSA v1.2.** Provenance is generated by the hardened build platform (GitHub Actions reusable workflows with OIDC-attested provenance).
- **cosign** keyless (workload-bound OIDC) signing, or KMS-protected signing identities for release artifacts.
- **SBOMs:** **CycloneDX 1.7** is primary and SPDX 3.0 is the export format. They're generated in the trusted build, including a cryptographic BOM where supported.
- **CI cloud access** goes through **OIDC federation to AWS**. Long-lived cloud keys in CI are prohibited.

##### 4. Promotion: no rebuild
Promotion runs DEV → INT/QA → SEC/PERF → PREPROD → PROD with the **same digest**. It verifies the signature, provenance, SBOM, policy and migration/rollback readiness, then gets environment approval and passes admission.

##### 5. Admission and policy as code
- **Kyverno** in every cluster enforces:
  - an approved registry
  - digest pinning
  - a valid signature and SLSA provenance from an allowed builder, repository and source
  - Pod Security Standards **Restricted**
  - resource limits
  - workload identity
  - network policy
- **OpenTofu policy checks** (Checkov/OPA in CI) cover infrastructure: public exposure, encryption, logging, IAM, regions, backup, deletion protection and residency.
- Production-protected namespaces **fail closed** if admission policy is unavailable.

##### 6. Separation of duties and break-glass
- A code author can't unilaterally merge, approve and promote a critical change.
- Production approvers are distinct from authors for high-risk changes.
- The emergency release path is still signed and admitted. It notifies Security immediately and requires a post-review.

#### Acceptance
- An unsigned image, a wrong builder or a tampered digest is rejected at admission.
- Preprod and prod run the identical digest.
- A fork PR can't reach signing or deploy credentials.
- A planted secret is detected and the rotation workflow fires.

#### Consequences
- These pipelines live in `.github/workflows/` and the policies in `deploy/policies/kyverno/`.
- The release manifest (DevSecOps spec §26) is the release evidence record.

---

### DEC-G5: Observability stack and telemetry residency

- **Status:** Proposed
- **Owner:** SRE Lead + Privacy Engineering Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Observability, SRE, Incident Response & Operational Readiness spec (OpenTelemetry contract, retention); Multi-Tenant/Residency spec §6, §18; Service spec §24
- **Sources:** Observability/SRE/IR spec §0, §3, retention and redaction rules; Observability/SRE/Resilience & DR spec; Multi-Tenant/Residency spec §6 (coverage matrix), §18; Evidence spec §0.2 (evidence ≠ logs)

#### Context
Residency claims are only valid if logs, traces, metrics and security telemetry also stay within the tenant's profile. Telemetry is a common accidental PII leak. The specs require OpenTelemetry and give rules, but not products.

#### Decision

##### 1. Stack, per cell
- **OpenTelemetry SDKs** in every service (`zid-telemetry`) send data to **regional OTel Collectors**, which handle normalization, redaction and residency-aware routing.
- **Backends:** Prometheus/Mimir (metrics), Loki (logs), Tempo (traces) and Grafana, **all running inside the cell**. Security telemetry goes to a cell-local or regional SIEM.
- **Fleet view:** the global view receives **only aggregated health metrics** that can't reconstruct tenant personal data.
- **Third-party observability SaaS** is allowed only where its ingestion, storage, support and subprocessor locations satisfy the tenant profile.

##### 2. What may and may not appear
- **Evidence and audit records are separate data products.** Logs are never evidence (DEC-F2).
- **Prohibited** in logs, spans and metrics: raw request/response bodies, tokens, credentials, secrets, verification evidence, prompts, raw policy and PII. **Only opaque IDs.**
- **Redaction happens twice:** in the SDK and again in the Collector pipeline.
- **Metric labels** are cardinality-controlled. High-cardinality IDs go only in logs/traces, and only where allowlisted.
- **Crash and core dumps** are classified at the highest data class they could contain, and stored in-region.

##### 3. Sampling and retention
- T0/T1 request paths are traced at a rate sufficient for SLO analysis. **Errors and selected high-risk transactions are kept by tail sampling.**
- **Sampling never suppresses mandatory security or evidence records**, which have their own retention paths.
- Retention is per signal and shorter than evidence retention, unless an operational or security need justifies longer. Security logs use a protected retention path.

##### 4. Required trust SLIs (from day one)
- Decision latency per risk class
- Outcome and reason-code rates
- Revocation propagation per class
- Mandate conflicts
- Outbox, projection and evidence lag
- Checkpoint age
- Fail-closed counts
- Key and certificate rotation health

#### Acceptance
- A PII/secret audit of production-like telemetry passes (Observability readiness check).
- Telemetry destinations match each cell's residency profile.
- Global observability failure doesn't make a healthy cell unhealthy (DEC-G1).

#### Consequences
`zid-telemetry` ships the redaction allowlist. Adding a new span or log attribute key needs review.
