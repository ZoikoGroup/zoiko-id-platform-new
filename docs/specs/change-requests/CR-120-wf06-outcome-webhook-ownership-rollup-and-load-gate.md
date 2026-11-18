# CR-120: the screening outcome webhook, the ownership roll-up, and the screening load gate

Source: Wireframe 06 (ZID-DTV-WF-06) sections 12 and ENG-19; QA-029 to QA-033. Status: engineering built 18 Nov 2026.

## What was built

- **Outcome webhook** (`screening.case.outcome_changed`, `com.zoiko.id.screening.case.outcome_changed.v1`): a thin notice emitted when a case opens, is decided or is reopened. It carries only the case id, market, outcome (`IN_REVIEW` or `COMPLETE`) and revision, never a name, list, candidate, disposition or reason; the customer reads the case under its own access for the calibrated status. It is in the subscribable list and declared in `contracts/asyncapi/proposed-events.yaml`; delivery uses the existing signed, versioned, tenant-scoped webhook sender. Tests: `test_screening_webhook.py`, `test_screening_webhook_pg.py` (one event per change, in the case's own tenant, no secret in the payload).
- **Ownership roll-up** (`screening/domain/ownership_rollup.py`, `application/ownership_screening.py`): a company is only as screened as its owners. The ownership analysis (WF02) is passed in as plain owners, so screening never imports it. No owner identified, an owner with no case, one whose identity is not evidenced, or a stale or unresolved screen is `INCOMPLETE`; an owner waiting for a person is `IN_REVIEW`; an owner whose case closed restricted makes the company `RESTRICTED` and is `designated` for the ownership rules (a possible match is never a designation); only every owner current and clear is `COMPLETE_CLEAR`. `needs_screening` lists the owners a new case should be opened for. Tests: `test_wf06_ownership_rollup.py`, `test_screening_ownership_rollup_pg.py`.
- **Screening load gate** (`tests/load/screening-gate/`, `slo.yaml` `screening_gate`): a k6 harness for eligibility reads and case opens with percentile thresholds, an error budget, and a failure if any response leaks a list name, candidate or score. Bound to the SLO file by `test_load_gate.py`. **The numbers are engineering proposals** (no requirement sets them, so the section carries no `requirement:` key) and **nothing has been run**: `RESULTS.md` says so. The chaos README now lists which existing tests cover screening degradation and what still needs a deployed cell.

## Not built or not proved

- Nothing calls the roll-up from a route or a screen, and nothing opens the owners' cases automatically: `owners_needing_a_case` says who needs one, the caller (not built) opens them.
- The webhook has been proved to be emitted, not delivered to a real endpoint in this work (delivery is the existing sender's).
- The load gate has not been run anywhere; the chaos cases that need a deployed cell (a killed worker, a regional partition, a real provider outage) are listed, not built.
