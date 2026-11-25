# Verify API: case review (WF04) and the fraud graph (WF05)

Written 25 Nov 2026 for the Zoiko iD product owner. Records what CR-167 (case review and four-eyes control, ZID-CP-WF04 v2.0) and CR-168 (the fraud graph, ZID-CP-WF05 v2.0) built, what was decided on the way, and what is deliberately not built. Nothing here is a claim of production capability: both are built and tested against synthetic data in a test database, not run with real partners.

## CR-167 Case review and four-eyes control

**Built.** A session waiting for review becomes a case in a queue. One reviewer claims it for 15 minutes and must name the case version they read; a stale screen changes nothing. The reviewer records each required check and proposes a result. A different person gives the second review, ticking a three-point checklist; only then does one transaction write the new decision, the event and the webhook outbox entry. A regulatory hold (placed and released by two different operators) stops everything. Sensitive evidence is hidden until a reviewer gives a reason, then shown for five minutes and counted. A final case is never edited: a correction is an appeal, reviewed by people who took no part. Every step is in a hash-chained trail that the database refuses to edit. An AI adviser exists as validation rules only: it is off by default, has no model installed, and can only advise.

**Console.** Review queue with state and queue filters and a "cases I hold" toggle; a case dialog with evidence, checks, ask-the-person templates, escalate, propose, second review checklist, reveal countdown, assistant feedback and history; an operator tab for holds.

**Decisions.** The proposal must name the evidence hash the case reports; the case now returns it. Reveal, claim and lease times are constants in `domain/review.py`.

**Not built.** A real model for the adviser. Rendered-screen tests (only logic tests exist).

## CR-168 The fraud graph

**Position.** A connected graph is a hypothesis, never proof. The graph is a projection over the company's own Verify API sessions, per company, rebuilt from them. Nothing is shared between companies, nothing is biometric, and nothing here changes a session, a decision or a webhook.

**What a link is made of today.** Only the company's own customer reference, as a token made from the reference hash that the disputes feature already keeps (so an erasure request can find and remove it). Verify API sessions carry no device, IP or document signal, so there are no other link types in use. Those types are in the registry as disabled and unqualified, and a query that names one cannot be published. A reference the company chose, repeated, is normal after a failed capture: every link says so, and the lead lists it among its uncertain facts.

**Built (the first eight work packages, FG-01 to FG-08).**
- Observations are idempotent on their source event, carry an expiry, and never hold the reference. A source that is not enabled is kept as quarantined with its reason and no token.
- Typed links with a strength (strong, corroborating, weak, similarity), a method, a rule version and a version number. A person can exclude or restore a link with a reason; a refresh never undoes an exclusion; the history stays.
- Leads from deterministic rules (repeat after refusal; one reference across applications) with their uncertain facts. A lead carries no finding.
- A bounded, masked read around one session (at most 200 sessions, three steps), with `has_more` and `source_gap`, and an audit event naming who looked.
- Hand-off to the existing review queue: a lead can go to the case of one of its sessions that is waiting for review, adds itself as evidence, and a retry gives the same case. If no session of the lead is waiting for review it cannot be handed over (`LEAD_NOT_ELIGIBLE`).
- A finding (confirmed, no finding, inconclusive) needs an escalated lead whose review case is final, recorded by someone who took no part in the case.
- An action proposal needs a confirmed finding, at most 25 exact targets, each held by a link that can support a step (never a weak-only one), a policy basis, an impact reference and an expiry of at most 30 days. A different person approves by naming the manifest checksum they looked at. Reordering or changing a target is a new manifest. **Nothing is executed:** an approved proposal is a signed instruction for the company's own people and systems.
- Biometric 1:N, face list and cross-company features are deny-only (`SENSITIVE_FEATURE_DISABLED`); no setting turns them on.
- Erasure: resolving an erasure request also removes the graph's token, retracts the links, expires open leads and takes the reference off the blocklist.

**Built (the governed extras).**
- **Query builder (.03).** A query is typed fields only; there is no free text and no search by a person's details. A weak or similarity link may only appear together with a stronger one and an explicit `require_corroboration`. A replay shows how many leads it would open and the review workload and changes nothing. A different person publishes; publishing retires the earlier version. A published query opens its own leads on the next refresh, named by its version.
- **Timeline and as-of (.06).** One chronology merging the graph's trail and the review case's trail, whether the lead's exact snapshot can still be rebuilt, and a picture "as of" an earlier time from what was recorded then. Erased data is never rebuilt, and the picture says so.
- **Blocklist (.08).** The company's own list, matched by exact reference only, advice only. An entry needs a confirmed finding behind it and a second person; it expires by reason (180 or 365 days) and comes up for review every 60. A wrongly listed person's challenge (recorded by staff) takes the entry out of matching until someone who took no part decides; removal keeps the record.
- **Data quality (.10).** Freshness per source, coverage, quarantine reasons and retention. A quiet source while sessions keep arriving is reported as missing data, never as less risk.
- **Masked export (.02).** A CSV or SVG of the same bounded picture the screen shows: no customer reference, only this company's sessions, a stated purpose and reason, a do-not-keep-after time, cells a spreadsheet will not run as formulas, never cached, and at most ten exports a day per person. Every export is on the record.
- **Lead times (.05).** A lead has a time by risk (24 hours, 72 hours, 7 days) from the moment it was opened. A lead handed to the review queue shows as paused (the case's own clock applies), a lead whose data feed is missing shows as unknown, and the overview counts the queue by state.
- **One source for every number (.01, .11).** The overview, the analytics and the metric list add the same rows through one function, with definitions published beside the values.
- **Query limits (.03).** A replay is stopped by a time budget, a link-read budget and a database statement timeout; a stopped replay records nothing and cannot affect another company.
- **Honest numbers (.11).** Precision only among adjudicated leads, with the denominator; flagged unreliable under 30 labels; groups under five are not shown; no loss-prevented figure is produced.

## Not built, and why

| Item | Reason |
|---|---|
| Device, IP, document-token and image-similarity links | The sessions do not collect those signals. Adding capture to the verification page is a privacy-reviewed change that was not chosen. |
| Biometric 1:N, face list, cross-company sharing | The spec requires separate legal, privacy and accuracy approvals first. They are off and cannot be enabled from this build. |
| Executing an action on a session | Chosen: proposal and approval only. |
| Precision, recall and fairness claims | Need independently adjudicated labelled cases. None exist, so the numbers show how few there are. |
| Reconciliation with a Command Center (WF05-01-A05, WF05-11-A05, WF05-05-A05) | No Command Center metric source exists in this build. What is built: one function adds up every lead number, the overview, the analytics and the metric list all read it, and the definitions are published at `/v1/verify-api/fraud/metrics`. A Command Center should read that list instead of counting again. |
| Operator-triggered cancellation of a running query (WF05-03-A05) | A replay is stopped by a time budget, a link-read budget and a database statement timeout, and records nothing when stopped. There is no button to cancel one in flight. |
| Expiry of an exported file | The file carries a do-not-keep-after time and every export is recorded, but a file cannot enforce its own deletion. |
| Least-privilege denial test, anomaly alerts (WF05-12-A01, A05; WF05-05-A04) | Duties exist but a missing-grant denial and access-anomaly alerting are not tested or built. |
| Cross-company governance steps (WF05-09-A03 to A05) | The feature is OFF; its shutdown and consent steps have nothing to run. |
| Console screens for WF05 | Not built. The API is complete for them. |
| Graph refresh on session events | Refresh runs when someone asks. |

## Where the evidence is

- Rules: `backend/app/tests/unit/test_verify_api_fraud_model.py`
- Routes and database: `backend/app/tests/component/test_verify_api_fraud_pg.py` and `test_verify_api_fraud_governance_pg.py`
- The QA register mapping (FG-T01-01 to FG-T12-05) is in `docs/traceability/acceptance-tests.csv`, with an honest status per row: IN_PROGRESS where a test exists, NOT_STARTED where nothing does.
- Routes: `/v1/verify-api/fraud/...` and `/v1/verify-api/review-cases/...`; actions `verify_api.fraud.read|analyze|propose|approve` and `verify_api.review.read|work|approve`.

## Fraud console (25 Nov 2026)

The Developers page now has a Fraud signals section (`features/admin/developers/fraud.tsx`): overview with coverage that never reads missing data as safe, leads with triage, escalate, finding and notes, and action proposals with approve, reject and revoke (the maker is never offered approve). Proposing an action, the query builder, blocklist and graph picture have no screen yet; they remain API only. The cell still enforces the four duties.
