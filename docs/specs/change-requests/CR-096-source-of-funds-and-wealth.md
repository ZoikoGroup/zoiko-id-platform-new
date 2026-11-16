# CR-096: Source of funds and wealth cases (ZID-GAP-001 v2.0 DTV-28; CR-073 P2-6)

Status: **engineering path built; the product line stays DESIGN, so nothing here runs for a customer.** The questionnaire shipped is a SAMPLE written by engineering. The questions, the evidence they ask for, their legal basis and the retention period are the compliance officer's and the data protection officer's to write and approve; nothing in this change pretends otherwise.

## Why

DTV-28 is the one regulated line in ZID-GAP-001 v2.0 that needs no licensed partner: it is questionnaires, evidence and review. CR-073 section D and P2-6 designed it and said the machinery could be built before the content exists. This change builds the machinery.

## What was built

| Part | Where | What it does |
|---|---|---|
| Rules | `verification/domain/source_of_funds.py` | A questionnaire is data identified by a content hash. Answers are typed (choice, amount as a decimal string with a currency, date, yes or no, text). The system can only say a submission is INCOMPLETE (naming each gap) or complete and waiting for a person. A reviewer is never the submitter; an adverse first decision is never final; an appeal goes to a third person. A clearance lapses after 365 days. There is no score. |
| Questionnaire | `config/questionnaires/source_of_funds/0.1.0-SAMPLE.yaml`, `verification/infrastructure/questionnaire_registry.py` | One file per version, never edited after use. The newest APPROVED version is used; a SAMPLE one only when nothing is approved. APPROVED will not load without a named approver and a legal basis reference. |
| Storage | migration `verification_0015`: `verify.sof_case`, `verify.sof_decision` | Row-level security on both. The decision table is append-only and holds reason codes from a fixed list and no free text, so it carries no personal data. The case holds answers and references to uploads, never the uploads. |
| Service and routes | `verification/application/sof_service.py`, `verification/api/v2_source_of_funds.py` | Seven routes under `/v2/source-of-funds`. Every route that starts or changes a case passes the product line gate first. Evidence references must name an upload that is in the company, ready, scanned clean and not erased. |
| Privacy | `verification/infrastructure/privacy_adapter.py` | The case is on the data map. Access requests export what was asked, answered and decided. Governed erasure empties the answers and evidence references and keeps the state and the decision record. |
| Actions | `verification.sof.submit`, `verification.sof.read`, `verification.sof.review` (R2, E2) | The queue shows no answers. Reading answers is its own permission, because source of funds is sensitive financial information. |

## The rules (each has a test)

1. The system never clears anyone. CLEARED and NOT_CLEARED are only ever a person's decision.
2. The person who submitted a case cannot decide it.
3. A first adverse decision is NOT_CLEARED_PENDING_APPEAL. Only the person concerned (or whoever submitted for them) may appeal. An appeal is decided by someone who neither submitted nor decided first. Only then can an adverse outcome be final.
4. Every decision needs reason codes from a fixed list. No free text is stored.
5. A case records the content hash of the questionnaire it was answered against. A changed questionnaire means a new case.
6. A SAMPLE questionnaire is refused on a LIVE line. A line that is DESIGN refuses every route that starts or changes a case.
7. A clearance counts only until `valid_until`.

## What it does not do

- It does not decide what a good answer or good evidence is. That is the reviewer's judgement, and the questionnaire's content is the compliance officer's.
- It does not read the documents. An upload is a reference with a type; reading it is a person's work (OCR can help a reviewer but never decides).
- It stores no score, no risk rating and no free text about a person.
- It does not switch the line on. The shipped `product-lines.yaml` keeps `source_of_funds` at DESIGN.

## Owner decisions still needed

1. The compliance officer writes and approves the real questionnaire per market, with a legal basis reference, and records it as a new APPROVED version.
2. The data protection officer sets the retention period (`SOURCE_OF_FUNDS_CASE`), adds source of funds to the DPIA, and approves the purpose code.
3. The owners choose the countries and switch the line to STAGING, then LIVE, in `product-lines.yaml` (the file does not load without a legal basis, and LIVE needs the approver's name).
4. Whether a clearance of 365 days is right for each market.

## Evidence

`backend/app/tests/unit/test_source_of_funds.py`, `backend/app/tests/component/test_source_of_funds_pg.py`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the four questions above |
|---|---|---|---|
| Compliance officer | | | |
| Data protection officer | | | |
