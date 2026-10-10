# ADR-0023: Temporal for long-running workflows and sagas

- **Status:** Proposed
- **Owner:** Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

## Context
Tenant onboarding, verification sessions, credential issuance, data-subject requests, erasure, approvals and tenant migration are multi-step sagas. They must be idempotent, survive restarts and use explicit compensation (Service spec §13).

## Decision
- Use **Temporal** with the Python SDK. Workflows hold workflow state only. Every step calls the authoritative service and records the returned ID and version.
- Temporal is **self-hosted per cell** on its own Aurora cluster, so workflow payloads stay in residency, **Q4 closed by ZID-TENANCY-001 §6, §20:** workflow state is tenant processing, so it must pass the residency coverage matrix and the dependency-sovereignty gate. A managed Temporal service is allowed only for a profile whose gate it passes.
- Workflow payloads contain opaque IDs, not PII.
- Workflow state is never an authorization source. Workflows re-check authority and security before protected effects.

## Consequences
- Durable, testable sagas without hand-rolled state machines for orchestration. Domain state machines still live in the domain modules.
