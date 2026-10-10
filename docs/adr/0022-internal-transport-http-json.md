# ADR-0022: HTTP/JSON for internal service calls at Stage A

- **Status:** Proposed
- **Owner:** Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

## Context
The Service spec allows HTTP/2+JSON, gRPC/Protobuf, or a controlled mix, but makes the contract semantics mandatory: request ID, trace context, server-derived tenant, epoch, caller identity, idempotency key, If-Match, deadline and schema version.

## Decision
- Internal calls use **HTTP/JSON with FastAPI and OpenAPI-generated Python clients** (httpx with keep-alive pools, orjson).
- They run over Istio ambient mTLS with SPIFFE identities.
- Mandatory headers are carried by `zid-context` (`X-Request-Id`, `traceparent`, `X-Zid-Deadline`, `Idempotency-Key`, `If-Match`, `X-Zid-Schema-Version`). Tenant and caller identity come from the mTLS identity and signed context, never from plain headers.
- Calls inside a deployable unit are in-process via `ports.py`, which removes most network hops.
- Revisit gRPC if the W0 benchmark shows serialization or connection overhead on the decision path.

## Consequences
- One API toolchain (OpenAPI) for both external and internal contracts.
