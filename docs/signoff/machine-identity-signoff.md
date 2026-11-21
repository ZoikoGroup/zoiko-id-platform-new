# Sign-off pack: ZID-MACHINE-001

Generated 2026-11-21 by `tools/scripts/signoff_pack.py` from `docs/traceability/acceptance-tests.csv`. This is a checklist for the approvers. It does not approve anything: a test is done only when an approver changes its status in the traceability file.

- Acceptance tests: **26**; blockers: **1** (not started, no resolving evidence, or a measurement that says its gate is not met)

## Tests and evidence

| ID | Test | Status | Evidence | Resolves |
|---|---|---|---|---|
| MACH-01 | Distinct identity | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_display_name_and_sponsor_never_identify_an_agent` | yes |
| MACH-02 | Sponsor gate | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_activation_needs_every_gate` | yes |
| MACH-03 | Artifact binding | IN_PROGRESS | `backend/app/tests/component/test_phase_c_flows_pg.py::test_agent_activation_binding_and_blueprint_revocation` | yes |
| MACH-04 | SPIFFE mapping | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_credential_issuance_blockers` | yes |
| MACH-05 | Issuer/audience | IN_PROGRESS | `backend/app/tests/component/test_workload_identity_pg.py::test_workload_resolution_quarantine_and_revocation` | yes |
| MACH-06 | Credential expiry | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_credential_issuance_blockers` | yes |
| MACH-07 | Sender constraint | IN_PROGRESS | `backend/app/tests/component/test_workload_identity_pg.py::test_mesh_verified_workload_gets_a_bound_token` | yes |
| MACH-08 | Rotation | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_a_rotated_workload_binding_overlaps_and_the_old_one_stops_after_the_overlap` | yes |
| MACH-09 | Agent revoke | IN_PROGRESS | `backend/app/tests/component/test_phase_c_flows_pg.py::test_agent_activation_binding_and_blueprint_revocation` | yes |
| MACH-10 | Workload revoke | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_workload_compromise_spares_healthy_replicas` | yes |
| MACH-11 | Environment quarantine | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_blueprint_and_environment_quarantine` | yes |
| MACH-12 | Attestation floor | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_credential_issuance_blockers` | yes |
| MACH-13 | Authentication not authority | IN_PROGRESS | `backend/app/tests/unit/test_pdp.py::test_agent_needs_a_mandate_and_valid_scope` | yes |
| MACH-14 | Prompt cannot delegate | IN_PROGRESS | `backend/app/tests/component/test_authzen_api_pg.py::test_authzen_evaluation_over_http` | yes |
| MACH-15 | Tool discovery | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_discovered_but_unregistered_or_suspended_tools_never_run` | yes |
| MACH-16 | MCP mix-up | IN_PROGRESS | `backend/app/tests/unit/test_mix_up_controls.py::test_the_client_refuses_an_id_token_from_another_issuer_or_for_another_client` | yes |
| MACH-17 | Sub-agent issuance | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_parent_cannot_mint_its_own_trusted_child` | yes |
| MACH-18 | Sub-agent authority | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_parent_cannot_mint_its_own_trusted_child` | yes |
| MACH-19 | External agent | IN_PROGRESS | `backend/app/tests/unit/test_pdp.py::test_a_third_party_agent_identity_alone_never_represents_zoiko_authority` | yes |
| MACH-20 | Blueprint revoke | IN_PROGRESS | `backend/app/tests/component/test_phase_c_flows_pg.py::test_agent_activation_binding_and_blueprint_revocation` | yes |
| MACH-21 | Service replica isolation | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_workload_compromise_spares_healthy_replicas` | yes |
| MACH-22 | Secret scan | IN_PROGRESS | `backend/app/tests/unit/test_devsecops_controls.py::test_no_long_lived_machine_credential_ships_in_images_charts_or_deploy_config` | yes |
| MACH-23 | Token exchange | IN_PROGRESS | `backend/app/tests/component/test_oauth_w3_pg.py::test_confidential_clients_mtls_par_fapi_exchange_and_introspection` | yes |
| MACH-24 | Evidence linkage | IN_PROGRESS | `backend/app/tests/component/test_authzen_api_pg.py::test_authzen_evaluation_over_http` | yes |
| MACH-25 | Failure mode | IN_PROGRESS | `backend/app/tests/unit/test_machine_identity.py::test_a_trust_failure_gives_no_credential_and_names_no_fallback` | yes |
| MACH-26 | Load | IN_PROGRESS | `tests/load/machine-token/RESULTS.md` | yes |

## Blockers

- **MACH-26** Load (tests/load/machine-token/RESULTS.md records that the gate is not met): 2x Stage A machine-auth/token load meets latency, error and rotation objectives.

## Approvals

| Lead | Name | Date | Decision |
|---|---|---|---|
| Agent Platform |  |  |  |
| Identity |  |  |  |
| Security |  |  |  |
| Platform/SRE |  |  |  |
| DevSecOps |  |  |  |
| Authorization |  |  |  |
