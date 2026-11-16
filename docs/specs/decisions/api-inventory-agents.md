# API inventory for decision D-06 and gate G0

Status: GENERATED from the committed OpenAPI contracts, not from a running environment. It lists what the code declares. A person must still confirm it against the live deployment and sign it (D-06). Regenerate with the script named in the decision pack.

Total operations: 873. Agent and machine-identity operations: 91.

## Operations per contract

- authzen/evaluation.json: 1
- public/authentication.json: 27
- public/authority-mandate.json: 21
- public/commercial.json: 165
- public/communication.json: 6
- public/credentials.json: 71
- public/evidence.json: 61
- public/federation.json: 18
- public/integration.json: 22
- public/notification.json: 5
- public/oauth.json: 18
- public/onboarding.json: 47
- public/operator-access.json: 51
- public/policy.json: 8
- public/privacy.json: 101
- public/residency.json: 11
- public/scim.json: 26
- public/search.json: 1
- public/settings.json: 5
- public/signals.json: 13
- public/tenant.json: 52
- public/usage.json: 10
- public/verification.json: 26
- public/webhooks.json: 16
- public/workload-identity.json: 91

## Agent and machine-identity operations

| Method | Path | Contract |
|---|---|---|
| GET | /v1/machine/agent-review | public/workload-identity.json |
| GET | /v1/machine/agent-review/{proposal_id} | public/workload-identity.json |
| POST | /v1/machine/agent-review/{proposal_id}:assign | public/workload-identity.json |
| POST | /v1/machine/agent-review/{proposal_id}:dismiss | public/workload-identity.json |
| POST | /v1/machine/agent-review/{proposal_id}:handoff | public/workload-identity.json |
| GET | /v1/machine/agents | public/workload-identity.json |
| POST | /v1/machine/agents | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id} | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/api-binding | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/api-binding | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/api-binding:revoke | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/budget | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/change-requests | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/children | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/daara | public/workload-identity.json |
| PUT | /v1/machine/agents/{agent_id}/daara | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/daara/dry-run | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/daara/runs | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/daara/runs | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/daara/runs/{run_id} | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/daara:request-publish | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/daara:validate | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/effective-access | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/events | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/events:export | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/monitoring | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/pause | public/workload-identity.json |
| GET | /v1/machine/agents/{agent_id}/policy | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/policy/drafts | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/policy/simulate | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}/runs | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}:activate | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}:attest | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}:retire | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}:revoke | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}:submit | public/workload-identity.json |
| POST | /v1/machine/agents/{agent_id}:suspend | public/workload-identity.json |
| POST | /v1/machine/api-clients | public/workload-identity.json |
| POST | /v1/machine/attestations:verify | public/workload-identity.json |
| GET | /v1/machine/blueprints | public/workload-identity.json |
| POST | /v1/machine/blueprints | public/workload-identity.json |
| POST | /v1/machine/blueprints/{blueprint_id}/versions/{version}:approve | public/workload-identity.json |
| POST | /v1/machine/blueprints/{blueprint_id}/versions/{version}:revoke | public/workload-identity.json |
| POST | /v1/machine/budget-adjustments | public/workload-identity.json |
| POST | /v1/machine/budget-reservations | public/workload-identity.json |
| POST | /v1/machine/budget-reservations/{reservation_id}:release | public/workload-identity.json |
| POST | /v1/machine/budget-reservations/{reservation_id}:settle | public/workload-identity.json |
| POST | /v1/machine/budgets | public/workload-identity.json |
| GET | /v1/machine/builds | public/workload-identity.json |
| POST | /v1/machine/builds | public/workload-identity.json |
| POST | /v1/machine/builds/{build_id}:approve | public/workload-identity.json |
| POST | /v1/machine/builds/{build_id}:revoke | public/workload-identity.json |
| POST | /v1/machine/builds:resolve | public/workload-identity.json |
| POST | /v1/machine/builds:versions | public/workload-identity.json |
| GET | /v1/machine/daara/template | public/workload-identity.json |
| POST | /v1/machine/environments/{environment_id}:quarantine | public/workload-identity.json |
| POST | /v1/machine/environments/{environment_id}:release | public/workload-identity.json |
| POST | /v1/machine/governance/alerts/{alert_key}:acknowledge | public/workload-identity.json |
| POST | /v1/machine/governance/alerts/{alert_key}:route | public/workload-identity.json |
| GET | /v1/machine/governance/baseline | public/workload-identity.json |
| PUT | /v1/machine/governance/baseline | public/workload-identity.json |
| GET | /v1/machine/governance/delegates | public/workload-identity.json |
| POST | /v1/machine/governance/delegates | public/workload-identity.json |
| POST | /v1/machine/governance/delegates:revoke | public/workload-identity.json |
| GET | /v1/machine/governance/events | public/workload-identity.json |
| GET | /v1/machine/governance/events:export | public/workload-identity.json |
| GET | /v1/machine/governance/evidence:verify | public/workload-identity.json |
| GET | /v1/machine/governance/freezes | public/workload-identity.json |
| POST | /v1/machine/governance/freezes/{case_id}:request-restore | public/workload-identity.json |
| GET | /v1/machine/governance/overview | public/workload-identity.json |
| POST | /v1/machine/governance/pause | public/workload-identity.json |
| GET | /v1/machine/governance/requests | public/workload-identity.json |
| GET | /v1/machine/governance/requests/{request_id} | public/workload-identity.json |
| POST | /v1/machine/governance/requests/{request_id}:apply | public/workload-identity.json |
| POST | /v1/machine/governance/requests/{request_id}:approve | public/workload-identity.json |
| POST | /v1/machine/governance/requests/{request_id}:clarify | public/workload-identity.json |
| POST | /v1/machine/governance/requests/{request_id}:reject | public/workload-identity.json |
| POST | /v1/machine/runs/{run_id}:finish | public/workload-identity.json |
| POST | /v1/machine/runs/{run_id}:progress | public/workload-identity.json |
| POST | /v1/machine/tool-calls:authorize | public/workload-identity.json |
| POST | /v1/machine/tool-grants:redeem | public/workload-identity.json |
| GET | /v1/machine/tools | public/workload-identity.json |
| POST | /v1/machine/tools | public/workload-identity.json |
| POST | /v1/machine/tools/{tool_id}:activate | public/workload-identity.json |
| POST | /v1/machine/tools/{tool_id}:retire | public/workload-identity.json |
| POST | /v1/machine/tools/{tool_id}:suspend | public/workload-identity.json |
| GET | /v1/machine/trust-domain | public/workload-identity.json |
| GET | /v1/machine/workload-bindings | public/workload-identity.json |
| POST | /v1/machine/workload-bindings | public/workload-identity.json |
| POST | /v1/machine/workload-bindings:revoke | public/workload-identity.json |
| POST | /v1/machine/workload-token | public/workload-identity.json |
