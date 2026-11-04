# Device and accessibility test pack (one half-day)

Closes, once real results are attached: CL03-T18, CL04-C18, WF09-OP-12, UX-18 (keyboard), UX-21 (200% zoom), UX-22 (responsive approvals), UX-30 (performance), and the accessible-onboarding approval of each country's go/no-go gate (ZID-EDR-001 item 5). The automated checks
(axe in a simulated browser, Playwright at four viewports) are already linked as supporting evidence; **they are not a screen-reader run and they are not these results.** Nothing here is marked verified until a named tester fills the results table below and the file is attached with `tools/scripts/record_evidence.py`.

## Setup
- Stack: the staging environment, or local (`launch_stack.py cell`, `npm run dev:web`, `npm run dev:operator`), on a build with the commit under test noted in the results.
- Testers: one screen-reader user for each of NVDA + Firefox or Chrome (Windows), VoiceOver + Safari (macOS) and VoiceOver + Safari (iPhone), TalkBack + Chrome (Android) where the market needs it. One person for keyboard-only and zoom.
- Devices: a 390 px phone, an 834 px tablet, a 1440 px desktop; a throttled connection (Fast 3G) for UX-30.
- Accounts: a fresh sign-up (organization journey), an Owner, a Member, a Billing Viewer, and an operator with the finance appointments.

## Scripts (run each at every device and with the assistive technology named)
| # | Task | Pass when |
|---|---|---|
| A1 | Sign up: choose a journey and country, enter an email, read the confirmation | Every field has a spoken name; the country list is searchable; the generic message is announced; no focus is lost on error |
| A2 | Open the emailed link, create a passkey | The step is announced; failure and cancel are announced with a way back |
| A3 | Company verification: identity step, search the register, pick a result, declare authority, read the status | Each check is a named region with its state in words; no state is conveyed by colour alone; the status updates are announced politely |
| A4 | Billing: read the overview, open an invoice, add a payment method, give billing access to a person | Tables have captions and headers; the step-up dialog is a named modal, focus stays inside, Escape cancels |
| A5 | Approvals: open the inbox, open a request, vote with step-up | The exact change is read before the vote button; counts are announced after filtering |
| A6 | Operator: raise a credit, a refund, read a dispute, open the reconciliation board and sign a close | Every control is reachable by keyboard; the deadline and its time zone are spoken; a blocked close names why |
| K1 | The same tasks, keyboard only, no pointer | Every action reachable; visible focus; no keyboard trap; logical order |
| Z1 | The same pages at 200% and 400% zoom and 320 px | No loss of content or function; no horizontal scrolling except tables with their own scroll region |
| R1 | Approvals on the 390 px phone | The inbox, the request and the vote are usable one-handed with targets of at least 24 CSS px (44 where practical) |
| P1 | Overview, billing, the approvals inbox on Fast 3G | Largest contentful paint at or under the target the owners set; no layout shift when the role arrives |

## Results (fill in; do not edit the scripts)
| Script | Device and assistive technology | Build | Result | Defects (ticket) | Tester | Date |
|---|---|---|---|---|---|---|
| | | | | | | |

A defect that blocks a task is a fail. The row closes only when every script passes on every device in scope, or the owners record an accepted exception with a decision record.
