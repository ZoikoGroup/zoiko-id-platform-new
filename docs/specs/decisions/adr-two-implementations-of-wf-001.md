# Decision: two implementations of the Delegated Authority and Access Readiness Agent (ZID-AG-WF-001)

Status: **Option 1 APPLIED on the engineering owner's instruction of 2026-11-16 (as CR-094 was). The platform owner and the security owner have NOT signed.** DAARA's routes answer 404 unless `ZID_DAARA_ENABLED=true` (default off), its worker steps do nothing, and the console says it was replaced. Nothing was deleted: the code, the tables (`machine_0009`) and every record stay, so setting the flag brings it back unchanged. The Access Readiness agent now also honours the governance emergency stop (company-wide and per agent), before every page it reads.
Date: 16 Nov 2026. Raised when `origin/main` was merged into `anil`.

## What happened

Two teams built the same agent from the same specification (ZID-AG-WF-001), both citing CR-091. (The DAARA change requests have since been renumbered: agent governance CR-105, DAARA CR-106, and the screening meters CR-104, so each number names one change.) Both run today, side by side, with no conflict in code, routes, tables or actions.

| | DAARA (branch `anil`, CR-106) | Access Readiness (`main`, CR-091) |
|---|---|---|
| Where it lives | inside the `machine` module (`machine/*/daara*.py`) | its own module, `modules/access_readiness`, own migration stream |
| Size | about 1,760 lines | about 4,150 lines |
| Rules | R-01 to R-06 | R-01 to R-06 (same codes and meanings) |
| API | `/v1/machine/agents/{id}/daara…`, `/v1/machine/agent-review` | `/v1/access-readiness/…`, `/v1/access-readiness/review-proposals` |
| Actions | `agent.daara.*`, `agent.review.*` | `access_readiness.read / manage / approve / review` |
| Release | the AG-WF-002 governance approval (policy hash, two to four signers) | its own approval of the exact scope digest by a different person (R3) |
| Credential for a run | in-process calls through the governance gates (budget, freeze, quotas) | a scoped worker lease issued per run |
| Evidence | per-agent hash chain from governance | server-side export, purpose required, redacted by role |
| Optional AI explanation | checked against the facts, budget reserved and settled | checked against the facts, off by default at the cell |
| Console | setup page under agent governance | wizard, run detail, review hand-off, accessibility spec |
| Default | shadow mode | off (every route 404 until a cell turns it on) |
| Component tests | 9 | 14 |

Both are real and tested. The cost of keeping both is a second set of everything: two review queues for the same finding, two sets of permissions to grant, two approval paths, two evidence formats, and two things for an independent tester to run for the same acceptance rows.

## Options

1. **Keep Access Readiness as the one WF-001 agent; keep governance (WF-002) for every agent; retire DAARA's own routes.** One module per spec, the larger test and console coverage, off by default. Cost: Access Readiness would need to call the governance gates (budget, freeze, company baseline) that DAARA already uses, so a company-wide stop also stops it. Roughly the size of one slice of work.
2. **Keep DAARA as the one agent; retire Access Readiness.** Smaller and already inside governance. Cost: loses the dedicated module, the run lease, the server-side export and the console screens that `main` now ships; the console work would be redone.
3. **Keep both** and state that they are separate products (for example Access Readiness for customers, DAARA as the internal reference agent used to prove governance). Cost: the duplication above, permanently.

## Recommendation

Option 1. The spec asks for one agent. `main` already carries the module boundary, screens and exports the spec describes, and governance already carries what every agent needs in common. The missing link is small and testable: make a readiness run pass through the governance gates before it reads anything.

## Before anything is removed

- Neither code path is deleted by this note. Both stay until the decision is signed.
- Moving a tenant from one agent to the other must keep its review proposals and evidence readable; neither store is dropped.
- The acceptance rows (AT-01 to AT-24) are written against both; whichever stays must re-run them.

## Sign-off (UNSIGNED)

| Role | Name | Date | Option chosen |
|---|---|---|---|
| Platform owner | | | |
| Security owner | | | |

## What was done under option 1 (2026-11-16)

- `ZID_DAARA_ENABLED` (default false): the eleven DAARA routes (`/v1/machine/daara/template`, `/v1/machine/agents/{id}/daara...`, `/v1/machine/agent-review...`) answer 404 with a message that names the Access Readiness agent. The governance alert routes that share the file are not DAARA's and stay on.
- The DAARA worker steps (`daara_schedule`, `daara_queue`) run only when the flag is on.
- The governance console's DAARA tab shows "Replaced by the Access readiness agent" with a link, instead of an error.
- Access Readiness runs now refuse to start, and stop before the next page, while an emergency stop of agent governance covers the agent or the whole company (`EMERGENCY_FROZEN`); a stop that cannot be read refuses the run (`GOVERNANCE_UNAVAILABLE`).
- Tests: `test_daara_is_retired_by_default_its_routes_answer_404_and_the_alert_routes_stay`, `test_an_emergency_stop_of_agent_governance_stops_the_readiness_agent_too`. The DAARA component tests still run, with the flag on.

## Reversal

Set `ZID_DAARA_ENABLED=true`. Nothing else changes. To remove the code later, drop the module and its tests in one reviewed change, and migrate or archive `agent.daara_*` tables under the privacy officer's retention rules; neither is done here.

## What is still open

The owners' signatures above, and whether Access Readiness should also reserve a budget or count against agent quotas (it passes the stop, not the budget gates). Say so if the owners want that.
