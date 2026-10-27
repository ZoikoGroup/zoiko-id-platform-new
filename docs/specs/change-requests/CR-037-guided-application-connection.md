# CR-037: A new person can connect an application and watch shadow without reading the specification

- **Target:** ZID-OVERLAY-001 §12, §18, §25 (shadow, exit criteria, onboarding console), ZID-UX-001 · **Raised by:** Console wiring (onboarding page) · **Status:** Open (built)

## Gap

Onboarding stopped at "shadow.observations: SHADOW_EXIT_CRITERIA_FAILED" and "PEP_CONTEXT_INCOMPLETE". A person new to the product could not tell what an enforcement point is, how to connect one, how many decisions were still needed or what to fix.

## Change

`GET /v1/onboarding/connection` (OWNER or ADMIN) returns where an application sends its access questions (`evaluation_url`, `token_url`), the company's stage, and, in SHADOW and PILOT, the plain numbers behind the shadow gates: decisions seen against the plan's minimum, the share with all the details (and for the highest-risk class), agreement with the current system, false allows, and `ready` when every shadow gate passes. It carries no secret.

The onboarding page, in plain words:

- **Step 5, Applications:** a four-step guide (tell us about your application, give it a key, send us each access question, watch it work) with a check mark per step, the application's own key shown, and a copy-ready example in curl, Node.js and Python already filled in with the company's addresses.
- **Step 7, Shadow:** three progress bars (questions seen, questions with all the details, agreement with your current system) and one sentence on what to do next: nothing has arrived, details are missing, or the systems disagree. "Ready for the pilot" when the gates pass.
- **Step 8, Pilot:** "Not ready for the pilot yet" with the progress, before the person clicks.
- The two error codes a new person would meet, `SHADOW_EXIT_CRITERIA_FAILED` and `PEP_CONTEXT_INCOMPLETE`, now have plain-words help.

## Rules

- The cell still decides: the page shows readiness, it never opens the pilot itself.
- Nothing about how an application authenticates is invented here: the example states the token step and says to prove identity with the registered key pair.

Tests: the connection read in the shadow lifecycle test (addresses, stage, observation count, `ready`).
