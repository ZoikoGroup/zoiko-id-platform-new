# CR-021: Agent, blueprint and tool registry API; activation-gate attestations

- **Target:** ZID-MACHINE-001 §2-3, §12-15, §18, §21, §23; ZID-UX-001 §13; action catalogue · **Raised by:** Phase H H-5 (Document 09 is not placed in the Guideline sequence; engineering placed it in C, DEC-D3/F7) · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `agent.agent_gate_attestation` | machine | The four §12.1 activation gates only a person can establish (security review, kill path tested, short-lived credentials, observability): who attested, when, a short note. One row per (agent, gate). Activation reads these records, never a caller's claim. The security review must be independent of the agent's sponsor, operator and publisher. RLS on `tenant_id`. |

`agent.agent_profile`, `agent.blueprint_release`, `agent.tool_capability` and `agent.workload_binding` already exist (CR-003, CR-007).

## API

`/v1/machine/agents`, `/v1/machine/blueprints`, `/v1/machine/tools` (list, register, lifecycle, sub-agents, gate attestation, readiness). Publisher, operator, sponsor and owning organization stay separate fields.

## Actions

| Action | Risk / evidence |
|---|---|
| `machine.agent.read` | R1 / E1 |
| `machine.agent.manage` (register, sub-agent, attest, suspend) | R2 / E2 |
| `machine.agent.activate` (activate, revoke, retire) | R3 / E3 |
| `machine.blueprint.manage` (draft, approve, revoke) | R3 / E3 |
| `machine.tool.manage` | R2 / E2 |

## Events

No new event types: `agent.registered`, `.activated`, `.suspended`, `.revoked`, `agent.blueprint.approved`, `.revoked`, `subagent.created`, `tool.registered`, `tool.suspended` are catalogued.

## Rules implemented

- A blueprint release is immutable; its publisher cannot approve it. Revoking one suspends every live binding that runs its artifact and the agents built on it (history kept).
- An agent is created DRAFT with a sponsor; ACTIVE needs all ten §12.1 gates. Suspending an agent suspends its sub-agents and live bindings; revoking is terminal.
- A tool is DRAFT until activated; a side-effecting tool always requires a mandate; discovery never makes a tool executable.
