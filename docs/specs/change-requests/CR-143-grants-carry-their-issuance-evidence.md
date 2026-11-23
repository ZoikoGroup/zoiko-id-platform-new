# CR-143: a grant carries the audit record of its own issuance

Source: found while preparing a real-data test of the access readiness agent (ZID-AG-WF-001), 20 Nov 2026. Builds on CR-091 (the access readiness agent, rule R-05) and the authority grants (ZID-MANDATE-001). Status: built 20 Nov 2026.

## The gap

Rule R-05 asks whether the policy evidence behind a mandate can be found, and reads it from the mandate's source grant (`authz.authority_grant.evidence_ref`). No production code ever wrote that column (the tests set it by hand), so on a real company every mandate would have read as "evidence reference missing" and the agent would have proposed a review for each of them.

## What changed

- `POST /v1/authority-grants` already wrote an append-only audit record of the issuance (`authority.grant.issued`). It now points the grant at that record's id (`set_evidence_ref`, once, never replacing one that is there). The evidence of a grant is the record of who gave whom what, and when.
- Migration `authority_0005` points the grants already issued through the API at the audit record written when they were. It skips on a fresh database, where the evidence tables do not exist yet and there are no grants.

## Where this stops

- A grant that was never audited has no such record and stays without a reference, so R-05 still reports it, truthfully: the grants onboarding creates for a new company's owner and administrators (`ROLE_ISSUED` by the system) are not audited today. A company that wants mandates the agent reads as evidenced issues the grant it builds them on through Authority and approvals, Issue grant.
- The delegation-template apply route issues grants without an audit record; it is not changed here.
- This does not make a reference mean more than it says: it proves a record of the issuance exists, not that the issuance was right.

Test: `test_access_readiness_api_proof_pg.py::test_a_grant_issued_over_the_api_carries_the_audit_record_of_its_issuance` (a grant issued over HTTP carries exactly the audit record of that issuance).
