# ADR-0011: Cell topology, failure domains and recovery tiers

- **Status:** Proposed
- **Owner:** SRE / Platform Lead + Chief Architect (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Deployment Topology spec RES-W0 ("cell reference architecture, plane boundaries, tenant placement model and failure-domain ADRs"); Deployment spec §22 (edge provider choice)
- **Sources:** Deployment Topology, HA & DR spec §0.2, §2–6, §9–17, §22; Multi-Tenant/Cell/Residency spec §0.1, §3, §9, §15; Back-End Target Architecture §15–16

## Context
The 99.995% target leaves about 26 minutes of downtime a year. AZ failures must therefore recover in seconds, and cell disasters in minutes. Correctness (no split-brain, no double-spend, residency) must hold during every failure.

## Decision

### 1. Failure domains

| Domain | Rule |
|---|---|
| **Cell** | **The primary blast-radius unit.** One cell's failure never takes down another. Per-cell compute, databases, brokers, caches, credentials, quotas and rate limits are independent. |
| **AZ** | ≥2 AZs per cell (3 where available). Stateless tiers run active-active with N+2 replicas; stateful tiers use quorum. Normal AZ loss needs no DNS or manual action. |
| **Tenant** | A noisy or compromised tenant is throttled or isolated (dedicated shard or pool) before it can affect the fleet. |
| **Global plane** | Never a synchronous dependency of an already-routed decision. Cells keep running on cached signed placement and the last known good config if it's lost. |
| **Release** | Rollouts are staged by cell. A bad release can be halted per cell. Fleet-wide admin actions need canary and stop conditions. |

### 2. Planes
Data, security, evidence, control, management and analytics planes are separated as described in architecture §5. **The security plane uses a priority path and is never starved by bulk work. Analytics failure never affects decisions.**

### 3. Consistency classes

| Class | Data | Consistency |
|---|---|---|
| C0 | Derived, cache, analytics | Eventual; rebuildable |
| C1 | Profiles, config | Read-after-write / bounded staleness |
| C2 | Sessions, security state, relationships | Strong for protected use; stale copies never create a new R2/R3 ALLOW |
| C3 | Mandate budgets, writer epochs, root-critical state, evidence commit metadata | Serializable single writer; **never multi-region writers** |

### 4. Recovery tiers

| Tier | Subsystem | RTO | RPO |
|---|---|---|---|
| T0 | Edge / stateless runtime | ≤60 s | 0 |
| T1 | Authentication, PDP, security-state runtime | ≤5 min (cell disaster) | ≤5 s, revision-convergent |
| T2 | PostgreSQL C2 tenant state | ≤5 min | ≤30 s target (stricter per tenant possible) |
| T3 | Mandate C3 writer / critical authority | ≤5 min | 0 where a synchronous approved pair exists; **otherwise no automatic unsafe failover** |
| T4 | Evidence E3 ledger | ≤15 min ingest/replay | 0 for acknowledged synchronous E3 |
| T5 | Analytics, search, reporting | ≤24 h | ≤24 h (rebuild) |

**RPO applies to a declared cell or regional disaster.** Normal HA failures lose no acknowledged state. Where cross-region RPO 0 isn't achievable, **C3 operations prefer temporary unavailability over split-brain or unprovable consumption.**

### 5. Failover gate (no split-brain)
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

### 6. Residency-bound recovery
- Recovery honors the tenant's residency profile. **There is no emergency failover to an unapproved jurisdiction.**
- Sovereign tenants that forbid cross-jurisdiction failover must use an approved second location **or explicitly accept a different RTO/RPO**.
- This is the basis for open question Q1 (UK: AWS has a single region).

### 7. Edge provider (Deployment spec §22)
- **Stage A:** AWS edge (Route 53, Global Accelerator, WAF/Shield; ADR-0018).
- **Before any Tier-1 / 99.995% contractual claim:** add a **second edge/DNS provider**, because the spec prefers multiple edge providers for mature Tier-1 service.
- DDoS/WAF/rate limiting sits in front of expensive identity and verification work. Health checks distinguish network failure from application failure, to avoid failover storms.

## Acceptance
- AZ-loss chaos test keeps service within SLO with no manual action.
- Cell failover rehearsal meets the T1–T4 objectives with a fenced writer and zero double-spend.
- Failback is rehearsed.
- Backup restore is proven: "backups are not DR; replicas are not backups."
- DR exercises run quarterly before any Tier-1 claim.

## Consequences
- The OpenTofu `cell` module is the unit of deployment.
- Each cell is self-sufficient: runtime, data, keys, telemetry and evidence.
- Recovery cells are pre-provisioned for T1–T3.
