# Independent test run and screen-reader protocol for the agent features

Status: **PROTOCOL. No run has been done.** For a tester who is not on the build team. A row passes only with a dated record and a link to its evidence. Matrices: [agent-governance-matrix.md](agent-governance-matrix.md), [agent-daara-matrix.md](agent-daara-matrix.md); rows in `docs/traceability/acceptance-tests.csv` (AG2-T, AG1-AT, AG3-T).

## 1. Independent run of the acceptance rows

1. Start from a clean checkout of the signed build, on a fresh database.
2. For each row marked EVIDENCE or PARTIAL, run the linked test, then try the behaviour by hand in the console.
3. Record one line per row in the table below. A row the tester could not run stays NOT_RUN, never PASS.
4. Security separately attacks: the gateway with a stolen or expired token, a replayed API-client assertion, a lease with a wildcard, a budget with huge or negative amounts, a tenant reading another tenant's agent.

| Row | Tester | Date | Build | Result (PASS, FAIL, NOT_RUN) | Evidence link |
|---|---|---|---|---|---|

## 2. Screen-reader and keyboard pass

Pages: governance overview, approvals, permissions, limits, monitoring, DAARA setup (all 5 steps), run detail, review queue, Register API client dialog, API client binding card.

| Check | How | Pass when |
|---|---|---|
| Keyboard only | Tab, Shift+Tab, Enter, Space, Escape through every page and dialog | Every control reachable, visible focus, no trap, Escape closes dialogs and returns focus |
| NVDA with Firefox, NVDA with Chrome | Read each page top to bottom, then act | Headings, landmarks, labels, errors and status changes are announced |
| VoiceOver with Safari (Mac and iOS) | Same script | Same |
| Live messages | Trigger an error, a success and a refusal | Announced once, without moving focus |
| Zoom 200 percent and 400 percent reflow | Browser zoom | No loss of content, no horizontal scroll at phone width |
| Colour | Forced colours and a colour-blind simulator | Status is never colour only |
| Tables | Approvals, runs, alerts | Header cells announced with each value |

Record each as a row in the table in section 1 against AG1-AT22. The automated spec is `frontend/tests/e2e/agent-governance-a11y.spec.ts`; it does not replace this pass.

## 3. Sign-off

| Role | Name | Date | Result |
|---|---|---|---|
| Independent QA | | | |
| Security | | | |
| Accessibility tester | | | |
