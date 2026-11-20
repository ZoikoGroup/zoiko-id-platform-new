# CR-132: a matching certificate reading completes the company check

Source: owner decision, 20 Nov 2026. Supersedes the "evidence only" rule of CR-131. Builds on CR-131 (certificate reading) and CR-109 (company verification grades). Status: built 20 Nov 2026. Not run on real certificates.

## The decision

The owner wants the "Company and legal status" check of a company claim completed from the uploaded certificate, with no manual review. The risk was stated and accepted: OCR proves only that the words on a picture agree with the claim, so a forged or edited certificate with the right name and number completes the check.

## What was built

- `org.kyb_document_reading` (migration organization_0014, append-only, row-level security): one row per stored certificate that was read, with the verdict, the per-field results and the incorporation date. Never the text.
- `POST /v1/org-claims/{id}/proofs` keeps the reading of a stored CERTIFICATE_OF_INCORPORATION or REGISTER_EXTRACT image. The check-only route (`certificate:check`) still stores nothing and completes nothing.
- `claims.status()`: the company check is COMPLETED when a CONSISTENT reading exists and the source has not said anything against the claim (ENTITY_INACTIVE, IDENTIFIER_MISMATCH, SOURCES_CONFLICT, LEI_CONFLICT are never overridden, and a closed claim is never reopened). The status carries `company_basis` (`SOURCE` or `DOCUMENT_OCR`) so the basis is always visible.
- Establishing the organization with no register record uses `establish_document_root`: an INSTITUTIONAL_KYB root at AR2 for entity existence only, named by the applicant's legal name. The owner's tenant-admin grant is issued at risk level 2, so that owner cannot act at risk 3 until a higher root exists.
- The company-claim screen says the certificate is read automatically and completes the check when it matches.

## Not changed

- The K0 to K4 grade rules (`kyb_grade.py`): nothing here raises a grade.
- The other three checks (identity, authority, risk) still have to complete for a claim to read VERIFIED.

## Not built

- PDF reading, certificates from other countries, any authenticity check of the certificate (see CR-131).
- A way for a reviewer to see the reading beside the document.

Tests: `tests/component/test_org_claim_proofs_pg.py`, `tests/component/test_org_claim_establish_pg.py`.
