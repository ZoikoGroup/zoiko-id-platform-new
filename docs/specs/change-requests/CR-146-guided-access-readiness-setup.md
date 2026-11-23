# CR-146: Guided setup for the access readiness agent

Status: built 21 Nov 2026 (uncommitted). Frontend only; no API or rule change.

**Problem.** Setting up the access readiness agent took 12 steps across five registry pages and two people, with traps a real user cannot see: the blueprint id must be exactly `daara`, the tools must be the two read tools, the builder cannot approve their own key or release, and the security review must come from someone outside the chain.

**Change.** One page, `/admin/agents/readiness/start`, reads the company's real state and shows: progress, the next step in plain language, whose turn it is, and one button. Both people open the same page and each finds their own step. A pure planner (`guided-setup-logic.ts`, 19 tests) derives the steps; the page (`guided-setup.tsx`) runs the same API calls the registry pages run.

**Rules kept.** The server still decides every action. The page never attests a gate for the user: each human check has its own tick box, and only ticked ones are confirmed. Separation of duties is shown as "Waiting for someone else", not bypassed.

**Entry points.** Access readiness home ("Set up step by step") and the Access readiness banner on Agents & tools.

**Not built.** A screen-reader pass on the new page; a component render test of the page itself.
