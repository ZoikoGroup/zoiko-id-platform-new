# CR-131: reading an uploaded company certificate, and what the reading is worth

Source: owner request, 19 Nov 2026: upload the company's legal certificates, extract them with OCR, and complete the verification step from them. Builds on the company proof upload (WF-13B section 06), the platform's imaging service (OCR) and CR-109 (company verification grades). Status: built 19 Nov 2026. Not run on real certificates.

> Superseded in part by CR-132 (20 Nov 2026): a stored certificate whose reading is CONSISTENT now completes the company check.

## The product decision

An uploaded certificate is **evidence the applicant supplies**. Anyone can make or alter one, and OCR proves only that the words on the picture agree with the claim. So the reading is built to do two useful things and one thing it must not do:

- **Catch mistakes at once.** The applicant learns immediately that the number or name on the certificate differs from what they typed, or that it is another company's certificate, instead of a reviewer finding out days later.
- **Help the reviewer.** The upload answer says whether the name, number and incorporation date read from the picture agree with the claim. The document itself is still stored for the reviewer, unchanged.
- **It does not verify the company.** Nothing about a company's status or grade changes because a certificate was read (tested). The company is verified by its register, or, where an owner has acknowledged it for a country (CR-130 era routing, `lei_as_primary`), by its LEI record. This follows the standing rule in `proof-policy.yaml`: "A proof is evidence for a reviewer. It never verifies a company by itself", and the incorporation-certificate rule in `company_documents.py`: a certificate proves a past event, not current status.

## What was built

- `domain/certificate_read.py`: reads company name, company number and incorporation date from OCR text of a UK-style certificate of incorporation or register extract, and compares the name and number with the claim. Answers: CONSISTENT (name and number agree), CONTRADICTS (a number or name clearly there differs; a lost leading zero counts as different), NOT_ENOUGH (too little read, or a field not found: held against nobody). The answer carries codes and the date only, never the text.
- `POST /v1/org-claims/{id}/proofs`: for kinds CERTIFICATE_OF_INCORPORATION and REGISTER_EXTRACT, the upload answer now has a `read` object. A JPEG or PNG is read by the imaging service; a PDF is stored as before and answered "upload a photo or scan" (the reader takes pictures only). If the reader is down the upload still stands.
- The company-claim screen shows the answer: matches, does not match (which field), or could not be checked.

## Not built

- PDF reading (no PDF text or rendering library is installed; a certificate saved as a PDF must be photographed or scanned).
- Certificates from other countries (the patterns are the UK's; other layouts return NOT_ENOUGH).
- Any check that the certificate is genuine (a forged one with the right words reads CONSISTENT). Authenticity for signed extracts and QR codes exists separately (`extract_check`) and is not wired to this reading.
- Storing the reading: it is shown on the upload and not kept, so a reviewer sees the document and not the applicant's result.

Tests: `tests/unit/test_certificate_read.py`, `tests/component/test_org_claim_proofs_pg.py`.
