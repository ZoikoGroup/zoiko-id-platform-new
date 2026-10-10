# Canonical specification register

These documents are the **upstream authority** for Zoiko iD (Build Documentation Guideline §1). Engineering artifacts (architecture, ADRs, code) must not contradict an approved spec without an ADR and a spec change request.

- **Production build may rely only on Approved versions** (Guideline §5). Every spec below is currently a Draft or Baseline Candidate; release gate **G0** (Readiness plan §2) requires approval.
- The `.docx` files are stored through Git LFS (see `/.gitattributes`). Because binaries can't be diffed, every new spec version must be logged in this register with what changed.

| # | Doc ID | Title | Version · date | Status | File |
|---|---|---|---|---|---|
| — | ZID-ENG-BRIEF-001 | Engineering Brief | v1.0 · 7 Oct 2026 | — | `Zoiko iD Engineering Brief.docx` |
| — | (Guideline) | Engineering Build Documentation Guideline | v1.0 · 6 Oct 2026 | — | `Zoiko iD Engineering Build Documentation Guideline.docx` |
| 01 | ZID-ARCH-002 | Back-End Target Architecture | v2.0 · 6 Oct 2026 | Baseline Candidate (ARB) | `Zoiko_iD_Back_End_Target_Architecture.docx` |
| 02 | ZID-REQ-001 | System Requirements & NFR | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_System_Requirements_and_NFR_Specification.docx` |
| 03 | ZID-DOM-001 | Canonical Domain Model & Objects | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Canonical_Domain_Model_and_Object_Specification_v1.0.docx` |
| 04 | ZID-DATA-001 | Data Model & Database Schema | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Data_Model_and_Database_Schema_Specification_v1.0.docx` |
| 05 | ZID-API-001 | API & Interface | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_API_and_Interface_Specification_v1.0.docx` |
| 06 | ZID-AUTH-001 | Authentication, Session & Federation | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Authentication_Session_and_Federation_Specification_v1.0.docx` |
| 07 | ZID-AUTHZ-001 | Authorization, Authority Graph & Policy Engine | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Authorization_Authority_Graph_and_Policy_Engine_Specification_v1.0.docx` |
| 08 | ZID-MANDATE-001 | Delegated Authority & Mandate Ledger | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Delegated_Authority_and_Mandate_Ledger_Specification_v1.0.docx` |
| 09 | ZID-MACHINE-001 | AI Agent, Service & Workload Identity | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_AI_Agent_Service_and_Workload_Identity_Specification_v1.0.docx` |
| 10 | ZID-ROOT-001 | Organizational Authority & Root of Trust | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Organizational_Authority_and_Root_of_Trust_Specification_v1.0.docx` |
| 11 | ZID-VERIFY-001 | Identity Verification & Assurance | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Identity_Verification_and_Assurance_Architecture_v1.0.docx` |
| 12 | ZID-VC-001 | Verifiable Credentials, EUDI & Trust Federation | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Verifiable_Credentials_EUDI_and_Trust_Federation_Specification_v1.0.docx` |
| 13 | ZID-SIGNALS-001 | Continuous Evaluation, Revocation & Security Signals | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Continuous_Evaluation_Revocation_and_Security_Signals_Specification_v1.0.docx` |
| 14 | ZID-PRIV-001 | Consent, Privacy, Retention & Data Rights | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Consent_Privacy_Retention_and_Data_Rights_Specification_v1.0.docx` |
| 15 | ZID-EVIDENCE-001 | Decision Evidence, Audit & Provenance | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Decision_Evidence_Audit_and_Provenance_Specification_v1.0.docx` |
| 16 | ZID-EVENTS-001 | Event Architecture, Messaging & Webhooks | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Event_Architecture_Messaging_and_Webhook_Specification_v1.0.docx` |
| 17 | ZID-SEC-001 | Security Architecture, Threat Model & Operator Trust | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Security_Architecture_Threat_Model_and_Operator_Trust_Specification_v1.0.docx` |
| 18 | ZID-CRYPTO-MI-001 ⚠️ | Cryptography, Key Management & Machine Identity | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Cryptography_Key_Management_and_Machine_Identity_Specification_v1.0.docx` |
| 18 | ZID-CRYPTO-001 ⚠️ | Cryptography, Key Management & Secrets | v1.0 · 6 Oct 2026 | Draft | `Zoiko_iD_Cryptography_Key_Management_and_Secrets_Specification_v1.0.docx` |
| 19 | ZID-TENANCY-001 | Multi-Tenant, Regional Cell, Residency & Sovereignty | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Multi_Tenant_Regional_Cell_Residency_and_Sovereignty_Architecture_v1.0.docx` |
| 19a | ZID-RESILIENCE-001 | Deployment Topology, Cells, HA & DR | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Deployment_Topology_Cells_High_Availability_and_Disaster_Recovery_Specification_v1.0.docx` |
| 20 | ZID-OVERLAY-001 | Enterprise Overlay, Integration & Tenant Onboarding | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Enterprise_Overlay_Integration_and_Tenant_Onboarding_Specification_v1.0.docx` |
| 21 | ZID-SVC-001 | Service Decomposition & Internal Interfaces | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Service_Decomposition_and_Internal_Interface_Specification_v1.0.docx` |
| 22 | ZID-DEVSECOPS-001 | Infrastructure, Environments & DevSecOps | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Infrastructure_Environments_and_DevSecOps_Specification_v1.0.docx` |
| 23 | ZID-OPS-001 ⚠️ | Observability, SRE, Incident Response & Operational Readiness | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Observability_SRE_Incident_Response_and_Operational_Readiness_Specification_v1.0.docx` |
| 23 | ZID-SRE-001 ⚠️ | Observability, SRE, Resilience & DR | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Observability_SRE_Resilience_and_Disaster_Recovery_Specification_v1.0.docx` |
| 24 | ZID-UX-001 | Product Console & Engineering UI/UX Wireframes | v1.0 · 7 Oct 2026 | Draft | `Zoiko_iD_Product_Console_and_Engineering_UI_UX_Wireframes_v1.0.docx` |
| 25 | ZID-VERIFY-MASTER-001 | Master Verification, Test, Certification & Production Readiness | v1.0 · 7 Oct 2026 | Final Master Plan (for review) | `Zoiko_iD_Master_Verification_Test_Certification_and_Production_Readiness_Plan.docx` |

⚠️ **Duplicate slots (architecture open question Q2):** the Guideline lists one document each for 18 and 23, but two versions exist.
- **Doc 18:** ZID-CRYPTO-MI-001 (7 Oct) has post-quantum sections identical to ZID-CRYPTO-001 (6 Oct) and adds machine identity. It looks like the superset.
- **Doc 23:** ZID-OPS-001 and ZID-SRE-001 are both dated 7 Oct.

The Architecture Chair must mark one of each pair authoritative, or define the split between them.

**19a:** ZID-RESILIENCE-001 isn't in the Guideline's list of 25. It extends Doc 19 and Doc 23 for topology, HA and DR (ADR-0011 cites it).

## Open spec change requests (raised by ADRs)

| Raised by | Target spec | Change |
|---|---|---|
| ADR-0003 | ZID-DOM-001 §6.3, ZID-DATA-001 | Adopt the four `continuity_mode` values |
| ADR-0003 | ZID-MANDATE-001 §6 | Add `REVALIDATION_PENDING` grant state |
| Table ownership | ZID-DATA-001 | Add the 11 tables marked `proposed` in `backend/database/table-ownership.yaml` |
| Table ownership | ZID-DATA-001 §1.1 | State that schemas are namespaces and ownership is per table (one owner per table, per ZID-SVC-001 §5–6) |
