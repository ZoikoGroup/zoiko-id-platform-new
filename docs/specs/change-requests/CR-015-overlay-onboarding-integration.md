# CR-015: Tenant lifecycle, onboarding, domains and integration registry

- **Target:** ZID-OVERLAY-001 §2–5, §11, §14–21; ZID-ROOT-001 §6, §19; ZID-DATA-001 §2; action catalogue; event catalogue · **Raised by:** Phase G W0–W5 · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `platform.tenant_role_assignment` | tenant | OWNER / ADMIN / MEMBER with status (PENDING_VERIFICATION, INVITED, ACTIVE, REVOKED) and origin. OWNER is a workspace role, never signing authority (OVERLAY §4). |
| `platform.tenant_state_transition` | tenant | Every lifecycle move (§2) with its reason code, actor and machine-readable gate report. Append-only. |
| `platform.tenant_configuration_revision` | tenant | Monotonic configuration revisions with a commitment to each change. |
| `platform.tenant_bootstrap_request` | tenant | The bootstrap that created a tenant: who created it, from which tenant (self-signup = itself). |
| `platform.tenant_domain` | tenant | Domain-control challenges (DNS TXT / HTTPS well-known), re-verification, and one VERIFIED claimant per domain across the cell (§5). |
| `platform.workspace_directory` | tenant | Workspace name → console client, not tenant-scoped, for sign-in discovery. |
| `platform.tenant_registration` | tenant | What a self-registered company declared: register and identifier (+ LEI), acknowledged privacy-notice version and time, chosen home cell. Re-checked against the register at verification. |
| `org.registry_observation` | organization | Each register read (Companies House, GLEIF) with outcome and source revision; the provenance of a REGISTRY root (ROOT §6, §14). |
| `platform.integration_application` | integration | Application / PEP registry (§14): protocols, PEP client, declared obligations and context fields, risk and evidence floors, lifecycle. |
| `platform.integration_connector` | integration | Connector framework (§15): type, source reference, capabilities, health, freshness, disable behaviour, quarantine. |
| `platform.connector_credential` | integration | Write-only connector credentials sealed under the data KEK, versioned with overlap windows and expiry alerts (§16). |
| `platform.mapping_release` | integration | Immutable attribute / group / role mapping releases with simulation reports; activation and rollback by release (§11, §20). |
| `platform.identity_routing_rule` | integration | Multi-IdP routing by application or login domain (§8–9). |
| `platform.onboarding_plan` | onboarding | The versioned onboarding plan object (§17): target mode, scope, exit criteria, cutover windows, rollback triggers, approvals. |
| `platform.onboarding_stage` | onboarding | Pilot stages (§19): the scope of applications, actions, risk and population enforced. |
| `platform.shadow_observation` | onboarding | Shadow-mode decision observations next to the incumbent outcome (§18), the input to the exit-criteria gates. |

## Actions

| Action | Risk / evidence | Note |
|---|---|---|
| `tenant.lifecycle.manage` | R3 / E3 | Suspend, resume, offboard; resume and offboard need a second administrator. |
| `tenant.member.manage` | R2 / E2 | Invite and revoke MEMBER and ADMIN; never OWNER. |
| `tenant.onboarding.manage` | R2 / E2 | Plan versions, validation, integration and shadow start. |
| `tenant.onboarding.activate` | R3 / E3 | Pilot entry, stage activation, production activation; a second administrator. |
| `tenant.domain.manage` | R2 / E2 | Domain challenges. |
| `integration.application.manage` | R2 / E2 | Application and PEP registry. |
| `integration.connector.manage` | R3 / E3 | Credentials, quarantine; release needs a second administrator. |
| `integration.mapping.manage` | R3 / E3 | Mapping release activation and rollback; bound to the release content hash. |

## Events (proposed)

`tenant.lifecycle.changed`, `tenant.member.changed`, `tenant.suspended`, `tenant.resumed`, `tenant.closed`, `tenant.domain.changed`, `tenant.domain.stale`, `integration.application.changed`, `integration.connector.changed`, `integration.connector.stale`, `integration.credential.rotated`, `integration.credential.expiring`, `integration.mapping_release.changed`, `onboarding.plan.changed`, `onboarding.stage.changed`, `provisioning.lifecycle.changed`. All are in `contracts/asyncapi/proposed-events.yaml`.

## Engineering readings to confirm

- The bootstrap OWNER is `PENDING_VERIFICATION` until the organization gate passes; development verification (`ZID_TENANT_SIGNUP_DEV_VERIFY`) is refused outside local/test.
- A registry root binds its holder only by equality of a verified `legal_name` commitment with a finite list of renderings of a current officer's registered name (no fuzzy matching).
- Platform-fulfilled obligations (`DECISION_RECEIPT`) never count against a PEP's declared obligations.
- Shadow exit criteria may only tighten the platform defaults (100 observations, full R3 context, no critical false allow, p95 ≤ 60 ms).
