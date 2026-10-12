# CR-007: Phase C tables wider than ZID-DATA-001 v1.0

- **Target:** ZID-DATA-001 §3.2, §4.3, §7.1 · **Raised by:** Phase C migrations (`principal`, `organization`, `relationship`, `authority` streams) · **Status:** Open

The migrations implement the upstream specs (ZID-MANDATE-001, ZID-ROOT-001, ZID-DOM-001) where ZID-DATA-001 v1.0 is narrower:

| Table | ZID-DATA-001 v1.0 | Required by | Implemented as |
|---|---|---|---|
| `authz.authority_grant` | no grant_type, continuity_mode, toolset, limits, issued_at, evidence_ref; status DRAFT/ACTIVE/SUSPENDED/REVOKED/EXPIRED | ZID-MANDATE-001 §3, §6; CR-001; CR-002 | adds `grant_type`, `continuity_mode` (4 values), `tools`, `purposes` (set, MANDATE §4), `value_limit`+`currency`, `count_limit`, `data_limit_bytes`, `min_assurance`, `issued_at`, `evidence_ref`, `revision`; status adds `PENDING_APPROVAL`, `REVALIDATION_PENDING`, `SUPERSEDED` |
| `authz.authority_grant` (guards) | invariants stated in prose | MANDATE §4, §6 | trigger: depth strictly decreases from the parent; scope columns immutable (material change = superseding grant); terminal states final |
| `org.authority_root_assertion` | root types REGISTRY/VLEI_ROLE/BOARD_RESOLUTION/SIGNING_MATRIX/INSTITUTIONAL_KYB/OTHER; status ACTIVE/STALE/SUSPENDED/REVOKED/EXPIRED; no capability class or scope | ZID-ROOT-001 §13 | `facts` (root_capability_class set), types `VLEI_OOR`, `VLEI_ECR`, `POWER_OF_ATTORNEY`; states `PENDING`, `CONFLICTED`, `SUPERSEDED`; `jurisdiction`, `scope_actions`, `value_limit`+`currency`, `source_revision`, `source_effective_at`, `observed_at`, `row_version` |
| `org.source_event` | not present | ZID-ROOT-001 §18, ROOT-24 | proposed table: one row per processed source event; a duplicate insert means "already applied" |
| `org.organization` | no flag for dissolution state | ZID-ROOT-001 §18 | `can_originate_authority`; `authority_root_revision` monotonic by trigger |
| `core.principal_identifier` | uniqueness "according to identifier policy" | ZID-DOM-001 §4.3 | explicit `uniqueness_scope` + `scope_key` with one partial unique index per scope; values stored only as hashes |
| `core.principal` | no sponsor | ZID-DOM-001 §4.2 (AI_AGENT needs a sponsor) | `sponsor_principal_id`, CHECK for AI agents; trigger refuses lower `security_revision` and identity changes |
| `org.relationship_edge` | `object_principal_id` only; types without MANAGES/PARENT_OF/ASSIGNED_ROLE | ZID-DOM-001 §5; ZID-AUTHZ-001 §7 | adds those types, `object_tenant_id` + `cross_tenant_trust_id` (CHECK), `status`; `authz.relation_publication` holds the monotonic per-tenant revision (trigger) |
| `agent.mandate` (2nd revision) | no parent mandate, delegation depth, budget mode, tool/jurisdiction/resource-scope sets | ZID-MANDATE-001 §9, §17 | adds those columns; trigger: sub-mandate depth strictly decreases, terms immutable, terminal states final |
| `authz.approval_request`, `authz.approval_decision` | not present | ZID-MANDATE-001 §16 | proposed tables; one decision per approver per request; decisions bound to the request's transaction hash; append-only |
| `agent.agent_profile` | status ACTIVE/SUSPENDED/REVOKED/RETIRED; no owning organization, blueprint version, max sub-agent depth, allowed tools | ZID-MACHINE-001 §2, §12 | adds DRAFT/PENDING_APPROVAL and those columns; ACTIVE needs sponsor + owning organization (CHECK); REVOKED never returns |
| `agent.blueprint_release`, `agent.tool_capability` | not present | ZID-MACHINE-001 §3, §15 | proposed tables (blueprint review state drives credential issuance; tool registry gates invocation) |
| `agent.workload_binding` | proposed in CR-003 | ZID-MACHINE-001 §16 | one live binding per workload identity (partial unique index) |
| `authz.policy_release` | status DRAFT/TESTING/ACTIVE/RETIRED | ZID-AUTHZ-001 §18 | VALIDATING/REVIEW/SCHEDULED/REVOKED, schema_version, cases, reviewers; one ACTIVE per tenant; published source immutable |
| `authz.policy_decision` | outcomes without STATE_UNAVAILABLE | ZID-AUTHZ-001 §14, §24 | adds STATE_UNAVAILABLE, evidence_class, authority_chain_commitment, input_commitment; append-only; R2/R3 allows must carry release + revisions |
