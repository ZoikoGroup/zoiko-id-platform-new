# ADR-0018: AWS for Stage A, with one account per production cell

- **Status:** Proposed
- **Owner:** SRE / Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0011 (cell topology, edge provider)

## Context
The specs require regional cells, HSM-backed keys (FIPS 140-3), WORM evidence storage, managed Kafka and Postgres, and residency evidence. The cloud provider is not specified.

## Decision
- **AWS** for Stage A: EKS, Aurora PostgreSQL, MSK, ElastiCache for Valkey, OpenSearch, S3 Object Lock (compliance mode), KMS with a CloudHSM custom key store, Route 53 + Global Accelerator + WAF/Shield.
- AWS Organizations / Control Tower. **One account per production cell** (`cell-uk-1-prod`, `cell-eu-1-prod`), a `global-plane-prod` account, and separate lower-environment, security and log-archive accounts.
- All infrastructure in **OpenTofu**, with modules `env-baseline`, `global-plane` and `cell`.
- Cloud-specific code stays behind `zid-crypto`, storage and messaging adapters, so a later sovereign or alternative-cloud cell is possible (W6).

- **Edge provider (Deployment spec §22):** AWS edge only for Stage A. A second edge/DNS provider is added before any Tier-1 / 99.995% contractual claim (ADR-0011 §7).

## Consequences
- The UK has a single AWS region, so UK residency DR is an open question (architecture §21 Q1; ADR-0011 §6).
- PQ support in KMS/CloudHSM must be confirmed before PQC-1/PQC-2 (ADR-0009).
