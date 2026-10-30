# Spec change requests

Raised by engineering when a decision ([docs/phases/](../../phases/README.md)) or the implementation finds a gap or inconsistency in a canonical spec. Each CR names the target spec section, the proposed change and the evidence. The spec owner accepts, amends or rejects it; an accepted CR updates the spec and this register.

| CR | Target | Summary | Raised by | Status |
|---|---|---|---|---|
| [CR-001](CR-001-continuity-modes.md) | ZID-DOM-001 §6.3, ZID-DATA-001 | Adopt four canonical `continuity_mode` values | DEC-C2 | Open |
| [CR-002](CR-002-revalidation-pending-state.md) | ZID-MANDATE-001 §6 | Add `REVALIDATION_PENDING` grant state | DEC-C2 | Open |
| [CR-003](CR-003-data-model-proposed-tables.md) | ZID-DATA-001 | Add 11 tables required by other specs | Table ownership registry | Open |
| [CR-004](CR-004-per-table-ownership.md) | ZID-DATA-001 §1.1 | Schemas are namespaces; ownership is per table | Repository structure | Open |
| [CR-005](CR-005-master-sequence-reconciliation.md) | Guideline §2, Docs 18/19/23 | Record the canonical document for each duplicate slot | ZID-SEC-001 §33 | Open |
| [CR-006](CR-006-mandate-ledger-columns.md) | ZID-DATA-001 §8.2–8.3 | Mandate ledger states and columns required by ZID-MANDATE-001 | DEC-C5 migration | Open |
| [CR-007](CR-007-phase-c-tables.md) | ZID-DATA-001 §3.2, §4.3, §7.1 | Grant, root, identifier and relationship columns required by upstream specs | Phase C migrations | Open |
| [CR-008](CR-008-relationship-edge-event.md) | ZID-EVENTS-001 §22 | Declare a relationship change event for the projector | DEC-C4 | Open |
| [CR-009](CR-009-phase-e-identity-additions.md) | ZID-DATA-001 §5, §6, §10; ZID-EVENTS-001 §22; action catalogue | Phase E tables, events and actions | Phase E implementation | Open |
| [CR-010](CR-010-admin-read-actions.md) | Action catalogue; ZID-API-001 §10-11; ZID-MANDATE-001 §9-10 | Administrative read actions and mandate creation | Web admin console | Open |
| [CR-011](CR-011-evidence-ledger-receipts-vault.md) | ZID-DATA-001 §9.2-9.3; EVIDENCE-001 §3, §7, §10-23 | Evidence ledger, receipts, audit records and subject-evidence vault | Phase F W1/W2 | Open |
| [CR-012](CR-012-operator-access.md) | SEC-001 §11-13; TENANCY-001 §19 | Operator access tables, actions and events | Phase F W4 | Open |
| [CR-013](CR-013-privacy-control-plane.md) | ZID-DATA-001 §7.2, §9.1; PRIV-001 | Privacy control plane tables and actions | Phase F W3 | Open |
| [CR-014](CR-014-residency-workload-identity.md) | TENANCY-001 §11-19; MACHINE §16; CRYPTO-MI-001 §29 | Residency enforcement and workload identity | Phase F W5/W6 | Open |
| [CR-015](CR-015-overlay-onboarding-integration.md) | OVERLAY-001 §2-5, §11, §14-21; ROOT-001 §6, §19 | Tenant lifecycle, onboarding, domains, sign-up registration and the integration registry | Phase G W0-W5 | Open |
| [CR-016](CR-016-locator-resilience.md) | TENANCY-001 §14-16; RESILIENCE-001 §15-30; DEC-G1/G3 | Global locator, signed discovery and governed recovery operations | Phase G W6-W7 | Open |
| [CR-017](CR-017-sre-operations.md) | SRE-001 §1, §4-6, §15-17, §22-24, §29-30 | Service catalogue, SLOs, incidents, readiness reviews and change freezes | Phase G W8 | Open |
| [CR-018](CR-018-webhook-management.md) | EVENTS-001 §14-20; API-001 §17; UX-001 §18 | Webhook management API, actions and delivery runtime | Phase H H-1 | Open |
| [CR-019](CR-019-alerts-tasks.md) | UX-001 §3, §20, §27 | Alerts and tasks (notification centre) | Phase H H-2 | Open |
| [CR-020](CR-020-global-search.md) | UX-001 §3.1, §25 | Global search (authorization-aware, tenant-scoped) | Phase H H-2 | Open |
| [CR-021](CR-021-agent-registry.md) | MACHINE-001 §2-3, §12-15, §18, §21; UX-001 §13 | Agent, blueprint and tool registry API; activation-gate attestations | Phase H H-5 | Open |
| [CR-022](CR-022-tenant-settings.md) | UX-001 §19, §20, §28; DATA-001 §1.1 | Versioned tenant settings, enforced session limits, cryptography overview | Phase H H-6 | Open |
| [CR-023](CR-023-usage-and-rate-limits.md) | UX-001 §18; DATA-001 §11.1; API-001 | API usage metering and the per-tenant rate limit | Phase H H-7 | Open |
| [CR-024](CR-024-commercial-catalogue-entitlements.md) | ZID-COM-001 §2, §5, §6, §12, §29; DATA-001 §1.1 | Commercial catalogue, plans and entitlements | Phase J J-1 | Open (built) |
| [CR-025](CR-025-billable-metering.md) | ZID-COM-001 §7, §11, §26, §31 | Billable metering ledger (idempotent, insert-only, replayable) | Phase J J-2 | Open (built) |
| [CR-026](CR-026-rating.md) | ZID-COM-001 §6, §7, §11, §13, §30 | Rating: included quota, overage, proration, credits | Phase J J-3 | Open (built) |
| [CR-027](CR-027-invoicing-billing-state.md) | ZID-COM-001 §11, §13, §30, §31 | Invoices, credit notes and the billing state machine | Phase J J-4 | Open (built) |
| [CR-028](CR-028-quotes-orders-contracts.md) | ZID-COM-001 §9, §10, §12, §14, §17 | Accounts, quotes, orders, contracts and discount authority | Phase J J-5 | Open (built) |
| [CR-029](CR-029-budgets-and-vendor-costs.md) | ZID-COM-001 §7, §8, §23 | Budget warnings and the vendor-cost ledger | Phase J J-6, J-7 | Open (built) |
| [CR-031](CR-031-communication-estate.md) | Email Communication Estate §02-§07 | The email estate: 160 templates, renderer, SMTP delivery, suppression, consent, contacts, wired events | Phase K | Open (engine and ten wired templates built; 150 wait for producer events) |
| [CR-030](CR-030-revenue-assurance-and-commercial-ux.md) | ZID-COM-001 §19, §22, §24, §31 | Revenue assurance, commercial console and pricing page, acceptance suite | Phase J J-8 to J-10 | Open (J-9, J-10 built; J-8 not built) |
| [CR-032](CR-032-plan-requests.md) | ZID-COM-001 §10, §12, §19 | A company asks for a different plan; the commercial team answers (no entitlement changes) | Console wiring | Open (built) |
| [CR-033](CR-033-deletion-rectification-shared-identity-clients.md) | ZID-TENANCY-001 §10, §23; ZID-PRIV-001; ZID-AUTH-001 §6-7 | Tenant data deletion, rectification at the source, shared-identity disclosure, self-service OAuth clients | Acceptance linking | Open (built) |
| [CR-034](CR-034-company-payment-method.md) | ZID-COM-001 §11, §19 | A company saves the card its invoices are charged to (Stripe hosted page, owner only) | Console wiring | Open (built) |
| [CR-035](CR-035-operator-direct-mode.md) | DEC-F4; ZID-SEC-001 §11-13; ZID-COM-001 §6, §12 | One operator account can run the platform: direct mode drops access requests and second approvers for non-customer-data work | Console wiring | Open (built) |
| [CR-036](CR-036-owner-approval-of-onboarding.md) | ZID-OVERLAY-001 §4, §25-26 | The company owner completes onboarding steps alone (no second administrator) | Console wiring | Open (built) |
| [CR-037](CR-037-guided-application-connection.md) | ZID-OVERLAY-001 §12, §18, §25; ZID-UX-001 | A guided, plain-words way to connect an application and watch shadow | Console wiring | Open (built) |
| [CR-038](CR-038-find-identity-provider-settings.md) | ZID-AUTH-001 §9, §19; ZID-UX-001 | Adding an identity provider needs four values: the cell finds the endpoints, keys and certificates from the provider's address | Console wiring | Open (built) |
| [CR-039](CR-039-mandate-roles.md) | ZID-MANDATE-001 §9-10, §14; ZID-UX-001 | Mandates are changed by their own people or by `mandate.manage`; a Member sees their own limits | Console wiring | Open (built) |
| [CR-043](CR-043-phase-l-company-verification.md) | ZID-DATA-001; ZID-EVENTS-001 §22; ZID-ED-03; ZID-ED-04 | Phase L: company verification and screening tables, events, routing and policy configuration | Phase L L-3, L-4 | Open (built) |
| [CR-044](CR-044-role-feature-matrix.md) | ZID-ENG-DEC-007; ZID-OVERLAY-001 §4 | Five tenant roles, the role by feature matrices and their decision evidence | Phase L L-7 | Open (built) |
| [CR-045](CR-045-capability-bands-and-usage-reservations.md) | ZID-ENG-DEC-008; ZID-COM-001 §6, §29 | Capability bands C1 to C5 over the Commercial Estate, the meter register and idempotent usage reservations | Phase L L-8 | Open (built) |
| [CR-046](CR-046-step-up-authentication.md) | ZID-ENG-DEC-009; ZID-AUTH-001 §3-5 | Step-up tiers S0 to S2, action-bound single-use proofs and the guard on sensitive routes | Phase L L-9 | Open (built) |
