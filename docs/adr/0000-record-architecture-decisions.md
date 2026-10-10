# ADR-0000: Record architecture decisions

- **Status:** Proposed
- **Owner:** Chief Architect (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Deciders:** Architecture Review Board
- **Sources:** Build Documentation Guideline §1, §5; Back-End Target Architecture §23; Engineering Brief §8

## Context
The Build Documentation Guideline requires that material decisions on identity, authority, evidence, privacy, cryptography, tenancy or federation are made through ADRs. Any divergence from an approved canonical spec also needs an ADR and a spec update. Back-End Target Architecture §23.2 adds a baseline gate: *"All ten ADRs ratified with named owner and date."*

## Decision
- ADRs live in `docs/adr/`, numbered `NNNN-title.md`.
- **Required header fields:** Status, **Owner (a named person at ratification)**, Date, Implements (spec ADR / P0 / section), Sources.
- **Body:** Context, Decision, Acceptance, Consequences, and Alternatives where relevant.
- **Number ranges:**

  | Range | Contents |
  |---|---|
  | `0000` | This process ADR |
  | `0001`–`0010` | The ten architecture-baseline ADRs; the number matches spec ADR-01 to ADR-10 |
  | `0011`+ | Further architecture and implementation decisions |

- **Status flow:** Proposed → Accepted → Superseded/Deprecated. Production work relies only on **Accepted** ADRs.
- **When an ADR goes beyond the specs** (where the specs are silent), it marks that content explicitly as an engineering proposal, so the ARB ratifies it consciously.
- **When an ADR finds a spec inconsistency or contradiction,** it names the spec section and includes the matching spec change request.
- ADRs are immutable once accepted. A later change is a new ADR that supersedes the old one.

## Consequences
Decisions become traceable and reviewable in source control, not in chat.
