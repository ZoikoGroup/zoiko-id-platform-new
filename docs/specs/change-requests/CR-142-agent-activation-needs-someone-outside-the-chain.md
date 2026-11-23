# CR-142: independence at activation, the right runtime path, a live proof at every run, and no leaking across companies

Source: design review of ZID-AG-WF-001 and ZID-AG-WF-002 against the build, 20 Nov 2026. Builds on CR-064 (two-person build approval), CR-093 and CR-094 (API agents) and the agent registry (ZID-MACHINE-001 section 12). Status: built 20 Nov 2026. Tightenings of existing rules and one extension (an API-bound agent can run the access readiness agent); no new permission.

## 1. The people who answer for an agent cannot activate it (WF-002 section 04)

The registry already kept an agent's own chain of accountability out of its approvals: the publisher cannot approve the blueprint release, the person who registered a build cannot approve it, and the sponsor, operator and publisher cannot attest the SECURITY_REVIEW gate. Activation had no such rule. `machine.agent.activate` can be delegated (CR-137, the authority page), so the person who registered an agent, and named themselves its sponsor, operator and publisher, could be given that right and switch it on. A component test with the check disabled showed it: the sponsor activated their own agent and got HTTP 200.

`activate` now refuses when the acting person is the agent's sponsor, operator, publisher **or registrant**: HTTP 403, code `AGENT_REVIEW_NOT_INDEPENDENT` (the code the release and build rules use), and the agent is left unchanged. One set of people is barred from attesting the review and from activating, so neither step can be done by someone who answers for the agent.

The registrant is stored on the agent record: migration `machine_0012` adds `agent.agent_profile.registered_by`, set once at registration. It is nullable: an agent registered before the column has no recorded registrant (the audit trail still names who did it), and an unknown registrant bars no one, because `None` never equals a real actor. A registrant that is not a principal id is stored as unknown, never as a wrong person.

A company with a single administrator can no longer activate an agent that administrator registered or sponsors. That is the intended effect: name another member as sponsor, or have another holder of `machine.agent.activate` activate.

## 2. An API-client build cannot be given a workload binding

A workload binding is the container path: an attestor compares a running image to a digest. An API client is not an image, so nothing can ever attest it, and an agent bound that way reaches ACTIVE with no way to get a credential (this happened in a rehearsal tenant: the workload binding form listed the API-client build's digest). `register_binding` now refuses a digest whose registered build is an API client: HTTP 422, `WORKLOAD_BUILD_IS_API_CLIENT`, naming the API client binding as the way. The console's workload form shows the same warning and disables the button for such an agent. The server rule is the one that decides; the form only saves a round trip.

## 3. A run needs a live proof of what runs the agent (WF-001 section 04 PREFLIGHT, AG1-AT07)

The run start checked the freeze, the agent, the sponsor, the policy, the approval and the quotas, but never that the agent still holds a proof of what runs it. It does now (`runtime_proof` in the governance context, `RUNTIME_PROOF_MISSING`, HTTP 403). The proof is either a bound API client in date, or a workload binding with a VALID, in-date attestation at the level the binding requires, for exactly its identity, environment and artifact. It is read at every run start and at a queued run's claim, so a revoked or expired binding, or a lapsed attestation, is seen at the next run. A proof that cannot be read is `POLICY_UNAVAILABLE`, never a pass. It follows the governance mode like the other gates: refused in `enforce`, recorded as `SHADOW_DENY` in `shadow`.

## 4. The access readiness agent runs for an API-bound agent (WF-001 AT-07, CR-093, CR-094)

Access readiness took its proof only from a workload binding with a verified attestation, so an agent bound to an API client (no container, no attestor) was refused every run with `AGENT_NOT_ATTESTED`. It now accepts the other kind of proof, at the level a key can show and no higher. A bound API client counts as attestation **level 1** (`API_ATTESTATION_CAP`) when, and only when, the same decision the token path makes passes (`credential_problems`: agent active, not frozen, binding live and in date, client build approved and not revoked, release unchanged). `agent_identity_status` reports which kind it is (`proof_kind`: WORKLOAD, API_CLIENT, NONE) so a reviewer can tell a verified image from a key. The per-run lease (`issue_workload_lease`) is issued from the API proof when the agent has no container binding, carries the identity `api-client:<client id>`, is never a bearer token and lasts no longer than any other lease, and `lease_problems` re-checks it at every step against the binding, its security revision and the same decision. The run records the API binding id as what it rested on, and the credential evidence says `proof: API_CLIENT`.

An API-bound agent still holds only tools at risk class 1 or lower. For the access readiness template that is the two read tools (`read_mandate_metadata`, `read_agent_identity_status`); the proposal write is made by the platform's own review service under the template's fixed scope, so the agent does not need the risk-2 tool `create_access_review_proposal` on its list. This extends the unratified Z1 rule (CR-094) to one more consumer; the security owner's sign-off on it is still open.

## 5. A company's emergency stop, revoked release or quarantine never reaches another company

Found by a test that failed only when two tests shared a database. A TENANT-scope emergency stop covers every agent it is matched against, and the revoked-release list and the quarantined-environment list were read for the whole table, relying on row-level security to confine them to one company. The worker's connection is not confined (it bypasses row-level security), so on it one company's company-wide stop, revoked release or quarantine could stop another company's runs. Two companies can run the same image digest, and an environment is only a name. Freeze reads (`active_freezes`, `active_freezes_for_agents`) and the credential decision (`credential_check`) now name the company in the query, taken from the agent or the binding being checked.

## 6. The lifecycle sweep takes a parent before its sub-agents

The sweep ordered by creation time only. A parent and a sub-agent registered in one transaction share a timestamp, so whichever the database returned first was reported alone, and the parent then reported no sub-agents. It now orders by depth in the agent tree first, so a parent's suspension takes the whole branch in one result.

## 7. Integration no longer imports federation

CR-138 made federation call integration for a connector's sealed secret, while integration already imported federation to apply a quarantine, which made the two one dependency cycle (the repo's ratchet test failed). Integration now applies a quarantine through a setter the composition root registers at start-up, as the other cross-module hooks are wired, and refuses with `CONNECTION_STATUS_UNAVAILABLE` if it is not registered, so a quarantine never silently fails to reach the connection.

## 8. A queued run executes as the runtime role, not as the worker

The cause of the reads in section 5 was structural: the worker connects as a role that bypasses row-level security (it must, to find queued work across companies), and then ran each company's work on that same connection, where "the tenant scope" was only a setting a bypassing role ignores. Every read that forgot to name the company could see every company. The queued-run consumers (`drain_queued` for access readiness, `work_queue` for DAARA) now run each run AS the role named by `ZID_AGENT_RUN_ROLE` (`zid_runtime`, the role row-level security applies to), through `zid_db.as_role` (a parameterized `set_config('role', ...)`, undone with the savepoint on any error). The worker's own queue read stays privileged; everything done for a company does not. A role that cannot be assumed fails closed: the run stays queued and nothing runs. Unset, behaviour is as before (a development or test setup with no such role).

Deployment: `deploy/gcp/db/01_roles.sql` grants the worker login membership of `zid_runtime` (`WITH INHERIT FALSE, SET TRUE` on PostgreSQL 16 and later, so it may take the role but does not hold its privileges otherwise), and `deploy/local-prod/docker-compose.yml` sets the variable. This does not make the per-query company filters of section 5 redundant; it makes the next missed one harmless.

## Where this stops

- Section 3 is the "no proof, no run" part of the design's attested worker. It is not the separate worker process with its own short-lived token per read; DAARA still runs inside the cell and calls the governance checks in process. That needs the worker-substrate decision (D-08) and infrastructure.
- An API client still proves only that it holds a key, not what code runs (CR-093); its tools stay capped at risk class 1 and side effects NONE or REVERSIBLE.
- The registrant of an agent created before `machine_0012` is unknown on the record.

## Built

`activator_blockers` and `_chain_of_accountability` (`machine/domain/agents.py`), the check in `activate` and the registrant at registration (`application/agents.py`), `registered_by` in the store and migration `machine_0012`; `WORKLOAD_BUILD_IS_API_CLIENT` in `application/workload.py` (422 in `api/workloads.py`); `Code.RUNTIME_PROOF_MISSING`, `GovernanceContext.runtime_proof` and the check in `evaluate_run_start` (`domain/governance.py`), `live_runtime_bindings` (`infrastructure/agent_store.py`) and `_runtime_proof` (`application/governance.py`); the workload form (`frontend/.../workloads/workloads-page.tsx`) with `isApiClientBuild`.

Tests: `test_agent_activation_independence.py` (unit: each role barred, the registrant barred, an unknown registrant bars nobody, one set of people for review and activation); `test_agent_registry_pg.py` (the sponsor, the publisher and the registrant get 403 on activation and the review, the agent stays PENDING_APPROVAL, an independent reviewer succeeds); `test_api_agents_pg.py` (an API-client build is refused a workload binding and the named path then works); `test_daara_pg.py::test_at07_...` (a revoked binding, an expired binding, no attestation, too low a level, a lapsed statement are each refused before a run exists; a valid one, and a bound API client, run); `test_agent_governance.py` (the decision, including a proof that could not be read); `agents-logic.test.ts` (the console helper). The seeded agents in `gateway_fixture.py` now carry the proof a real activation leaves; a test that asserts an API agent has no workload binding turns that off with `runtime_proof=False`.

Tests for sections 4 to 7: `test_access_readiness_api_proof_pg.py` (an API-bound agent runs and is refused the moment its binding is revoked or expired, its build revoked or its digest changed; the lease is re-checked at every step and a revoked binding gets no new lease; company A's revoked release and quarantine do not stop company B's agent, checked over an unscoped connection, and each test fails without its fix), `test_agent_governance_pg.py` (company A's company-wide stop is not seen for company B's agent), `test_agent_lifecycle_pg.py` (a sub-agent created before its parent is still reported with it), and the dependency-cycle ratchet, which passes again. The stale `test_org_claim_dev_shortcut.py` now stubs the certificate reading CR-132 added.

Tests for section 8: `test_access_readiness_controls_pg.py` (every step of a queued run is the runtime role and the outcomes are unchanged; a role that cannot be assumed leaves the run queued) and `test_daara_pg.py` (the same for the DAARA queue).
