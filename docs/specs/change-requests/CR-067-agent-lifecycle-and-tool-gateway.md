# CR-067: Agents stop when their sponsor leaves, and every tool call goes through a gateway (ZID-GAP-001 WP-10, WP-11; ZID-MACHINE-001 §12-15)

Status: open (built and tested; the AI Security Lead and Product confirm the decisions below before this is relied on in production).

## Why
ZID-GAP-001 lists two release-blocking agent gaps (G2): an agent must not become an unbounded proxy for the person behind it, and a tool call must be checked at the
moment it happens. Reading the code against that plan showed the *rules* already existed in the domain (`tool_invocation_blockers`, `register_child_agent`,
`respond(SPONSOR_REMOVED)`) but nothing called them: no path took a tool call, and nothing suspended an agent when its sponsor left.

## What changes
**WP-10, agent lifecycle**
- `agent.agent_profile.expires_at` (machine_0006, optional, nothing backfilled). An agent past its date no longer acts. A sub-agent inherits its parent's date.
- `lifecycle_breach` (pure): an ACTIVE agent whose sponsor is missing, disabled, erased or otherwise not ACTIVE is orphaned; one past `expires_at` is expired.
  It fails closed: a sponsor that cannot be read counts as gone.
- Worker step `agent_lifecycle` (`lifecycle_sweep`) suspends such agents by policy through the existing kill path, so sub-agents and live bindings stop with them.
  The actor on the trail is `SYSTEM`, the reason code is `SPONSOR_NOT_ACTIVE` or `AGENT_EXPIRED`. Idempotent. A poll covers every way a sponsor can leave
  (offboarding, disable, erasure) without hooking each one.

**WP-11, the agent-to-tool gateway** (`POST /v1/machine/tool-calls:authorize`)
- The caller is the agent: a `workload`-scoped token whose subject is the agent principal. The agent is never a body field, so a person's token, a token without
  the workload scope and a forged body are all refused.
- Pre-checks (pure, `domain/tool_gateway.py`): every agent in the chain, leaf to root, is ACTIVE, unexpired and has an active sponsor; every one lists the tool (a
  parent that drops a tool takes it from its children); the tool is registered and ACTIVE; the chain is intact, single-tenant, loop-free and not deeper than its root allows.
- The policy decision is the same one the AuthZEN endpoint gives: that logic moved, unchanged, into `decision/application/access.py` (`evaluate_access`), and both call it.
  A tool whose canonical action is not in the action catalogue has no policy, so no permission.
- A tool flagged `requires_human_approval` is refused until an APPROVED approval is bound to this exact call (`action|resource|arguments digest`); the refusal names that context.
- On success the gateway signs a grant (`action-grant+jwt`) bound to the tool's own server (audience), the resource, the arguments digest, the chain commitment and a nonce.
  It lives at most 120 seconds and never outlives the decision it rests on.
- Every outcome, allow or deny, is an audit record (`tool_call.authorized` SUCCESS, `tool_call.denied` DENIED) with an `AGENT` actor and the whole chain.
- **Grants are spent once** (machine_0007, `agent.tool_grant`). The gateway records each grant's nonce. The tool's own server then calls
  `POST /v1/machine/tool-grants:redeem` with the grant and the digest of the arguments it is about to run. Redemption verifies the signature, requires
  the audience to be the caller's own principal, the tenant and the arguments digest to match, **re-checks the whole delegation chain at that moment**
  (a sponsor who left, a suspended parent or an expired agent since signing stops the call), and marks the grant used in one atomic UPDATE, so two
  parallel redemptions cannot both win. A refusal at redemption is an audit record too (`tool_call.refused`, DENIED). `tool_call.redeemed` is SUCCESS.
- A tool server that does not want to call Zoiko for every check can run the pure rules itself (`grant_problems`) after verifying the signature
  with the published keys; only the "once" needs the redeem call.

## Tests
`test_agent_lifecycle.py` (unit), `test_agent_lifecycle_pg.py`, `test_tool_gateway.py` (unit, 17 cases), `test_tool_gateway_pg.py` (HTTP, real tokens, real policy decision:
allowed call, person asking in an agent's name, missing workload scope, forged body, unlisted tool, unknown tool, no authority, approval required, suspended parent,
sponsor leaves), `test_tool_grants_pg.py` (an APPROVED human approval bound to the exact arguments and refused when one byte changes; redeem once; replay refused;
wrong server, other arguments, forged signature and a person refused; sponsor leaves between signing and redeeming; eight parallel redemptions, one winner;
the gateway's decision receipt sealed, exported and verified offline with only the published keys, and a tampered copy refused),
`test_agent_revocation_propagation_pg.py` (commit to first refusal for three revocation triggers against the R3 in-cell budget). The AuthZEN contract and tests are unchanged by the move.

## Found while testing, and fixed
- A tool that requires a mandate could be called by an agent on its own account with no mandate, because the policy decision only asks for a mandate when a machine acts for someone else. The gateway now requires the mandate and the represented person for such a tool (`MANDATE_REQUIRED`).
- With the database taken away from a running cell, every request answered `500 INTERNAL_ERROR` (not retryable, no request id). It failed closed, but a client could not tell an outage from a bug. A lost dependency (refused or dropped connection, timeout) now answers `503 DEPENDENCY_UNAVAILABLE`, retryable, in `zid_app`. Test: `test_chaos_degradation_pg.py`, `libs/zid-app/tests/test_dependency_outage.py`.

## Not done (said plainly)
- **The SDK is a first cut.** `sdks/python` (`zoiko-tool-server`, guide in `docs/sdk/tool-server-sdk.md`) verifies a grant offline, redeems it once and ships a reference tool server. It does not fetch or rotate the platform's signing keys for you beyond a single fetch, it has no other languages (the Go, Java, .NET and TypeScript folders are still empty), and nobody outside the repo has integrated it.
- **The grant's nonce is checked at the Zoiko cell only.** A tool server that never calls redeem gets signature and expiry protection but not single use.
- **A human approval is re-checked at signing, not again at redemption.** The chain is re-checked at redemption; the approval state is not (its transaction binding already ties it to the exact arguments).
- **No red-team prompt corpus.** The gateway never reads model text, so prompt injection cannot change what it allows, but that is a design argument, not a measured result.
- **Revocation was measured at the gateway only**, on one developer machine (p99 about 180-290 ms against a 2 s budget), not through the UI, queue, webhook or wallet channels, and not under a performance-environment load.
- **No load figures for the gateway itself** (the plan's decision-service objectives, p95 100 ms, are unmeasured here).
- The tool registry has no per-tool owner or expiry yet.

## Owner decisions (proposed values, none ratified)
1. **Grant lifetime**: proposed 120 seconds. Shorter is safer for irreversible tools; longer helps slow tool servers.
2. **Should the sweep also review agents on a schedule** (for example, a periodic human re-attestation), not only on sponsor loss and expiry? Proposed: yes, a later slice.
3. **Is `workload` scope the right gate for callers**, or should the gateway also require the certificate binding of the workload token? Proposed: also require it in production.
