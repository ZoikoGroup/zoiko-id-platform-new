# Phase N: Console guide (signed-in, guide-only)

> **Status:** engineering proposal, built and tested as a prototype on 2026-11-11 at the product owner's request. Not reviewed or ratified by Security, Product or the CTO, and **not part of the ten public-chatbot documents** (see "How this relates to the public assistant").

Back to the [phase index](README.md). The anonymous [public assistant](phase-m-public-assistant.md) is a different feature with a different trust model.

## What it is

A helper inside the web console for **every signed-in person with one of the five customer roles** (OWNER, ADMIN, APPROVER, OPERATOR, VIEWER, or no company role). It explains the console and the person's **own role**:

| Question | Example | Source |
|---|---|---|
| What can I do? | "What can I do with my role?" | the person's roles x the published role matrix |
| Can I...? / why can't I...? | "Can I invite a viewer?" | one matrix row, the person's mode (run, request, approve, view, own account, none) and the row's rule |
| Who can...? | "Who can approve role grants?" | the matrix row across all five roles |
| What is a role? / how do two differ? | "Admin vs Owner" | the matrix, plus a short curated description per role |
| How do I get more access? | "How do I get Admin access?" | the matrix rows for requesting and approving a grant |
| Where do I find...? | "Where do I manage my passkeys?" | a fixed list of the console's pages |

**Guide-only by design.** It cannot read company data (members, invoices, sessions, approvals) and cannot change anything. A request to do or look up something is declined with a pointer to the right page, and what the person's role allows for it.

## Decisions

| ID | Decision | Why |
|---|---|---|
| **DEC-N1** | It runs **entirely in the browser**: a deterministic engine over two catalogues the console already loads for its menus, `GET /v1/roles/matrix` and `GET /v1/tenant/me/permissions` (both documented as "navigation only"). **No new backend route, no model, no server call carrying the question.** | The smallest attack surface that still answers the question. What the person types never leaves their browser, so there is nothing to log, store or leak. |
| **DEC-N2** | The matrix **describes policy; it does not decide anything.** Every permission answer says "this is what your role allows in principle; the platform still checks scope, approvals and security". While the owners keep the matrix in report-only mode (`enforcement: false`) the answer also says it is not enforced everywhere yet. | The matrix itself says "a role is never enough on its own", and enforcement is still report-only. The guide must never imply a guarantee. |
| **DEC-N3** | The UI is the **public assistant's widget, reused** through its injectable `fetchImpl` plus a small, backward-compatible `copy` override. Answers are escaped plain text; links are ids resolved by a **fixed map** (`/guide/out/<id>`). | Reuses tested focus, accessibility and rendering code. No open redirect: nothing a person or a server supplies chooses a destination. |
| **DEC-N4** | Matching is **deterministic** (stemmed, synonym-aware, scored over the 68 real matrix rows) and **abstains** rather than guesses. | Correctness is provable: a property test asks about every action as every role and checks the exact answer. |
| **DEC-N5** | **A secret-looking question is refused** (422, as in the public assistant) and never echoed. | Defence in depth, although nothing is sent anywhere. |

## How this relates to the public assistant

ZI-CHAT-ADR-006 **ADR-01 excludes a private console copilot** from the public assistant and says one "would require its own threat model, RBAC/ABAC, consent, UX and ADRs". This feature is that separate, much smaller thing, and it keeps to ADR-01's reasons:

- The **public assistant is untouched** and still holds no identity, session or role. This guide shares none of its backend.
- No identity or tenant capability is added to any public surface.
- The guide's own threat model is below. It needs a Security review before anyone treats it as approved.

## Threat model (guide-only)

| Threat | Control | Proof |
|---|---|---|
| The guide leaks data it should not | It reads only the two catalogues every signed-in person can already fetch; no company data is read | `test/local-api.test.ts` (loaders are the only I/O) |
| The question is sent, stored or logged | It is answered locally; the source has no `fetch`, storage, cookie, beacon, socket, or `console.log` | `local-api.test.ts` "what leaves the browser: nothing" (spy on `fetch`, source scan); browser run: only the two GETs went out |
| A wrong answer misleads the person | Property test over all 68 actions x 5 roles; standing disclaimer; enforcement caveat | `engine.test.ts` (340 combinations) |
| An open redirect or script injection | Fixed link map with an own-property lookup (`constructor`, `__proto__` refused); answers rendered as escaped text | `console-guide.test.ts`, widget tests |
| The guide's page list drifts from the real menu | Test compares it, both directions, to the navigation in `console-shell.tsx` | `console-guide.test.ts` |
| The test matrix drifts from the policy | Backend test compares the fixture to `role-matrix.yaml` | `backend/app/tests/unit/test_console_guide_fixture.py` |
| It is mistaken for the public assistant | Own name, launcher and welcome text via the widget `copy` override | widget `copy` tests |
| Over-reach later (data reads, actions) | Explicitly out of scope; would need its own ADR, permission model and confirmation design | this document |

## Limits (honest)

- It understands a fixed set of question types in English and **abstains on the rest**; rephrasing helps. Better coverage (or a model) would be a separate, reviewed decision.
- It knows the 68 matrix rows and ~37 console pages, not product documentation or the company's own configuration.
- It shows page links for every page, including ones the person's menu may hide ("if you don't see it in your menu, your role may not include it").
- Only the five customer roles in `frontend/apps/web`. The Zoiko staff **operator console** (a separate app, separate login, much more sensitive capabilities) is **not covered**.
- Custom roles (an Owner's own named variations) are not modelled; the guide reports the five standard roles.

## Evidence

| What | Result |
|---|---|
| Guide engine and adapter | 39 tests, typecheck clean (`frontend/packages/console-guide`) |
| Widget (with the new `copy` prop) | 29 tests |
| Web app | 401 tests (includes the guide's route and navigation-parity tests), typecheck clean, lint clean on the new files |
| Backend | fixture-drift test (2) |
| Real browser, signed in with a real passkey | the guide opens, answers from the live matrix, and only `GET /api/bff/v1/roles/matrix` and `GET /api/bff/v1/tenant/me/permissions` leave the browser. All five roles and a two-role user were checked against the real matrix by varying only the "my roles" response |

## Files

`frontend/packages/console-guide/` (engine, pages, in-browser API adapter, tests) · `frontend/apps/web/src/components/console-guide.tsx` (mounted in `console-shell.tsx`) · `frontend/apps/web/src/app/guide/out/[id]/route.ts` (fixed-map redirect) · `frontend/packages/assistant-widget` (optional `copy` prop) · `backend/app/tests/unit/test_console_guide_fixture.py`.
