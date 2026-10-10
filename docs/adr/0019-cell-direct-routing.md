# ADR-0019: Cell-direct endpoints via discovery; global plane out of the hot path

- **Status:** Proposed
- **Owner:** SRE / Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0011 §1 (global plane out of the hot path)

## Context
The specs say global routing and control services must not be a synchronous dependency for an already-routed R2/R3 decision, and that stale routing must be fenced by `placement_epoch`.

## Decision
- The global plane serves **discovery/bootstrap only**. It resolves a tenant (by alias host, client ID or issuer) through the Tenant Locator and returns a signed `{cell_endpoint, placement_epoch, ttl}`.
- SDKs, PEPs and BFFs call the **cell endpoint directly** (for example `uk-1.api.id.zoiko.com`). Tokens carry signed `tid` and `cell` claims.
- The cell ingress re-derives the tenant and checks the epoch. A wrong or stale cell returns `CELL_ROUTING_STALE` with the new location, and clients re-discover.
- The global edge can proxy or redirect for clients that can't do discovery (legacy SAML flows), but runtime SDKs must not depend on it.

## Consequences
- No extra global hop per decision, and a global-plane outage doesn't affect routed traffic.
- SDKs must implement discovery caching and re-discovery on `CELL_ROUTING_STALE`.
