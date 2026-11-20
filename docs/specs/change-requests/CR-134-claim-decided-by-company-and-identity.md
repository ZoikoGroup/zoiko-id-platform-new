# CR-134: a company claim is decided by the company check and the applicant's identity

Source: owner decision, 20 Nov 2026. Supersedes the four-check rule of WF-13B-03 for the claim status. Builds on CR-132. Status: built 20 Nov 2026.

## The decision

A company claim is decided by two checks only: "Company and legal status" and "Your identity". The authority to act for the company and the risk and ownership review are no longer part of it, and no Zoiko iD operator approves anything on this path. The claim reads VERIFIED when both are COMPLETED.

## What was built

- `claims.status()` returns two checks (`company`, `person_assurance`). Any screening already declared is still returned as information and decides nothing. The claim events for authority and risk are no longer announced.
- `POST /v1/org-claims/{id}/submit` no longer needs a screening declaration (the `CLAIM_INCOMPLETE` refusal is gone).
- Company proof uploads and approval attachments no longer refuse a company that has no residency profile: its documents stay in its own home cell, where the request already is. A company with a profile is still checked against it.
- The web claim screen shows two checks, has no "add your authority and owners" step, and `nextStep` is `submit`, `wait` or `done`.

## Risk accepted

Anyone with a verified identity and a certificate image that matches the claim becomes the company's OWNER once the organization is established. Certificates are public documents, so this is a company-takeover risk that the authority check used to cover. The organization is rooted at the lower-assurance document root (CR-132) when no register record exists.

## Not changed

The screening and mandate code and screens remain but are not reached from a claim. The K0 to K4 grades are unchanged.

Tests: `tests/component/test_org_claims_lifecycle_pg.py`, `test_org_claims_trust_pg.py`, `test_org_claim_proofs_pg.py`, `frontend/apps/web/src/features/onboarding/org-claim.test.ts`.
