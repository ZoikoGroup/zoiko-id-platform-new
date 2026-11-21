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

**Running the automated spec against a stack that sends real email (no mail catcher).** The spec needs a signed-in person. Give it a one-use enrollment ticket and it signs in with a virtual passkey, with no email involved:

1. Get a ticket for an existing company person: a company administrator issues an enrollment link (Account, Passkeys, add a passkey), or on a local stack `python tools/scripts/bootstrap_local.py --ticket-only`. The ticket is the `ticket=` value in the link, and it works once.
2. From a shell whose folder is written with a capital drive letter (a lowercase `c:` makes Vitest and Playwright load two copies of themselves on Windows):

```
cd C:/Users/<you>/Downloads/zoiko_ids/frontend
E2E_ADMIN_TICKET=<ticket> node node_modules/@playwright/test/cli.js test -c tests/e2e/playwright.config.ts tests/e2e/agent-governance-a11y.spec.ts
```

Only the governance data is mocked; the session is real. Without `E2E_ADMIN_TICKET` the spec signs up a throwaway person and reads the mail from `E2E_MAIL_API` (default http://localhost:8025), which needs a mail catcher the production-image stack deliberately does not have.

## 3. Sign-off

| Role | Name | Date | Result |
|---|---|---|---|
| Independent QA | | | |
| Security | | | |
| Accessibility tester | | | |

## Repeating the automated rows, and comparing

`tools/scripts/acceptance_run.py` runs exactly the test each acceptance row links, with the CI flags, and writes a record: the commit, whether the working tree was clean (a dirty tree is marked `+dirty` and is not the commit it names), the interpreter and platform, a hash of the rows it ran, and PASS, FAIL, NOT_RUN or NOT_AUTOMATED for every row.

```
cd backend
python -m uv run python ../tools/scripts/acceptance_run.py --environment ci --out acceptance-run.json --markdown acceptance-run.md
```

Engineering runs it and attaches the record. **An independent tester then runs the same command themselves, on a clean checkout of the named commit, with `--executed-by "<their name or firm>"`**, which is the only thing that marks a run independent. The two records are comparable row for row when their `rows_hash` matches; a row that passes for engineering and fails for the tester is a finding. Component tests need `ZID_TEST_PG_URL` (a disposable PostgreSQL). Rows that are not tests (a measurement, a document, the screen-reader pass) read NOT_AUTOMATED and still need a person.

The runner never edits `docs/traceability/acceptance-tests.csv`. A passing row is a result, not a verdict: only the approvers change a row's status, and only on evidence they have seen.
