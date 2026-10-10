# ADR-0007: Legal role and establishment model

- **Status:** Proposed. **Legal must own the decision; engineering owns the mechanism.**
- **Owner:** General Counsel + DPO; engineering co-owner: Privacy Engineering Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-07, §18; closes P0-08
- **Sources:** Back-End Target Architecture §18.1–18.3; Consent/Privacy/Retention spec §1 (Controller/Processor Operating Model, role map record); Readiness plan §24.1 (no-go conditions); Multi-Tenant/Residency spec (jurisdiction packs)

## Context
P0-08: certification, liability and legal roles can't be established from feature lists. Zoiko iD cannot carry one universal "processor" label. The same data can be processed under different roles for different purposes.

## Decision

### 1. Roles are assigned per processing activity, not per tenant
Every service flow records:
- who determines purpose and means
- the source of identity evidence
- the recipient
- the retention owner
- the legal basis context

| Zoiko role | When |
|---|---|
| Controller | Zoiko determines purposes and means (direct Zoiko service, platform security/evidence) |
| Processor | The enterprise customer determines purpose; Zoiko acts on instructions (DPA) |
| Independent controllers | Zoiko and another party each determine separate purposes |
| Joint controllers | **Only where counsel determines it**; responsibilities allocated and surfaced |
| Subprocessor / recipient | Downstream providers and relying parties, with minimum disclosure and purpose binding |

### 2. Role map record (the deployable artifact)
```
processing_role_assignment
  processing_activity_id   tenant_id / product   data_controller_principal_id
  zoiko_role: CONTROLLER | PROCESSOR | JOINT_CONTROLLER | INDEPENDENT_CONTROLLER
  processor_principal_ids[]   recipient_classes[]   jurisdiction_pack_id
  contract_or_dpa_ref   effective_from / valid_to   approval_ref
```

### 3. Jurisdiction packs
- Counsel approves the legal classification as a **versioned jurisdiction pack**, which becomes deployable policy and configuration.
- **Engineers never infer the legal answer from a country code.**
- Packs set rights, retention and transfer rules. They are versioned independently of the code.

### 4. Flows that must be assessed (Architecture §18.1)

| Flow | Likely posture | Question counsel must answer |
|---|---|---|
| Enterprise authentication overlay | Processor in many deployments | Does Zoiko determine any independent fraud/security purpose? |
| Reusable verification | May create independent-controller or statutory duties | Who may rely, who is liable for stale evidence, and what is retained? |
| Credential issuance | Issuer role plus controller/processor duties by scheme | What authority permits issuing the claim? |
| EUDI relying party | Verifier obligations | Registration, requested attributes, purpose limitation, logging |
| Agent mandate / authority evidence | Customer-directed plus Zoiko security/evidence purposes | How is representation established, and who bears transaction liability? |
| Security telemetry | Zoiko controller interests may apply | Minimize PII; separate the security purpose from analytics |

### 5. Reliance and establishment
- **Reliance:** no "verified once, trusted forever" claim. Every assertion has scope, assurance profile, source, `performed_at`, `valid_until` and refresh semantics.
- **AML reliance:** contracts define who performed CDD, evidence access, refresh duties, liability and jurisdictions. **EU AMLR 2024/1624 applies from 10 July 2027**, and the packs must be ready before customers rely on Zoiko for it.
- **Establishment:** entity or establishment decisions are taken **only where market law or a scheme requires them**. No assumption is taken from proposals; for example, the European Business Wallet Regulation is still a proposal.

## Engineering enforcement
- A processing activity with **no approved role assignment cannot go to production**. CI and release checks verify that every launch flow has a `processing_role_assignment`.
- Readiness no-go condition: an unknown or untested controller/processor role for a launch-tenant flow blocks G4.

## Acceptance (P0-08)
A counsel-approved role map and a dated market/certification plan exist before regulated production.

## Consequences
- `privacy` owns the role map and jurisdiction pack registry.
- The packs themselves are Legal's deliverable, and are open question Q7 in the architecture doc.
