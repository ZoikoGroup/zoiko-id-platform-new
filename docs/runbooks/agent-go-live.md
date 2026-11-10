# Runbook: getting an agent from a build to a working, governed identity (local and production)

Change records: [CR-064](../specs/change-requests/CR-064-approved-builds.md) (approved builds), [CR-066](../specs/change-requests/CR-066-pipeline-registered-builds.md) (a pipeline registers builds).

## What the platform checks, and why every step exists

An agent is software that runs somewhere else; the platform issues it an identity only while the software that is really running is the software people reviewed.
Outside `local`, `test` and `dev` (`ZID_AGENT_BUILDS=auto`) this chain is **mandatory**, and each link refuses without the one before it:

| # | Step | Who | Refused without |
|---|---|---|---|
| 1 | **Register a build** (the fingerprint, a `sha256:` digest, of one exact image) | the pipeline (production) or a person | grant `machine.build.register` |
| 2 | **Approve the build** | a **different** person | grant `machine.build.manage`; the registrant can never approve their own |
| 3 | **Register the tools** the agent may use, and activate them | release manager | grant `machine.tool.manage` |
| 4 | **Draft a blueprint release** from the approved build, then **approve it** | release manager, then a different reviewer | an approved build (`BUILD_NOT_APPROVED`); grant `machine.blueprint.manage` |
| 5 | **Register the agent** from that release | release manager | an existing release; grant `machine.agent.manage` |
| 6 | **Bind its workload identity** (SPIFFE id, environment, digest) | operator | an approved build for that digest (`WORKLOAD_ARTIFACT_NOT_APPROVED`); grant `machine.workload.manage` |
| 7 | **Submit, attest the four checks, activate** | release manager submits; a different reviewer attests and activates | grants `machine.agent.manage` / `machine.agent.activate` |
| 8 | **Run it by digest**; the attestor compares what runs to what was approved | the deployment | attestation level 2, else `ATTESTATION_INSUFFICIENT` |

Revoking a build at any time revokes its releases and suspends the bindings and agents built on it: the kill path.

## Nobody holds these grants by default

A new company's owner holds only `machine.agent.read` (see `TENANT_ADMIN_ACTIONS`). That is deliberate: agent authority is high privilege. **The owner issues the grants** under
*Authority & approvals → Issue grant → Agents and tools* (and *Workload identity* for step 6). Issuing a grant asks for a fresh passkey check. A sensible split for two to four people:

- **Engineer:** *See agents, blueprints and tools*, *Register a software build*.
- **Reviewer (never the engineer):** *See…*, *Approve or revoke software builds*, *Publish, approve or revoke blueprint releases*, *Register agents and attest their checks* (attesting is part of it), *Activate an agent*.
- **Release manager:** *See…*, *Register, activate or suspend tools*, *Publish, approve or revoke blueprint releases*, *Register agents and attest their checks*.
- **Operator:** *See…*, *Register, revoke or quarantine workload bindings* (Workload identity).

The console's **Agents & tools** page has a setup guide that shows which step is next, and warns, before you press a button, when your grants do not cover it.

## Production

**Once, by the platform operator** (settings in `.env`, see `deploy/gcp/env.example`):
1. Leave `ZID_AGENT_BUILDS` unset (`auto`). `off` is refused at start-up.
2. `ZID_BUILD_REGISTRIES=["ghcr.io"]` so the console can look a digest up from an image name (public images only; Docker Hub needs `registry-1.docker.io` and `auth.docker.io`).
3. Optionally `ZID_BUILD_IMAGES=["ghcr.io/<org>/<agent>"]`, so people pick software and a version from lists instead of typing a name.
4. The workload path (step 8) needs SPIRE and the attestor: `deploy/gcp-vm/README.md` section 9.

**Once per agent, by DevOps:**
1. **Agents & tools → Builds → Create pipeline credentials** (one press). The key pair is made in your browser; keep the private key file as a pipeline secret (it is shown once). This registers a
   *build pipeline* client, which can propose builds and nothing else. The screen also shows the identity to give the grant to.
2. **Authority & approvals → Issue grant** to that identity: **one** grant, *Register a software build*. It cannot approve, revoke, draft a release or register a tool (tested with every other machine
   grant given to it). A client made for access decisions cannot be used instead: only a build-pipeline client is trusted to register without a passkey.
3. Copy `tools/ci/register-agent-build.yml` into the agent's repository and set `ZID_API_BASE`, `ZID_CLIENT_ID`, `ZID_CLIENT_KEY`, and `PLATFORM_TOOLS_REF` (a full commit SHA).

**Every release:** the pipeline pushes the image and registers the build. In the console, **Agents & tools → Builds → Waiting for approval** shows what it is (digest, source, a link to the
build record or a warning that none was recorded, who registered it). A different person presses **Approve**. Then steps 3 to 8 above. Deploy by digest (`image@sha256:…`), never a tag.

**Check:** `./workload-identity.sh sync-attestor <workspace>` brings the approved digests into the attestor's file (you write each entry's selectors; you never type a digest).
A registered but unapproved build stays under *Waiting for approval* and runs nothing.

## Local (development), with the stand-in agent

The compose stack builds and runs a stand-in agent, `acme/invoice-agent:1.0.0` (container `payments-agent`, label `team=finance`, user 10001). It lives only on your machine, so the
registry lookup cannot find it: you give its digest by hand, once.

1. Read the digest: `docker image inspect --format '{{.Id}}' acme/invoice-agent:1.0.0` (an image ID, `sha256:…`; a rebuild can change it, then register the new one).
2. Sign in as the owner; **Issue grant** to yourself and to a second person (the split above). The owner needs two people: nothing the registrant registers can be approved by themselves.
3. **Agents & tools → Builds → Register by hand**: Image `acme/invoice-agent:1.0.0`, Build digest = the value from step 1. The second person approves it under *Waiting for approval*.
4. **Tools → Blueprints → Agents → Workloads** in that order, following the setup guide. For the last mile (a real token for the running container) follow `local/workload/README.md`
   (SPIRE, `local/workload/setup.sh`, the attestor).

A local environment does not require an approved build (`ZID_AGENT_BUILDS=auto` skips it in `local`), so you can try the screens first and add the approval when you want to see it enforced:
set `ZID_AGENT_BUILDS=enforce` in `backend/.env` and restart the backend.

## When something is refused

| You see | Meaning | Do |
|---|---|---|
| "Your grants do not yet allow you to register a build" | no `machine.build.register` grant | the owner issues it (above) |
| `BUILD_NOT_APPROVED` | the release's digest is not an approved build | register and approve that exact digest first |
| `WORKLOAD_ARTIFACT_NOT_APPROVED` | the binding names a digest other than its release's approved build | use the release's digest |
| `STEP_UP_REQUIRED` (a person) | a fresh passkey proof is needed | confirm with your passkey and retry |
| `STEP_UP_REQUIRED` / 403 (a pipeline) on anything but registering a build | by design: a pipeline can only propose a build | have a person do it |
| `ATTESTATION_INSUFFICIENT` | what is running is not what was approved, or the attestor has not seen it | sync the attestor, check the container and its digest |
| "could not be looked up" on an image name | not a public image on a named registry | paste the digest from your registry or CI |

## Offboarding a company drains only that company

Closing a company (offboarding) quarantines its connectors, retires its applications and revokes its sessions. The worker that runs it bypasses row-level security, so each of those
steps names the tenant in its own query and refuses to touch another's rows; a regression test with two companies (`test_offboarding_scope_pg.py`) proves the other company is left alone.
An earlier version relied on row-level security alone and, under that worker, would have drained every company at once; if you ever ran an offboarding with that version, check the other
companies' connectors, applications and sessions.
