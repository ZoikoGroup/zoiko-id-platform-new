# ADR-0012: Python 3.13+ and FastAPI for all backend services

- **Status:** Proposed
- **Owner:** Engineering Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

## Context
The specs fix the semantics and data stores but leave the implementation language open. The team has chosen Python. Most services are I/O-bound (database, Kafka, providers, workflows). The decision service has strict latency and throughput targets (see ADR-0021).

## Decision
- Backend services use **Python 3.13+** with **FastAPI**, **Pydantic v2**, **SQLAlchemy 2 (async) + asyncpg**, **Alembic**, uvicorn + uvloop and orjson.
- Tooling: uv workspaces, ruff, mypy `--strict`, pytest, Hypothesis and import-linter.
- The backend ships as **one modular-monolith package and image** (`zoiko-id-platform`, ADR-0031) of hexagonal modules (architecture §6–7). Module boundaries are enforced by `ports.py` Protocols and architecture tests in CI.
- Domain logic (`domain/`) is pure Python with no I/O imports.
- **The only allowed exception:** the Credential module (CRED) may be built as a separate Kotlin/JVM service if the Python EUDI libraries (mdoc, OID4VP/HAIP) are not mature enough. That would be decided by its own ADR at W3 start.

## Consequences
- One language for the team, fast iteration, and OpenAPI generated from code.
- CPU-heavy paths need care: no blocking work on the event loop, and signing goes to KMS or a thread pool.
- Hot-path performance is gated by ADR-0021.

## Alternatives considered
- **Go everywhere:** better tail latency, but weaker SAML/EUDI libraries and not the team's choice.
- **Kotlin everywhere:** strong SAML/EUDI libraries, but heavier runtime and not the team's choice.
