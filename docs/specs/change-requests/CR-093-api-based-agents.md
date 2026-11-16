# CR-093: API-based agents: an agent whose software proof is a registered API client, not a container image (ZID-MACHINE-001 §3, §9, §16)

Status: **applied for registration, approval, binding, activation, the tool cap and the kill paths. The agent's own credential path is NOT built and is a decision for the security owner.** Raised on the engineering owner's instruction of 2026-11-14: keep everything that exists for agents, and add a way to create them without Docker, with whatever they need stored in the database.

## Why

The registry assumed an image. A blueprint release comes from an approved build (an image digest), and an agent can be ACTIVE only with a live workload binding that the attestor proves against that digest. A client that talks to the platform over its API has no image to attest. It can still be an accountable, sponsored, governed agent.

## What changed

| Where | Change |
|---|---|
| `machine_0010` | `build_artifact.kind` (`IMAGE` default, or `API_CLIENT`) and `client_descriptor` (public facts only, enforced by a check that the two go together). New `api_client_binding` (one live binding per agent), row-level security. |
| `domain/api_agents.py` | Public keys only: a key with any private member, a remote reference (`jku`, `x5u`, `x5c`), a weak or unnamed curve, an RSA key under 2048 bits, an `HS*` or `enc` key, a missing `kid` or a duplicate `kid` is refused by code. RFC 7638 thumbprints, a descriptor (client id, key thumbprints, audiences) and its digest. The tool cap. |
| `application/api_agents.py`, `api/api_agents.py` | `POST /v1/machine/api-clients`, `GET/POST /v1/machine/agents/{id}/api-binding`, `POST .../api-binding:revoke`. Existing actions (`machine.build.register`, `machine.agent.read`, `machine.agent.manage`). |
| `agent_store`, `build_store` | The runtime-binding count includes an API binding. The kill paths that move or suspend workload bindings (agent suspend or revoke, blueprint revoke, build revoke) move API bindings too. |
| `tool_gateway.py` | Every call from an agent in a chain that holds an API binding is checked against the cap. |
| Console | "Register API client" on the Agents page; an "API client binding" card on every agent, with Bind and Revoke. |

## The flow (the registry's own, the same two-person rules)

1. A person registers the client (name, PUBLIC keys, exact audiences). The platform generates the client id and the build digest. The build is DRAFT.
2. A **second** person approves the build. The registrar cannot.
3. A blueprint release is created from the approved build and approved by someone other than its publisher.
4. The agent is registered from the release (sponsor, operator, owning organization, tools).
5. The approved client is bound to the agent. The binding satisfies the runtime-binding gate.
6. The other gates are unchanged: sponsor, approved blueprint, tool manifest, independent security review, kill path, short-lived credentials, traceability. Then activate.

Changing a key is a different descriptor, so a different digest, so a different build, release and approval. A compromised client is stopped by revoking its build (every binding on that digest is suspended and its agents with it) or by revoking the binding (a running agent is suspended).

## What an API client cannot prove, and what the platform does about it

An API client proves it holds a private key. It cannot prove what code runs behind that key, which is exactly what the image attestor proves on the image path. So, by code and not by policy text:

- An agent bound to an API client may hold only tools whose side effect is `NONE` or `REVERSIBLE` and whose risk class is at most 1 (lowered from 2 on 2026-11-14 to match the token class; see the decision pack). A tool that moves money, cannot be undone or is above the ceiling is refused at binding and again at every gateway call (`API_AGENT_TOOL_NOT_ALLOWED`).
- An attestation ceiling of 1 for such an agent is defined (`API_ATTESTATION_CAP`) but **no gate reads it yet**: today the protection is the tool cap above. It becomes meaningful only when the token path exists and an owner sets the floor for it.

## What it deliberately does not do

- **It gives the client no way to get a token.** How a signed-key client obtains a credential for an agent principal is the security owner's decision (CR-066 covers only the build pipeline's `machine.build.register`). Until then a bound API agent can be set up, governed, approved and activated, and DAARA and the governance plane work for it (they run inside the platform), but it has no credential of its own to call the tool gateway.
- It stores no private key and never echoes a refused one.
- It does not fetch keys from a URL.
- It does not change the image path: the container binding, the attestor and every image test are untouched.

## Owner decisions still needed

1. May an API-only agent exist at all, and is risk class 1 the right ceiling (it was 2 until 2026-11-14)?
2. Which actions may a signed-key client call, and how does it obtain a token for the agent (extends CR-066)?
3. Does the attestation floor for risk class 2 tools stay at the image-path level for everyone else while API agents are capped at 1?

## Evidence

`backend/app/tests/unit/test_api_agents.py`, `backend/app/tests/component/test_api_agents_pg.py`, `frontend/apps/web/src/features/admin/agents/api-agent.test.ts`.
