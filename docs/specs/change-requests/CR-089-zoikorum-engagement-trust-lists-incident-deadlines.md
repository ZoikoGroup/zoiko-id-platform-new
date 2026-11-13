# CR-089: Zoikorum engagement decision, WF-05 trust lists and incident notification deadlines

Status: open (built and tested; the rules and packs they read are owner content).

## Why
ZID-RCG-003 section 10.1 (the Zoikorum professional-engagement sequence), WF-05 (qualified signature and seal validation with legal interpretation
kept apart) and acceptance case T45 (incident notification deadline simulation) were the remaining buildable gaps.

## What changes
- `POST /v2/verifications/{case_id}/engagement-decision` (credential, published in credentials.json): the five steps of 10.1 for one case:
  the engagement context, each check's own result, the approved market and profession pack (`governing_pack` with subdivision and profession),
  `QUALIFICATION_*` and `PRACTICE_ELIGIBILITY_*` kept apart (never a composite VERIFIED), an audience-bound revocable answer with expiry and
  the positive evidence it rests on, and a refresh time. Acceptance stays with the marketplace. Rules in `credential/domain/engagement.py`;
  new reason codes have en/es/fr wording.
- `GET /v1/regulatory/trust-lists` (operator) and the console page Regulatory > Trust lists: which trusted lists are authenticated, their
  qualified CA services, anchors, TSA roots and CRLs, and whether qualified status can be assessed at all. Read through the evidence ports.
- `operations/domain/notifications.py`: incident notification obligations from Legal's rules (`config/regulatory/incident-notification-rules.yaml`,
  shipped empty): routing, the clock from awareness, escalation, MET only with evidence and inside the window. T45 is COVERED.

## Not in this change
- The notification rules themselves, and wiring the obligations into the incident screens.
