# ADR-0017: Amazon MSK (Kafka) with a transactional outbox and polling relay

- **Status:** Proposed
- **Owner:** Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0004 §4 (priority lane for revocation)

## Context
The Event spec requires a Kafka-compatible replicated log, CloudEvents, AsyncAPI 3.1, a transactional outbox (no dual writes), at-least-once delivery with idempotent consumers, and a priority lane for security signals.

## Decision
- **Amazon MSK** per cell. Topics are named `zid.<env>.<cell>.<domain>.<class>`, in four lanes: `priority`, `domain`, `evidence` and `integration`.
- Outbox rows are written by `zid-outbox` in the same transaction. The **polling relay** uses `FOR UPDATE SKIP LOCKED`, with the aggregate ID as partition key and an idempotent producer reusing the outbox `event_id`.
- Priority lanes have dedicated relays and consumer pools (target relay latency under 100 ms).
- Consumers deduplicate through an inbox table (`event_id` plus the highest `aggregate_version`).
- Apicurio schema registry with BACKWARD compatibility by default.
- Local development uses Redpanda.

## Consequences
- Simple and testable at Stage A. Debezium/CDC can replace polling at Stage B without contract changes.
