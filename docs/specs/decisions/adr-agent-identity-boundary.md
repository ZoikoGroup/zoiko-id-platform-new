# ADR (DRAFT, unsigned): Zoiko iD owns agent identity, authority and evidence contracts

Status: **DRAFT for signature by the CTO and Platform owner. Not in force until signed.** Needed for gate G0 and decisions AG2-D07 and D-06.

## Decision proposed

Zoiko iD, not ZoikoVertex, owns the contracts for agent identity (registry, sponsor, workload or API-client binding), agent authority (leases, approvals, hard-deny list) and agent evidence (hash-chained events). A runtime such as ZoikoVertex may host an agent, and calls the same gateway as any other client.

## Evidence in the repository

- Registry, bindings and kill paths: CR-066, CR-093 and the machine module.
- Authority and governance: CR-091.
- First agent and review queue: CR-092.
- API surface: [api-inventory-agents.md](api-inventory-agents.md), generated from the committed contracts.

## What signing it means

The inventory is checked against the live environment by a named person (D-06), differences are recorded, and the boundary is not redrawn without a new ADR.

## Signatures

| Role | Name | Date |
|---|---|---|
| CTO | | |
| Platform owner | | |
