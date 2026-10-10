# ADR-0030: Observability stack and telemetry residency

- **Status:** Proposed
- **Owner:** SRE Lead + Privacy Engineering Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Observability, SRE, Incident Response & Operational Readiness spec (OpenTelemetry contract, retention); Multi-Tenant/Residency spec §6, §18; Service spec §24
- **Sources:** Observability/SRE/IR spec §0, §3, retention and redaction rules; Observability/SRE/Resilience & DR spec; Multi-Tenant/Residency spec §6 (coverage matrix), §18; Evidence spec §0.2 (evidence ≠ logs)

## Context
Residency claims are only valid if logs, traces, metrics and security telemetry also stay within the tenant's profile. Telemetry is a common accidental PII leak. The specs require OpenTelemetry and give rules, but not products.

## Decision

### 1. Stack, per cell
- **OpenTelemetry SDKs** in every service (`zid-telemetry`) send data to **regional OTel Collectors**, which handle normalization, redaction and residency-aware routing.
- **Backends:** Prometheus/Mimir (metrics), Loki (logs), Tempo (traces) and Grafana, **all running inside the cell**. Security telemetry goes to a cell-local or regional SIEM.
- **Fleet view:** the global view receives **only aggregated health metrics** that can't reconstruct tenant personal data.
- **Third-party observability SaaS** is allowed only where its ingestion, storage, support and subprocessor locations satisfy the tenant profile.

### 2. What may and may not appear
- **Evidence and audit records are separate data products.** Logs are never evidence (ADR-0024).
- **Prohibited** in logs, spans and metrics: raw request/response bodies, tokens, credentials, secrets, verification evidence, prompts, raw policy and PII. **Only opaque IDs.**
- **Redaction happens twice:** in the SDK and again in the Collector pipeline.
- **Metric labels** are cardinality-controlled. High-cardinality IDs go only in logs/traces, and only where allowlisted.
- **Crash and core dumps** are classified at the highest data class they could contain, and stored in-region.

### 3. Sampling and retention
- T0/T1 request paths are traced at a rate sufficient for SLO analysis. **Errors and selected high-risk transactions are kept by tail sampling.**
- **Sampling never suppresses mandatory security or evidence records**, which have their own retention paths.
- Retention is per signal and shorter than evidence retention, unless an operational or security need justifies longer. Security logs use a protected retention path.

### 4. Required trust SLIs (from day one)
- Decision latency per risk class
- Outcome and reason-code rates
- Revocation propagation per class
- Mandate conflicts
- Outbox, projection and evidence lag
- Checkpoint age
- Fail-closed counts
- Key and certificate rotation health

## Acceptance
- A PII/secret audit of production-like telemetry passes (Observability readiness check).
- Telemetry destinations match each cell's residency profile.
- Global observability failure doesn't make a healthy cell unhealthy (ADR-0011).

## Consequences
`zid-telemetry` ships the redaction allowlist. Adding a new span or log attribute key needs review.
