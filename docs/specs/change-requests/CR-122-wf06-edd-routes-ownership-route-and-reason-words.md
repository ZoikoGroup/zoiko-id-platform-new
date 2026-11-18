# CR-122: EDD routes and screens, the owners' roll-up route, and reviewer wording for PEP and article codes

Source: Wireframe 06 (ZID-DTV-WF-06) sections 10 and 12. Status: engineering built 18 Nov 2026.

## What was built

- **Operator routes** (`operator_access/api/screening_cases.py`): `GET` and `POST /v1/screening/review/cases/{id}/information-requests` (any case role asks; every ask is an audited protected act) and `POST /v1/screening/review/information-requests/{id}:review` (senior or Legal only, never the person who asked, an insufficient answer needs a code). Errors have fixed codes and statuses.
- **Tenant routes** (`screening/api/requests.py`): `GET` and `POST /v1/screening/cases/{id}/information` (what is wanted, in words that never say why; the customer's reply) and `POST /v1/screening/ownership/rollup` (a company's owners rolled up). The customer-safe roll-up reads a restricted owner exactly like one still in review and returns no designation list: saying otherwise would tell the company what a check found. `needs_case` lists the owners to open cases for through the ordinary case route.
- **Screens**: the operator case page has an "Information from the customer" card (ask for catalogue items with a 3 to 60 day window, see each request's state and reminders, accept an answer or mark it not enough with a code); the customer's screening page shows a "We need a little more information" panel with plain item labels.
- **Reviewer wording**: the PEP status and notes (`PEP_FORMER`, `PEP_NOTE_*`) and the article links (`ARTICLE_LINKED`, `ARTICLE_NAME_ONLY`, `ARTICLE_CONTRADICTED`, `ARTICLE_RETRACTED`, `ARTICLE_CORRECTED`) now read in words on the reviewer's case view.
- A defect found and fixed on the way: a refused duplicate request aborted the whole transaction, so the insert now runs in a savepoint.
- Tests: `test_screening_edd_api_pg.py` (both planes, with audit), `test_screening_edd_pg.py`, the roll-up unit tests, `case-logic.test.ts` and the web `logic.test.ts`; contracts regenerated.

## Not built

- **The customer's answer carries no content.** The reply route records which requested items the customer says they have sent; it does not capture the text or documents themselves, and nothing stores them. Until a capture and retention design exists, the content must travel by another channel the reviewer can cite.
- Reminders are recorded for a notifier; nothing sends them.
- The roll-up is called with owners the caller names; the platform does not read the WF02 ownership analysis itself, and nothing opens the owners' cases automatically (the caller does, from `needs_case`). No screen calls the roll-up yet.
- The screens have not been opened in a browser.
