# CR-118: case-content rules, EDD information requests, article retraction, and the release-gates screen

Source: Wireframe 06 (ZID-DTV-WF-06) sections 10, 11, 22; QA-023, QA-025, QA-026. Status: engineering built 18 Nov 2026.

## What was built

- **Candidate detail rules** (`screening/domain/candidate_detail.py`, pure): a former PEP is shown as former with the office, end date and relationship, and is never lowered in risk automatically (a lookback is a per-jurisdiction policy and changes only the note; every view still needs a person). A relative or close associate needs the named PEP and the relationship. A media article is linked to a subject only when two independent attributes agree and none contradicts; a name alone is `NAME_ONLY`, a contradicting birth date or country is `CONTRADICTED`.
- **Retraction and correction** (`screening/api/provider_events.py`, `reopen_for_delta(code=...)`): a signed provider event of kind `ARTICLE_RETRACTED`, `ARTICLE_CORRECTED` or `ENTRY_REMOVED`, naming the provider's references and a check class, sends each case that relied on them back to a person as a system revision with its own rationale. A retraction never clears the subject and a correction never escalates one. An unknown kind, class or missing references changes nothing; a replay is recognised.
- **EDD information requests** (`screening/domain/edd_request.py`, pure): catalogue items and a 3 to 60 day window; a partial answer stays open and lists what is missing; someone other than the requester reviews; expiry clears nothing; at most two reminders; customer wording never mentions screening, sanctions, PEPs or media.
- **Release-gates operator screen** (`operator/src/features/screening/gates.tsx`, route `/screening/gates`): the four gates and who still has to sign, sign a gate in the capability's one role, open a critical defect, place a hold, close a defect, release a hold. The screen signs for nobody; the backend refuses anything the capability does not allow.
- Tests: `test_wf06_candidate_detail.py`, `test_wf06_edd_request.py` (unit); `test_screening_article_update_pg.py` (component, with the provider-events and follow-on tests); `gate-logic.test.ts` (screen logic; the operator suite passes).

## Not built

- EDD requests have no storage, routes or screen yet: the state machine and wording are tested but nothing creates or reads one.
- The candidate detail and article-link rules are not yet called when a licensed result is stored, so a hit is not yet flagged unlinked or shown with a former-PEP note in the case view.
- Only the reopen is wired for corrections: the corrected article's new text is not compared with the old.
- The gates screen has not been opened in a browser, and nobody has signed a gate.
