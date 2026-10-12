# CR-003: Tables required by other specs but absent from ZID-DATA-001 v1.0

- **Target:** ZID-DATA-001 · **Raised by:** `backend/database/table-ownership.yaml` · **Status:** Open

Each table below is required by the cited spec. Fields are taken from that spec; the Data Model owner adds types, keys, indexes, RLS and retention. All tenant-owned tables carry `tenant_id`, UUIDv7 keys and `row_version` per ZID-DATA-001 §1.

| Table | Owner (unit/module) | Required by | Fields stated in the source spec |
|---|---|---|---|
| `agent.writer_fence` | authority-runtime/mandate | DEC-C5; ZID-RESILIENCE-001 §0.2, §7.1, §17 (writer epoch fencing) | tenant_id, epoch, updated_at |
| `agent.workload_binding` | authority-runtime/machine | ZID-MACHINE-001 §16 | binding_id, logical_principal_id, workload_identity_id, blueprint_version, artifact_digest, environment_id, attestation_level, status (ACTIVE/SUSPENDED/REVOKED/EXPIRED), valid_from/valid_to, security_revision, approved_by/evidence |
| `authn.refresh_token_family` | identity-runtime/session | ZID-AUTH-001 (rotation; reuse revokes family), ZID-API-001 §2.1 | family_id, session_id, client_id, current token verifier, rotation counter, revoked_at, reuse_detected |
| `authn.federation_connection` | enterprise-integration/federation | ZID-SVC-001 §5 (FED owns IdP/RP metadata, keys, trust, mapping reference); ZID-OVERLAY-001 §6–7 | issuer / SAML entityID, discovery/metadata ref, JWKS or signing cert refs, client/audience, allowed algorithms, mapping_release_id, status, metadata expiry |
| `platform.provisioning_source` | enterprise-integration/provisioning | ZID-OVERLAY-001 §10 | source_id, protocol (SCIM), credential profile, sync cursor, status, last_sync_at |
| `platform.integration_application` | enterprise-integration/integration | ZID-OVERLAY-001 §14 | application_id, tenant_id, name, environment (DEV/TEST/STAGE/PROD), protocols, redirect_uris/ACS, resource/audience IDs, credential profile, PEP profile, risk floor, evidence floor, owner, status (DRAFT/TESTING/ACTIVE/SUSPENDED/RETIRED), configuration_revision |
| `platform.mapping_release` | enterprise-integration/integration | ZID-OVERLAY-001 §3, §11 | mapping_release_id, status (DRAFT/REVIEW/ACTIVE/RETIRED), source attribute, target fact, transformation, missing-value behavior (IGNORE/CLEAR/ERROR), conflict policy, simulation ref |
| `platform.onboarding_plan` | enterprise-integration/onboarding | ZID-OVERLAY-001 §17 | onboarding_plan_id, tenant_id, scope, current_state/target_state, identity_sources, pilot_population, shadow_period, cutover_windows, rollback_triggers, support/escalation, data_migration, residency/sovereignty, acceptance_suite, approval_refs |
| `platform.jit_grant` | platform-control/operator_access | ZID-SEC-001 §11–12; ZID-UX-001 §21.1 | tenant, object classes, permitted actions, reason, case, expiry, approver(s), status |
| `platform.lockbox_request` | platform-control/operator_access | ZID-SEC-001 §11–12; ZID-TENANCY-001 §19 | tenant, requester, scope, customer approver, time bound, status, evidence ref |
| `platform.notification_request` | platform-control/notification | ZID-SVC-001 §3 (NOTIF), §30 | channel, template, recipient handle (opaque), status, retry state |
