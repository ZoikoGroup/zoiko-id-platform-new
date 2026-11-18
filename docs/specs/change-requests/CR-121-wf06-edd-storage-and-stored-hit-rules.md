# CR-121: EDD information requests stored, and the PEP and article rules applied to stored hits

Source: Wireframe 06 (ZID-DTV-WF-06) sections 10 and 11; QA-023, QA-025. Status: engineering built 18 Nov 2026.

## What was built

- **EDD requests stored** (migration `screening_0013`, `application/edd.py`, worker step `screening_edd`): `verify.screening_edd_request` under tenant row-level security, with a reviewer read policy. One open request per case (a unique index), the state machine from `domain/edd_request.py`, someone other than the requester reviews (also a table check), and the worker records reminders and expires what ran out. Expiry clears nothing: the case row is untouched. Another tenant's request answers the same as one that never existed.
- **Stored hits carry the rules** (`licensed.hit_row`): a PEP hit stores what its dates say (`PEP_FORMER`, `PEP_NOTE_FORMER_RISK_NOT_REDUCED`) and keeps its strength; a media hit stores whether the article really links to the subject (`ARTICLE_LINKED`, `ARTICLE_NAME_ONLY`, `ARTICLE_CONTRADICTED`) and is never stored as STRONG unless two independent attributes agree. The reasons are visible to reviewers only.
- Tests: `test_screening_edd_pg.py`, `test_wf06_hit_row.py`.

## Not built

- No route or screen creates, answers or reviews an EDD request: a tenant reply route, an operator route and a console page are still to build, and reminders are recorded for a notifier that does not exist yet.
- The reviewer's case view does not yet show the new reason codes in words.
