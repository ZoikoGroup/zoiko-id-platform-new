# Agent governance: turning it on and running it (ZID-AG-WF-002, CR-105)

Written for the people who operate the platform and for a company's Owner and Security lead. It describes what the build does today, not what is certified. Every number mentioned is a proposed pilot value from the document, not a promise to a customer.

## What it controls

An agent that passes the tool gateway's identity checks is still stopped unless a **policy version** names the tool, the resource, the purpose, the region and an expiry, **people signed that exact version**, the **company baseline** allows the tool, and the call is **inside the run, record and spending limits**. An **emergency pause** stops everything at the next check.

## Modes (`ZID_AGENT_GOVERNANCE`)

| Mode | What happens to a call that would be refused |
|---|---|
| `shadow` (default) | It goes through. The refusal is written to the agent evidence stream as `SHADOW_DENY` and shown on the Overview as "would be refused". |
| `enforce` | It is refused with the decision code (`SCOPE_NOT_APPROVED`, `REGION_DENIED`, `APPROVAL_REQUIRED`, `BUDGET_EXCEEDED`, `USAGE_LIMIT`, `POLICY_UNAVAILABLE`, ...). |
| `off` | No governance check. Refused outside local, test and dev. |

An emergency pause is enforced in **every** mode. Moving a company from shadow to enforce is an owner decision: read the "would be refused" list first (Governance, Overview, Recent policy decisions), because in enforce mode an agent with no live policy cannot call anything.

## First setup for a company (in this order)

1. **Company baseline** (`PUT /v1/machine/governance/baseline`, needs `agent.governance.manage`): the tools, regions, AI providers and limits a company allows. A company can lower the platform limits, never raise them, and cannot list a tool the platform prohibits. With no baseline, no agent may run.
2. **Name the signers** (`POST /v1/machine/governance/delegates`, needs `agent.governance.delegate`, step-up): give an active member the SECURITY, AUTHORITY, PRIVACY or FINANCE slot. Nobody grants a slot to themselves. These are not new roles; they decide who may sign which kind of change.
3. **Draft a policy version** for the agent (console: Governance, the agent, Permissions, Request a change). It is requested access, not effective access.
4. **Request approval.** The kind is worked out from the difference and cannot be chosen: first publish, scope increase, turning AI on, or a limit increase. Each needs its own signers:

| Change | Independent signers |
|---|---|
| Publish, scope increase | Security and Authority (and Privacy if a new data class is added) |
| Turn AI explanations on | Security, Privacy and Finance |
| Raise a budget or limit | Finance and Security |
| Restore after an emergency stop | Security and Authority, neither of them the person who paused |

   The requester, the person who wrote the draft and the agent's operator can never be the only signer. Signing needs a fresh passkey check. A request that is not fully signed within 24 hours expires. Any edit to the draft voids it.
5. **Apply** the approved version (`:apply`). Approved is not live: it goes live only while the agent is ACTIVE and the approval is still fresh and bound to the same hash.
6. **Budgets** (optional, only needed for AI cost): set the company ceiling first, then the agent's cap, then a per-call cap. With no cap, nothing paid can be reserved. A raise is pending until signed; a reduction takes effect at once.

## Running the emergency stop

- **Pause** one agent (Monitoring page) or the whole company (Overview). It asks for a reason and a typed word, needs no approval, suspends the agent, cancels running work and releases what it had reserved. The server confirms before the page says "stopped".
- **Restore** is a request that needs two other people and fresh checks (evidence chain intact, sponsor active, live policy bound to an approval). Restoring does **not** reactivate the agent: it stays SUSPENDED until it passes its activation gates again.

## Downloading the evidence

The overview and each agent's monitoring page have an export to CSV (`agent.audit.export`). It is redacted by role exactly as the page is: without `agent.audit.read_detail` the resource, actor, tool, region, policy hash, trace and cost columns are not in the file. Cells a spreadsheet would run as formulas are made inert, the file is capped at 5000 rows, and each export writes its own evidence row.

## What the evidence stream is

Every governed call, run start, reservation, approval, apply, pause and restore writes one row to `agent.governance_event`, chained to the previous row by digest and impossible to edit or delete (a database trigger refuses, even for the owner role). **Governance, the agent, Monitoring, Verify the chain** recomputes every digest of every chain (one per agent, one for company-level rows) and names the first broken record. A role without `agent.audit.read_detail` sees the stream without resource, actor, tool, region or cost.

## Known limits (read before relying on it)

- The DAARA runner (Agent Wireframe 01, CR-106) can run inline or be queued for a worker, and it calls the governance gates directly rather than requesting a gateway grant per read. A daily schedule exists but is switched off by the platform ceiling in `config/agent-governance/governance.yaml` (`platform_quota_ceiling.schedules_enabled`); an owner raising it is not enough on its own, because the company baseline and the agent's approved policy must allow it too.
- Not measured: the 5-second stop target, 100-way concurrency at production load, and screen-reader use of the five pages.
- The wireframe's "ten console roles" do not exist here; slots are delegates (decision AG2-D02).
- Alerts are computed from live state, then tracked: each can be acknowledged and routed to a team, has a response time (P0 15 min, P1 4 h, P2 24 h, provisional), is grouped when it repeats, and leaves the list when its condition stops. Acknowledging clears nothing and lifts no limit.
- Prices, caps, quotas, providers and retention are all owner decisions (AG2-D04 to D09). The `UNPRICED` label stays on every reservation until finance supplies one.
