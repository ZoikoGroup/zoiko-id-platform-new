# ADR-0010: Capacity design point and unit cost

- **Status:** Proposed
- **Owner:** CTO + SRE Lead + Finance (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-10, §17; closes P0-09
- **Sources:** Back-End Target Architecture §17–17.1; System Requirements §5; Deployment Topology spec §13–13.1; Data Model §13.1; Readiness plan §26 (performance gate)

## Context
P0-09: without a design point, SLOs can't be tested and the product can't be priced. The external gap analysis proposed a very large envelope. That is useful as a ceiling, but must not become an uncosted day-one requirement.

## Decision

### 1. Staged design points

| Stage | Principals | Authz decisions/s global | Per-cell peak | Token issuance/s | R2/R3 receipts/s |
|---|---|---|---|---|---|
| **A: Launch / first enterprise (ratified by this ADR)** | 10M | 20,000 | 5,000 | 10,000 | 2,000 |
| B: Enterprise scale | 100M | 250,000 | 50,000 | 25,000 | 10,000 |
| C: Strategic envelope | 500M | 1,000,000 | 200,000 | 100,000 | 50,000 |

- **Stage A is the current engineering design point.**
- **Stage C is a no-rewrite target, not a provisioning commitment.** IDs, APIs and partitioning must never block evolution to it.
- **Moving to B or C requires** a signed commercial forecast, load-test evidence and a unit-cost review.

### 2. Qualification
- Each stage must **sustain 2× its design point** long enough to expose saturation, rebalancing and failover behavior, while meeting the class SLOs.
- Tests report p50/p95/p99 with cold-cache, failure and noisy-neighbor cases.
- Load tests include skewed tenant traffic, hot mandate rows, cache loss, dependency latency and AZ evacuation.

### 3. Headroom and admission
- Normal peak keeps **≥30% cell compute headroom** and the ability to survive the loss of one AZ.
- A cell **stops accepting new tenant placements** before projected one-AZ-loss capacity passes the saturation threshold.
- Autoscaling does not replace database, broker and KMS quota planning.

### 4. Required unit economics (measured per cell and tenant tier)
- Cost per million AuthZEN decisions, by risk class
- Cost per million token issuances/exchanges
- Cost per million compact and R3 receipts, including storage retention
- Cost per active principal and per active organization per month
- Cost per verification/credential presentation, with and without provider fees
- Cost per SSF event transmitted/received
- Minimum annual cost of a dedicated/sovereign cell

### 5. Pricing gate
**No public enterprise pricing is finalized** until Stage A unit costs are measured in a production-representative environment and the largest loss-making workload is known.

## Acceptance (P0-09)
- The 2× Stage A test meets the latency and reliability targets.
- Internal unit costs are published.

## Consequences
- ADR-0021 (decision-service performance gate) is the first instance of this qualification.
- The METER module emits the cost-attribution meters from W1.
