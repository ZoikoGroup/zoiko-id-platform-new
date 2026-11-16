# CR-105: agent governance: permissions, spending, usage, approvals and the emergency stop (ZID-AG-WF-002 v1.0)

Status: **applied, default `shadow`.** Built on the engineering owner's instruction of 2026-11-14 to complete Agent Wireframe 02. The document is a build specification, not a signed policy: the numbers in it are proposed pilot values and the open decisions AG2-D01 to AG2-D11 are the owners'. Nothing here makes an agent GA, sets a price or approves a spend.

## Why

The agent registry, workload identity and tool gateway (CR-021, CR-042, CR-067) answer "who is this agent and may it call this tool at all". ZID-AG-WF-002 asks the next question: what exactly may it do, with what budget, who said so, and how do we stop it. Until now a call that passed the gateway pre-checks and the policy decision point had no lease, no quota, no budget, no company baseline and no emergency control.

## What changed

| Where | Change |
|---|---|
| `machine/domain/governance.py` | Pure rules, no I/O: the decision codes of section 13, the platform prohibition list, exact leases, the policy hash, quotas, exact-decimal money, hierarchical reservation, approval slots and separation of duties, the freeze, telemetry freshness. |
| `machine_0008` | Eleven tenant-scoped tables under row-level security: `governance_policy`, `governance_change_request`, `governance_signature`, `governance_delegate`, `company_baseline`, `budget`, `budget_reservation`, `budget_adjustment`, `run_ledger`, `governance_event`, `freeze_control`. Immutable by trigger: a policy version's content, a change request's snapshot, a budget revision's cap, a reservation's identity. Append-only by trigger: signatures, adjustments, the evidence chain. |
| `machine/application/governance.py` | The service. `govern_tool_call` is called by the tool gateway before the policy decision; an emergency freeze is also checked when a grant is redeemed. |
| `machine/api/governance.py` | About 30 routes under `/v1/machine`. |
| `config/agent-governance/governance.yaml` | Platform quota ceiling, approved currencies, reservation and approval lifetimes, telemetry age. Status PROVISIONAL. |
| `agent.*` actions (16) | In `contracts/action-catalog/actions.yaml`. The four reads join the Owner's seeded console grant; every change, approval, pause and delegation does not (they are business authority, issued per person). |
| Settings | `ZID_AGENT_GOVERNANCE` = off, shadow (default), enforce. `off` is refused outside local, test and dev. |
| Worker | Step `agent_governance`: releases stale reservations, ends overdue runs, expires unsigned requests. |
| Console | Five pages under `/admin/agents/governance`: overview, approvals, and per agent permissions, usage and spending, monitoring. |

## Decisions taken so the build could proceed (each is reversible)

| Ref | Decision | Why |
|---|---|---|
| AG2-D02 | The wireframe's "ten console roles" do not exist: the repository has five (Owner, Admin, Approver, Operator, Viewer). The signer slots SECURITY, AUTHORITY, PRIVACY and FINANCE are **delegates**: a named active member is given a slot by someone with `agent.governance.delegate` (step-up, never to oneself). No role gains authority from the slot list. | Inventing roles would pre-empt the role-policy sign-off the document asks for. |
| AG2-D01 | Hard prohibitions are matched on the tool's catalogued action and side effect, plus the registry's own management actions. A tenant baseline cannot list a prohibited tool, a lease cannot name one, and a simulation shows the denial. | Section 03. |
| AG2-D04 | Pilot quotas are the document's defaults, held in code; the platform ceiling is the file. A company may lower, never raise. | Section 06. |
| AG2-D05 | No budget is created by default. With no ceiling a paid step is refused. Any cap above the one in force, and the first cap, needs Finance and Security. A reduction is live at once. Currencies are the approved list. The price version is `UNPRICED`. | Section 05: AI is $0 until opt-in and FinOps approval. |
| AG2-D07 | Routes are `/v1/machine/...` (the existing prefix), not `/api/v1/...`. The console is `/admin/agents/governance`, not `/console/...`. | One routing standard. |
| Mode | Default is `shadow`: a refusal is recorded as `SHADOW_DENY` and the call proceeds. Moving to `enforce` is an owner decision per environment. The emergency freeze is enforced in every mode. | The pattern of CR-072 to CR-086. |
| Entitlement | Every governed call reads the plan's agent feature (`advanced.agent-workload-identities`) through the commercial resolver and follows the platform's commercial enforcement switch; an answer that cannot be read is POLICY_UNAVAILABLE. | Added in CR-106; it was a constant before. |
| Step-up | Enforced by the route guard (`config/step-up/route-actions.yaml`) in front of signing, applying, delegating and requesting a restore. | The platform switch decides enforcement. |

## What it deliberately does not do

- It does not build the DAARA runner (Agent Wireframe 01). The run, reservation and settlement routes are the integration point a runner will call; nothing here runs an agent.
- It has no AI explanation adapter and no model gateway. The reservation path for AI cost exists and refuses until a provider is approved and a cap is set.
- It does not mark `agent.run.manage` as machine-callable (CR-066 is not ratified for more than `machine.build.register`). A runner must act through a person's or service's governed grant until the Security owner rules.
- Alerts are computed from live state. Acknowledge, route, SLA and grouping were added in CR-106; there is still no incident casework beyond the freeze row.
- Restoring a freeze does not reactivate the agent: it stays SUSPENDED until it passes its activation gates again.
- It measures nothing about performance: the 5-second emergency target and the 100-concurrency proof are tested for correctness, not timed at scale.

## Owner decisions still needed

AG2-D01 (adopt the first agent), D03 (approver assignment policy), D04 (pilot quotas against plan limits), D05 (caps, price sources, ledger currency, finance delegates), D06 (approved AI providers and regions), D08 (retention and region of cost records, telemetry and approvals), D09 (the kill-switch target and ownership), D10 (Figma tokens), D11 (customer terms). Gates G0 to G5 of section 18 need named people; none can be granted by code.

## Evidence

`backend/app/tests/unit/test_agent_governance.py`, `backend/app/tests/component/test_agent_governance_pg.py`, `frontend/apps/web/src/features/admin/agents/governance/logic.test.ts`, `docs/qa/agent-governance-matrix.md`.
