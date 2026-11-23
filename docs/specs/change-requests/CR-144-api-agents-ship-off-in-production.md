# CR-144: API-based agents ship off in production until a named decision switches them on

Source: owner question, 20 Nov 2026 ("can these be made production ready by other means?"). Builds on CR-093, CR-094 and CR-142. Status: built 20 Nov 2026.

## The problem

An API-bound agent proves a key, not the code behind it. The rules that let it act are the token exchange (CR-094), the assurance level that token gets (the Z1 rule), and, since CR-142, the proof a platform-run agent rests on. None of them is signed by the security owner. Shipping them live in production would make the release depend on a sign-off that has not happened.

## The alternative

One switch, `ZID_API_AGENTS` (`auto` by default, `on`, `off`; `zid_platform/api_agents_switch.py`):

| Setting | Effect |
|---|---|
| `auto` | on outside production, **off in production** |
| `on` | on everywhere: the named decision |
| `off` | off everywhere |

Off means: every API-agent route (`/v1/machine/api-clients`, `/agents/{id}/api-binding`, `/api-agent-token`) answers 404 `API_AGENTS_DISABLED`; a token already issued gets no elevated assurance (the Z1 rule needs the switch); and a bound client no longer counts as an agent's runtime proof, so an API-bound agent's run is refused with `AGENT_NOT_ATTESTED`. Nothing is deleted: switching it on again restores the registered clients, bindings and agents exactly as they were. The cell and the worker configure it from their settings at start-up; a process that was never told reads `ZID_ENVIRONMENT` and `ZID_API_AGENTS` itself, so an unconfigured production process is off, not on by omission.

The container path, the registry, governance and the access readiness agent on a verified image are unaffected: they do not depend on any unsigned rule.

## How it is switched on

The security owner answers section C of `docs/specs/decisions/agent-owner-decision-pack.md`; the deployment then sets `ZID_API_AGENTS=on`. The rehearsal stack sets it, because it is where the feature is tested.

## Also in this change: the run-start proof is read only when a run starts

Found by the concurrency tests, which passed on the last commit and failed with this branch: the live runtime proof of CR-142 section 3 cost three queries and was read for every governed call. Only a run start (and a queued run's claim) needs it. `load_context(..., with_runtime_proof=True)` is now asked for by exactly those two; the per-call gate, reservation and progress calls are back to their earlier cost, and a context built without it carries `None`, which a run start refuses.

Tests: `test_api_agents_switch.py` (the resolution table; an unconfigured production process is off; the routes are a 404 and a token gets no assurance when off) and `test_access_readiness_api_proof_pg.py` (a bound client stops being a proof while off and returns, with the same binding, when on).
